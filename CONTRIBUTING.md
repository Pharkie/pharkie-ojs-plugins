# Contributing

## Code conventions

- WordPress plugin standards (PHP)
- Prefix everything `wpojs_`
- Use WP HTTP API (`wp_remote_post` etc.) — not raw cURL
- Use Action Scheduler for async jobs and retries
- Log all sync operations — failures must be visible in WP admin
- API key stored as `wp-config.php` constant (`WPOJS_API_KEY`), not in the database
- Settings page for OJS URL, subscription type mapping (WooCommerce Product → OJS Subscription Type), journal ID(s)
- **No raw SQL in plugin code.** Plugins use their respective frameworks (WordPress HTTP API, OJS DAOs/services, REST endpoints). Direct DB queries are only acceptable in setup/migration scripts (dev environment bootstrapping), never in runtime plugin code.
- **Setup scripts are infrastructure automation** — they bootstrap dev/staging environments with direct DB calls where APIs don't exist (OJS subscription types, plugin settings). This is acceptable because they run once, not on every request.

## Backfill pipeline rules

- **Haiku prompt is frozen.** Once the extraction prompt produces good raw HTML, never modify it. All content fixes go in `backfill/lib/postprocess.py`.
- **No magic numbers.** Thresholds must be named constants (e.g. `MATCH_THRESHOLD`). Search boundaries must use structural landmarks (e.g. "up to the first body heading"), not arbitrary percentages like "first 20% of HTML".
- **Raw HTML is preserved.** `pipe1_haiku_html.py` saves `.raw.html` (full extraction). `pipe2_postprocess.py` produces `.post.html`. Post-processing can be rerun from raw without API calls.

## Don't

- Modify OJS source code
- Sync plaintext passwords between systems (password hashes are synced during bulk sync — this is safe)
- Build message queues, webhook servers, or microservices
- Add features beyond the core sync requirement
- Assume any OJS API endpoint exists without checking [`docs/ojs-sync-plugin-api.md`](docs/ojs-sync-plugin-api.md)
- Revisit OIDC SSO or Pull-verify — both eliminated with documented reasons (see [`docs/discovery.md`](docs/discovery.md))

## Pre-commit hooks

Installed via `./setup-hooks.sh` (runs automatically in dev container). Symlinks `.git/hooks/pre-commit` to `scripts/pre-commit`. Checks:

1. Environment variable documentation
2. YAML syntax
3. Documentation link validation
4. Backfill tests (if Python files staged)
5. Secret detection (ggshield)

Modular checks live in `scripts/lib/`.

## Testing

### Backfill regression tests

Fixture-driven regression tests for the backfill pipeline's deterministic detection logic (name detection, citation classification, HTML post-processing, etc.).

**Test data lives in `backfill/tests/fixtures/*.json`** — human-readable JSON files, one per category. Each fixture has both true cases (should be detected) and false cases (should NOT be detected). Open these files directly to review or update ground truth.

```bash
# Run all backfill tests
python3 -m pytest backfill/tests/ -v

# Run a specific test file
python3 -m pytest backfill/tests/test_citations.py -v
```

**QA-driven workflow — when you find a bug during QA:**

1. Add the exact text to the relevant fixture JSON (`backfill/tests/fixtures/*.json`) — as a `true` entry if it was missed, or `false` if it was wrongly detected
2. Run `python3 -m pytest backfill/tests/` — the new case should **fail**
3. Fix the implementation (in `backfill/lib/citations.py`, `backfill/lib/postprocess.py`, etc.)
4. Run tests again — new case passes, no other tests break
5. **Never change a test to match implementation.** If a test fails, the code is wrong, not the test. The fixtures are human-verified ground truth.

**Fixture files:**

| File | What it tests |
|---|---|
| `names.json` | Person name detection (`looks_like_person_name`) — Western, East Asian, Arabic, South Asian, Turkish, African, Russian, Greek, Spanish, accented, particles, initials, hyphenated; false cases include article titles and English phrases |
| `bios.json` | Author bio detection (`is_author_bio`) |
| `references.json` | Bibliographic reference detection (`is_reference`) |
| `notes.json` | Note/endnote detection with expected reason (`is_note`) |
| `provenance.json` | Provenance note detection (`is_provenance`) |
| `contacts.json` | Contact detail detection (`is_author_contact`) |
| `classify.json` | Combined reference-vs-note classification (`classify`) |
| `postprocess.json` | HTML post-processing: `strip_title`, `strip_authors`, `strip_abstract`, `strip_keywords`, `strip_start_bleed`, `strip_end_bleed`, `postprocess_article` |

### E2E tests

E2E tests use Playwright. Run from the project root (never from `e2e/`):

```bash
# Run all tests
npx playwright test

# Run a specific test file
npx playwright test e2e/tests/wpojs-sync/ojs-login.spec.ts
```

Only run one Playwright instance at a time — two instances against the same Docker environment corrupts state.

## Dev environment

See [`docs/docker-setup.md`](docs/docker-setup.md) for Docker setup and [`docs/setup-guide.md`](docs/setup-guide.md) for secrets management and devcontainer details.

Quick start:

```bash
scripts/rebuild-dev.sh --with-sample-data --skip-tests
```

This seeds ~1400 test WP users + subscriptions and 2 sample OJS issues. For the full journal archive, run `backfill/import.sh backfill/private/output/* --wipe-articles` after rebuild.
