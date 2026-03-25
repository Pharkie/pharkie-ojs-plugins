# CLAUDE.md

## Project

WordPress ↔ OJS integration. WP manages memberships via WooCommerce Subscriptions; OJS hosts a journal behind a paywall. Goal: members get access automatically, non-members can still buy content. See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the full architecture, plugin descriptions, and decision trail.

## Key docs

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — architecture, plugins, constraints, evaluated approaches
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — code conventions, pre-commit hooks, testing, "don't" list
- [`docs/setup-guide.md`](docs/setup-guide.md) — dev environment, secrets management (SOPS/age), devcontainer
- [`docs/ojs-sync-plugin-api.md`](docs/ojs-sync-plugin-api.md) — OJS plugin REST API reference
- [`docs/ojs-internals.md`](docs/ojs-internals.md) — OJS native API, DB schema, PHP internals
- [`docs/wp-integration.md`](docs/wp-integration.md) — WP membership stack, hooks, code patterns
- [`docs/discovery.md`](docs/discovery.md) — decision trail: what was tried, eliminated, and why
- [`docs/docker-setup.md`](docs/docker-setup.md) — Docker dev environment
- [`docs/non-docker-setup.md`](docs/non-docker-setup.md) — non-Docker plugin installation
- [`docs/vps-deployment.md`](docs/vps-deployment.md) — VPS deployment
- [`docs/support-runbook.md`](docs/support-runbook.md) — support staff quick reference
- [`TODO.md`](TODO.md) — roadmap

## Good to know

