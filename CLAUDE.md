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
- [`private/docs/monitoring.md`](private/docs/monitoring.md) — Better Stack monitors, heartbeats, GitHub Actions workflows, troubleshooting
- [`docs/vps-deployment.md`](docs/vps-deployment.md) — VPS deployment
- [`docs/support-runbook.md`](docs/support-runbook.md) — support staff quick reference
- [`docs/archive-checker-plugin.md`](docs/archive-checker-plugin.md) — Archive Checker plugin: visual review interface for backfill article splits
- `private/TODO.md` — roadmap (in private repo)

## Good to know

- **OJS has NO subscription REST API.** The endpoints don't exist. That's why a custom OJS plugin is needed. See `docs/ojs-sync-plugin-api.md`.
- **OJS plugin uses `getInstallMigration()`**, not `getInstallSchemaFile()` (which is `final` in OJS 3.5). See `WpojsApiLogMigration.php`.
- **OJS plugin folder must be `wpojsSubscriptionApi`** (camelCase). Hyphens/underscores break autoloading and the Plugins admin page. See `docs/non-docker-setup.md`.
- **Apache + PHP-FPM strips Authorization headers.** Need `CGIPassAuth on` in `.htaccess`. Do not use `?apiToken=` query param in production (leaks key into access logs).
- **OJS 3.5 stores galley labels in `publication_galleys.label` column**, not in `publication_galley_settings`. Inserting label rows into the settings table causes `getLabel()` to return a localized array instead of a string, breaking label comparisons (e.g. inline HTML plugin's `=== 'Full Text'` check).
- **Haiku extraction can drop repeated/multilingual content.** Haiku may treat transliterated references (e.g. Cyrillic then Latin script) as duplicates and omit one set. The prompt now explicitly says to include both, but always verify HTML galleys against source PDFs for articles with non-English references.
- **OJS 3.5 upgrade is the biggest risk.** The 3.5 upgrade has significant breaking changes (Slim→Laravel, Vue 2→3). If this goes badly, re-evaluate Janeway migration.
- **WP usernames are synced to OJS** but sanitized to lowercase-alphanumeric (OJS constraint). WP usernames commonly contain dots, hyphens, underscores, spaces, or `@` — these get stripped, so typing the WP login into OJS may not match. Mitigated: the login page relabels the field to "Email" and sets `autocomplete="email"`. OJS login auto-detects email-shaped input and does email lookup. See `docs/ojs-sync-plugin-api.md#username-sync`.

## Backfill pipeline

Imports journal back-issues (whole-issue PDFs) into OJS. See [`backfill/README.md`](backfill/README.md) for the pipeline overview, [`docs/backfill-reference.md`](docs/backfill-reference.md) for command reference, and [`docs/archive-checker-plugin.md`](docs/archive-checker-plugin.md) for the QA workflow.

Structure: `backfill/split_pipeline/` (PDF splitting, split1–split5), `backfill/html_pipeline/` (HTML/JATS/import, pipe1–pipe10), `backfill/lib/` (shared code), `backfill/validate_toc.py`.

### Gotchas

- **JATS is the single source of truth** for all per-article data (DOIs, publisher-IDs, page numbers, citations, body content). No registries. `pipe6_ojs_xml.py` reads everything from JATS.
- **toc.json `authors` field** is always a string (e.g. `"Emmy van Deurzen & Michael R. Montgomery"`). Do not convert to list — 8+ downstream scripts expect string.
- **`_manual_html` in toc.json** = hand-corrected HTML galleys. `pipe1_haiku_html.py` skips these automatically.
- **Haiku extraction can drop repeated/multilingual content.** Always verify HTML galleys against source PDFs for articles with non-English references.
- **Docker in devcontainer requires `sudo`** for `pipe7_import.sh` and `pipe8_restore.py` (they call `docker` directly). Other pipeline steps (pipe1–pipe6) don't need Docker. For `--target live`, do NOT use `sudo` — it breaks SSH config resolution. Only `--target dev` needs `sudo`.
- **`pipe3_generate_jats.py` wipes citations AND DOIs** — ALWAYS run full pipeline (pipe2→pipe6), never skip `pipe4_extract_citations.py`. After pipe3+pipe4, run pipe4b to re-attach DOIs from `doi_matches.json` cache (~2 min, no API calls). Only use `--revalidate` when you need to re-score against Crossref (~45 min).
- **Three HTML stages per article:** `.raw.html` (Haiku extraction), `.post.html` (post-processed), `.galley.html` (from JATS). No file collisions.

### QA iteration loop

1. Fix the post-processing pipeline (systemic fix, not per-article)
2. `python3 backfill/html_pipeline/pipe2_postprocess.py backfill/private/output/<vol.iss>/toc.json`
3. `python3 backfill/html_pipeline/pipe3_generate_jats.py backfill/private/output/<vol.iss>/toc.json`
4. `python3 backfill/html_pipeline/pipe4_extract_citations.py --extract --volume <vol.iss>`
4b. `python3 backfill/html_pipeline/pipe4b_match_dois.py --volume <vol.iss> --email EMAIL` (re-attaches DOIs from cache, ~2 sec/vol — **do NOT use --revalidate** unless you need fresh Crossref scoring)
5. `python3 backfill/html_pipeline/pipe5_galley_html.py backfill/private/output/<vol.iss>/toc.json`
6. `python3 backfill/html_pipeline/pipe6_ojs_xml.py <toc.json>` (writes import.xml next to toc.json)
7. `sudo bash backfill/html_pipeline/pipe7_import.sh backfill/private/output/<vol.iss> --force` (~7 sec)
8. `sudo python3 backfill/html_pipeline/pipe8_restore.py --target dev --issue <vol.iss>` (~0.6 sec)
9. QA in browser — repeat from step 1 if issues found

**Per-issue iteration takes ~8 seconds.** Reprocess only affected volumes, not all 1400 articles — approved articles should not be regressed. Full reimport (`--wipe-articles`, ~20 min) only when all volumes need updating.

### Post-import scripts (post-QA, one-off)

Run after QA is complete and articles are finalized:

1. `python3 backfill/html_pipeline/pipe4b_match_dois.py --volume <vol.iss> --email EMAIL` — matches refs to Crossref DOIs, writes `<pub-id>` to JATS + `doi_matches.json`. See [`docs/crossref-reference-linking.md`](docs/crossref-reference-linking.md).
2. `sudo python3 backfill/html_pipeline/pipe9b_citation_dois.py --target dev` — writes matched DOIs from JATS to OJS `citation_settings` table (2 SQL calls, seconds). Requires pkp/crossrefReferenceLinking plugin for display.
3. `sudo python3 backfill/html_pipeline/pipe9c_content_filtered.py --target dev` — writes content-filtered flags from JATS `<custom-meta>` to OJS `publication_settings` table. Used by Archive Checker filter pill and article page warning.

### Content-filtered articles

Articles that couldn't be fully extracted (Haiku content-filtered, PyMuPDF fallback) are flagged through the full chain:

1. **JATS** (source of truth): `<custom-meta><meta-name>content-filtered</meta-name><meta-value>true</meta-value></custom-meta>` in `<article-meta>`. Written by pipe3 from `.post.html` `AUTO-EXTRACTED` comment or toc.json `_content_filtered` flag.
2. **Galley HTML**: `<div data-content-filtered="true">` prepended by pipe5 (reads from JATS).
3. **OJS DB**: `publication_settings` row (`setting_name='contentFiltered'`). Written by pipe9c (reads from JATS).
4. **Archive Checker**: filter pill excludes by default, warning banner on article. Queries DB.
5. **Article page**: warning notice. Queries DB.

To manually flag an article: set `_content_filtered: true` in toc.json, rerun pipe3→pipe9c.

### Deploying to live

Three scenarios, from lightest to heaviest.

#### Code-only changes (plugin code, CSS, JS, templates)

No article data changes — just updated plugin behaviour or UI.

1. `ssh sea-live 'cd /opt/pharkie-ojs-plugins && git pull'`
2. If docker-compose.yml changed (volume mounts, env vars): `docker compose up -d --force-recreate ojs`
3. `scripts/monitoring/content-check.sh --host=sea-live` — verify site still works

#### Specific issues changed (pipeline fix affecting some volumes)

Article data changed for specific volumes only. No need to reimport everything.

1. Reprocess affected volumes on dev: pipe2→pipe6 for each volume
2. Re-attach DOIs: run the DOI re-attachment script (reads `doi_matches.json`, writes to JATS)
3. Import to dev: `pipe7 --force` + `pipe8` for affected volumes, verify in Archive Checker
4. Better Stack: pause monitors
5. `scripts/dev/backfill-remote.sh --host=sea-live --sync-only` — sync import XMLs
6. `ssh sea-live` → `pipe7_import.sh <affected volumes> --force` — reimport just those issues
7. `pipe8_restore.py --target live --confirm`
8. `pipe9b_citation_dois.py --target live --confirm`
9. `pipe9c_content_filtered.py --target live --confirm`
10. Better Stack: unpause monitors
11. `scripts/monitoring/content-check.sh --host=sea-live`

#### Full reimport (all 68 volumes)

Nuclear option — wipes all articles and reimports from scratch. Use when systemic pipeline changes affect all volumes, or for a clean deployment.

1. Full pipeline rerun on dev: pipe2→pipe6 for all volumes + DOI re-attachment
2. Import to dev: `pipe7 --force` + `pipe8` + `pipe9b` + `pipe9c`, verify
3. Better Stack: pause monitors
4. `scripts/dev/backfill-remote.sh --host=sea-live` — syncs + wipes + reimports all
5. `pipe8_restore.py --target live --confirm` — restores submission/issue IDs + DOI status
6. `pipe9b_citation_dois.py --target live --confirm` — writes citation DOIs
7. `pipe9c_content_filtered.py --target live --confirm` — writes content-filtered flags
8. Sync Archive Checker reviews: export from dev `archive_checker_reviews`, import to live
9. Better Stack: unpause monitors
10. `scripts/monitoring/smoke-test.sh --host=sea-live` — infrastructure (28 checks)
11. `scripts/monitoring/content-check.sh --host=sea-live` — content (14 checks)

#### DOI re-attachment after pipeline reruns

pipe3 wipes JATS (including DOIs from pipe4b). After any pipe3+pipe4 rerun, DOIs must be re-attached from `doi_matches.json` before pipe5+pipe6. This is a Python script (not a pipeline step) that reads cached matches and writes `<pub-id>` elements back to JATS. Without this, DOIs are silently lost.

#### Notes

- `--force` reimports existing issues without wiping. `--wipe-articles` wipes first (preserves users/subscriptions/payments).
- `pipe8` is always needed after `--wipe-articles` or `--force` to restore original submission IDs and DOI registration status (preserves URLs, DOI links, payment records, prevents re-deposit).
- `pipe9b` and `pipe9c` are always needed after import — they write to DB tables that the import doesn't populate.
- Archive Checker reviews survive `--wipe-articles` (custom table, not touched by import). But `publication_id` becomes stale — the `submission_id` column is what matters.
- Better Stack monitors must be paused before any operation that causes downtime. See memory file `feedback_maintenance_window.md`.

### Data and tests

All journal-specific data lives in the private repo via symlink: `backfill/private` → `private/backfill/`. Regression tests: `python3 -m pytest backfill/tests/ -v`. See `CONTRIBUTING.md` for the fixture-driven testing workflow.
