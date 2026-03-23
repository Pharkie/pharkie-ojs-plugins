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

This seeds ~1400 test WP users + subscriptions and 2 sample OJS issues. For the full journal archive, run `backfill/import.sh backfill/output/* --clean` after rebuild.