- **OJS has NO subscription REST API.** The endpoints don't exist. That's why a custom OJS plugin is needed. See `docs/ojs-sync-plugin-api.md`.
- **OJS plugin uses `getInstallMigration()`**, not `getInstallSchemaFile()` (which is `final` in OJS 3.5). See `WpojsApiLogMigration.php`.
- **OJS plugin folder must be `wpojsSubscriptionApi`** (camelCase). Hyphens/underscores break autoloading and the Plugins admin page. See `docs/non-docker-setup.md`.
- **Apache + PHP-FPM strips Authorization headers.** Need `CGIPassAuth on` in `.htaccess`. Do not use `?apiToken=` query param in production (leaks key into access logs).
- **OJS 3.5 stores galley labels in `publication_galleys.label` column**, not in `publication_galley_settings`. Inserting label rows into the settings table causes `getLabel()` to return a localized array instead of a string, breaking label comparisons (e.g. inline HTML plugin's `=== 'Full Text'` check).
- **htmlgen can drop repeated/multilingual content.** Haiku may treat transliterated references (e.g. Cyrillic then Latin script) as duplicates and omit one set. The prompt now explicitly says to include both, but always verify HTML galleys against source PDFs for articles with non-English references.
- **OJS 3.5 upgrade is the biggest risk.** The 3.5 upgrade has significant breaking changes (Slim→Laravel, Vue 2→3). If this goes badly, re-evaluate Janeway migration.
- **WP usernames are synced to OJS** but sanitized to lowercase-alphanumeric (OJS constraint). WP usernames commonly contain dots, hyphens, underscores, spaces, or `@` — these get stripped, so typing the WP login into OJS may not match. Mitigated: the login page relabels the field to "Email" and sets `autocomplete="email"`. OJS login auto-detects email-shaped input and does email lookup. See `docs/ojs-sync-plugin-api.md#username-sync`.

## Backfill pipeline

Imports journal back-issues (whole-issue PDFs) into OJS. Three steps:

1. **Create `toc.json`** — Claude reads the PDF and writes `backfill/private/output/<vol>.<iss>/toc.json` with article metadata. See `docs/backfill-toc-guide.md` for schema.
2. **`backfill/split-issue.sh <issue.pdf>`** — split the PDF into per-article PDFs + OJS Native XML. Requires toc.json to already exist.
3. **`backfill/import.sh <issue-dir>`** — load the split output into OJS via Docker CLI.

Pipeline scripts (called by `split-issue.sh`):
- `backfill/preflight.py` — validate PDF, detect vol/issue
- `backfill/split.py` — split PDF into per-article PDFs using PyMuPDF
- `backfill/author_normalize.py` — normalize author names
- `backfill/enrich.py` — enrich toc.json with subjects, disciplines, citations from spreadsheet data
- `backfill/generate_xml.py` — generate OJS Native XML with base64-embedded PDFs + HTML galleys
- `backfill/verify.py` — post-import verification against OJS database

JATS is the single source of truth for article content. The pipeline direction is **PDF → JATS → HTML**:

1. `backfill/htmlgen.py` — sends split PDFs to Claude API, generates initial HTML body content
2. `backfill/generate_jats.py` — generates JATS 1.3 XML per article from toc.json metadata + HTML body. Output: `backfill/private/output/<vol.iss>/<seq>-<slug>.jats.xml`
3. `backfill/extract_citations.py` — reads JATS `<body>`, finds reference sections, extracts items, writes to JATS `<back>` (ref-list, fn-group, bio, notes). Removes ref sections from body.
4. `backfill/split_citation_tiers.py` — reads JATS `<ref-list>`, classifies items as reference or note, moves notes to `<fn-group>`
5. `backfill/jats_to_html.py` — generates HTML galley from JATS (body + notes + bios + provenance; references excluded — OJS renders those from citations table)
6. `backfill/generate_xml.py` — generates OJS Native XML for import. Reads citations from JATS `<ref-list>`, page numbers from JATS `<fpage>`/`<lpage>`.

Shared classification logic: `backfill/lib/citations.py` (is_reference, is_note, is_author_bio, etc.)

toc.json retains issue-level data only: PDF page splits, article ordering, section assignments, metadata.

Page numbers: `backfill/add_page_numbers.py` auto-detects printed page numbers from source PDFs and writes `journal_page_start/end` to toc.json. `generate_jats.py` reads those and writes `<fpage>`/`<lpage>` to JATS. `generate_xml.py` reads page numbers from JATS only (not toc.json) — JATS is the single source of truth. To update page numbers: edit toc.json → re-run `generate_jats.py` → re-run `generate_xml.py`. To push to live without re-import: `backfill/push_page_numbers.py --target live`.

OJS IDs: JATS stores `<article-id pub-id-type="publisher-id">` (the OJS submission_id) so that article URLs and DOIs survive a teardown/rebuild. `generate_jats.py` reads these from `backfill/private/id-registry.json` (generated by `snapshot_ids.py`). After a `--clean` reimport, `restore_ids.py` remaps the new auto-assigned IDs back to the originals. JATS is the authoritative source; id-registry.json is the convenience cache.

Full rebuild (deploy extensive content changes to live, or disaster recovery — avoids surgical per-article fixes):
1. `backfill/import.sh backfill/private/output/* --clean` — reimport all issues
2. `python backfill/restore_ids.py --target live --confirm` — remap IDs to originals (URLs, DOIs, payments reconnect)
3. WP→OJS user sync — recreate users + subscriptions
4. Crossref "Deposit All" (OJS admin: Website > Plugins > Crossref) — re-confirms DOIs, safe no-op since DOIs and URLs are unchanged

Standalone utilities:
- `backfill/audit.py` — audit all source PDFs in `backfill/private/input/` for completeness
- `backfill/compare_archive.py` — compare PDF sources
- `backfill/export_review.py` — export toc.json entries to spreadsheet-compatible format
- `backfill/import_review.py` — import reviewed/corrected spreadsheet data back into toc.json
- `backfill/sheets_export.py` — publish all toc.json data to Google Sheet for review
- `backfill/add_page_numbers.py` — auto-detect printed page numbers from source PDFs, populate toc.json
- `backfill/push_page_numbers.py` — push page numbers to OJS database (dev or live) without re-import
- `backfill/snapshot_ids.py` — snapshot submission/issue IDs from OJS database for idempotent rebuild
- `backfill/restore_ids.py` — restore original IDs after a fresh import (preserves URLs, DOIs, payments)

All journal-specific data lives in the private repo (`private/backfill/`). The public repo has a single symlink: `backfill/private` → `private/backfill/`. Paths like `backfill/private/input/`, `backfill/private/output/`, `backfill/private/authors.json`, `backfill/private/doi-registry.json`, and `backfill/private/reports/` all resolve through this symlink. Regenerable files (split PDFs, import.xml) are gitignored in the private repo too. See `private/README.md` for full structure.

### Fixing a bad split or HTML galley

1. Fix `pdf_page_start`/`pdf_page_end` in `backfill/private/output/<vol>.<iss>/toc.json`
2. Re-split: `backfill/split-issue.sh backfill/private/input/<vol>.<iss>.pdf`
3. Delete the affected `.html` file(s): `rm backfill/private/output/<vol>.<iss>/<seq>-<slug>.html`
4. Re-generate HTML: `python3 backfill/htmlgen.py backfill/private/output/<vol>.<iss>/toc.json --yes`
5. Re-generate XML: `python3 backfill/generate_xml.py backfill/private/output/<vol>.<iss>/toc.json -o backfill/private/output/<vol>.<iss>/import.xml`
6. Re-import: `backfill/import.sh backfill/private/output/<vol>.<iss> --force`

### Fixing an HTML galley on live

**Never re-import on live** — it risks duplicates and ID changes. Instead, update the galley file in place:

1. Edit the `.html` file in `backfill/private/output/<vol>.<iss>/` and commit
2. Find the galley file path on live:
   ```
   docker compose exec -T ojs-db bash -c 'mysql -u root -p$MYSQL_ROOT_PASSWORD $MYSQL_DATABASE -e "
     SELECT f.path FROM files f
     JOIN submission_files sf ON f.file_id = sf.file_id
     JOIN publication_galleys g ON g.submission_file_id = sf.submission_file_id
     JOIN publications p ON g.publication_id = p.publication_id
     JOIN publication_settings ps ON p.publication_id = ps.publication_id AND ps.setting_name = \"title\"
     WHERE ps.setting_value LIKE \"%Article Title%\" AND f.mimetype = \"text/html\";
   "'
   ```
3. Build the full HTML file (repo files are body-only, live files need the DOCTYPE wrapper):
   ```
   { echo '<!DOCTYPE html>'; echo '<html lang="en">'; echo '<head><meta charset="utf-8"><title>Full Text</title></head>'; echo '<body>'; cat backfill/private/output/<vol>.<iss>/<seq>-<slug>.html; echo '</body>'; echo '</html>'; } > /tmp/galley-update.html
   ```
4. Copy into the live OJS container:
   ```
   scp /tmp/galley-update.html root@$SERVER_IP:/tmp/
   ssh root@$SERVER_IP "cd /opt/pharkie-ojs-plugins && docker compose cp /tmp/galley-update.html ojs:/var/www/files/<path-from-step-2>"
   ```
