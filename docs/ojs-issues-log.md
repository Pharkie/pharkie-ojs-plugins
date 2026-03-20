# OJS Issues Log

Problems encountered with OJS during this project. Evidence for the Janeway backup evaluation.

Issues marked **[we reported]** were filed by us. Others were found by other users — we just hit the same problems.

## Install bugs

### 1. OJS 3.5.0-2: ROR dataset crash during install

The web installer crashes with `ValueError: Path cannot be empty` in `UpdateRorRegistryDataset.php`. A logic bug (`empty($pathCsv || ...)` instead of `empty($pathCsv) || ...`) lets an empty CSV path through to `fopen('', 'r')`. Fresh install is impossible on 3.5.0-2.

- **Reported by others:** [pkp/pkp-lib#12144](https://github.com/pkp/pkp-lib/issues/12144)
- **Fixed in:** OJS 3.5.0.3 / Docker tag `3_5_0-3`

### 2. Docker CLI install script broken for OJS 3.5

The `pkp-cli-install` script (used when `OJS_CLI_INSTALL=1`) fails silently on 3.5.x. Two bugs: missing required `timeZone` field (new in 3.5), and no session cookie (OJS 3.5 rejects the POST without one). The install appears to succeed but `installed` stays `Off`.

- **We reported:** [pkp/containers#26](https://github.com/pkp/containers/issues/26)
- **Workaround:** Our `docker/reset-ojs.sh` script handles both issues.

### 3. Failed install leaves database and config in corrupt state

If the install crashes partway through (e.g. due to bug #1), OJS sets `installed = On` in config and creates partial database tables. On next visit it tries to use the half-built database and 500s. Recovery requires three separate resets: database wipe, config flag reset, AND volume deletion. There's no self-healing or install rollback.

- **Not reported upstream** — general design issue, not a single fixable bug.

## Import/Export bugs

### 4. Native XML import fails on own export

Exporting issues from one OJS 3.5 instance and importing to another fails with:

> SQLSTATE[23000]: Integrity constraint violation: 1452 Cannot add or update a child row: a foreign key constraint fails (`ojs`.`submission_files`, CONSTRAINT `submission_files_source_submission_file_id_foreign`...)

The exporter writes `source_submission_file_id` references to IDs that don't exist yet in the target database. The importer doesn't resolve the ordering. This is OJS exporting data that OJS can't import.

- **We reported:** [pkp/pkp-lib#12276](https://github.com/pkp/pkp-lib/issues/12276)
- **Workaround:** Strip `source_submission_file_id` references from the XML before importing. Loses some file linkage but metadata imports fine.

## API gaps

### 5. No subscription REST API

OJS has no REST endpoints for subscription CRUD. This is why we had to build a custom plugin (`wpojs-subscription-api`). See `docs/ojs-sync-plugin-api.md`.

- **Not reported upstream** — known long-standing gap, not a bug.

### 6. User creation API unconfirmed

The Swagger spec shows read-only user endpoints. Creating users programmatically requires either the custom plugin or direct DB access.

- **Not reported upstream** — known limitation.

## Configuration gaps

### 7. Docker image installs pdftotext but doesn't enable it

The `pkpofficial/ojs:3_5_0-3` Docker image installs `poppler-utils` (which provides `pdftotext`), but the default config has all search indexing helpers commented out. PDF files import successfully but are never full-text indexed. The only indication is a `Skipped indexation: No suitable parser` log message during import — easy to miss.

The fix is adding the `[search]` section with the `index[application/pdf]` line pointing to `pdftotext`. OJS's own `config.TEMPLATE.inc.php` has this as a commented-out example, but the Docker image doesn't uncomment it despite installing the binary.

- **We reported:** [pkp/containers#27](https://github.com/pkp/containers/issues/27)
- **Impact:** Search won't find content within PDFs until config is fixed
- **Workaround:** Add to `config.inc.php`:
  ```ini
  [search]
  index[application/pdf] = "/usr/bin/pdftotext -enc UTF-8 -nopgbrk %s - | /usr/bin/tr '[:cntrl:]' ' '"
  ```

## Docker / platform

### 9. No ARM64 Docker image — Mac development requires Rosetta emulation

The official `pkpofficial/ojs` images are amd64-only. PKP's CI (`pkp/containers`) uses plain `docker build` with no `buildx` or `--platform` flags. No upstream issues have been filed requesting ARM64 support.

On Apple Silicon Macs, Docker Desktop runs the image under Rosetta emulation at ~3–5x slower than native. This is mostly fine for browsing OJS, but bulk API operations (e.g. creating 684 users during sync) overwhelm the emulated container — requests time out with 500 errors even though the DB writes succeed. Load-based backpressure on the OJS side now handles this automatically: OJS self-monitors response times and returns 429 with `Retry-After` when under pressure, and WP's adaptive throttling backs off accordingly.

One community image (`teic/docker-pkp-ojs`) builds for both amd64 and arm64 and covers OJS 3.3–3.5, but has minimal adoption (~632 pulls). Building the official Dockerfile locally with `docker buildx --platform linux/arm64` should also work since OJS is pure PHP with no arch-specific binaries.

- **Not reported upstream** — no ARM64 issues filed on `pkp/containers`.
- **Community ARM64 image:** [teic/docker-pkp-ojs](https://hub.docker.com/r/teic/docker-pkp-ojs)
- **Workaround:** OJS load-based backpressure handles this automatically. No manual `--delay` needed.

## Caching

### 11. Serialized plugin cache (`cache/HTML/*.ser`) prevents new sidebar blocks from appearing

After adding a new custom block via direct DB inserts (Custom Block Manager plugin), the block doesn't render in the sidebar until `cache/HTML/*.ser` files are deleted. Clearing `t_compile/` and `opcache/` alone is insufficient — OJS serializes plugin settings into `cache/HTML/` and serves from that cache until it's manually purged.

This is not a bug per se — it's expected cache behaviour — but it's a trap when provisioning via setup scripts. A full `find cache/ -type f -delete` is needed after any plugin setting changes.

- **Not reported upstream** — expected behaviour, easy to miss.
- **Workaround:** Delete all cache files after setup, which `setup-ojs.sh` already does.

## Breaking changes in 3.5

### 10. "Editorial Team" renamed to "Editorial Masthead"

OJS 3.5 renamed the Editorial Team page to Editorial Masthead. This affects the route (`/about/editorialTeam` → `/about/editorialMasthead`), the page title locale key (`common.editorialMasthead`), and the nav menu item type (`NMI_TYPE_MASTHEAD`). The new page auto-populates from assigned editorial roles rather than being manually edited — there's no longer a free-text field for the editorial team list.

This is a deliberate change: "masthead" is the traditional publishing term. PKP is still iterating on it — [pkp/pkp-lib#11800](https://github.com/pkp/pkp-lib/issues/11800) and [pkp/pkp-lib#11805](https://github.com/pkp/pkp-lib/issues/11805) track improvements for 3.5.0-2, including better handling of roles that don't map to OJS's predefined list.

- **Forum discussion:** [PKP Forum #97267](https://forum.pkp.sfu.ca/t/error-to-include-editorial-masthead-in-omp-3-5/97267/5)
- **Impact for SEA:** Nav menu item needed updating from "Editorial Team" to "Editorial Masthead". The editorial team content is now driven by OJS role assignments rather than a static page — ensure editorial board members are assigned appropriate roles in OJS.
- **Fixed in:** `setup-ojs.sh` sets the nav label to "Editorial Masthead".

## Static analysis

### 8. PHPStan cannot fully analyse OJS plugins

OJS uses a non-standard autoloader (`PKP\core\PKPContainer` + runtime classmap) that PHPStan can't resolve without booting the full application. Running PHPStan on the `wpojs-subscription-api` plugin produces ~70 false positives:

- **`Response::HTTP_*` constants** (~40): `Illuminate\Http\Response` extends Symfony's `Response` which defines these. PHPStan can't find them because the Symfony package lives inside `laravel/framework/src/Illuminate` rather than as a standalone vendor package.
- **DAO magic methods** (~15): `IndividualSubscriptionDAO::updateObject()`, `getByUserIdForJournal()`, etc. exist via OJS's `__call` delegation but aren't visible to static analysis.
- **Unscanned classes** (~10): `AccessKeyManager`, `APIRouter::registerPluginApiControllers()` — exist at runtime but live in directories PHPStan doesn't scan.
- **Intentional `method_exists()` checks** (~5): The `/preflight` endpoint deliberately checks whether OJS methods exist for version compatibility. PHPStan correctly notes they always return true on 3.5 — that's the point.

**One real bug found:** `authorize()` method on `WpojsApiController` shadowed `PKPBaseController::authorize()` with a different signature. Renamed to `checkAuth()`.

- **Not reported upstream** — OJS architectural limitation, not a fixable bug.

**How to run:** Download `phpstan.phar` into the OJS container (no Composer available), point at the OJS autoloader, and provide a bootstrap file defining `PKP_STRICT_MODE`:

```bash
docker compose exec ojs bash -c "
  curl -sL https://github.com/phpstan/phpstan/releases/latest/download/phpstan.phar -o /tmp/phpstan.phar
  echo '<?php define(\"PKP_STRICT_MODE\", false);' > /tmp/phpstan-bootstrap.php
  cat > /tmp/phpstan.neon << 'NEON'
parameters:
    bootstrapFiles:
        - /tmp/phpstan-bootstrap.php
    level: 5
    paths:
        - /var/www/html/plugins/generic/wpojsSubscriptionApi
    scanDirectories:
        - /var/www/html/lib/pkp/classes
        - /var/www/html/classes
        - /var/www/html/lib/pkp/lib/vendor/laravel/framework/src/Illuminate
    scanFiles:
        - /var/www/html/lib/pkp/includes/functions.php
NEON
  php /tmp/phpstan.phar analyse --configuration=/tmp/phpstan.neon --no-progress --memory-limit=1G
"
```

## Role model

### 12. "Journal manager" and "Journal editor" share the same role_id

Both `Journal manager` (user_group_id 2) and `Journal editor` (user_group_id 3) map to `role_id = 16` in the `user_groups` table. They are functionally the same permission level — the distinction is purely a workflow label. There is no role hierarchy; manager does not supersede editor. A user assigned to both gains nothing over having just one.

This makes role planning confusing: you'd expect manager > editor > section editor, but the first two are identical under the hood. OJS uses `user_group_id` for UI filtering (who sees what dashboard tabs) but `role_id` for actual permission checks.

- **Not reported upstream** — by-design OJS architecture, unlikely to change.

### 13. Empty "References" section renders on every article page (OJS 3.5)

The article details template (`article_details.tpl`) has `{if $parsedCitations || (string) $publication->getData('citationsRaw')}` to conditionally show the References section. In OJS 3.5, `$parsedCitations` is always set to a `LazyCollection` object (from `CitationDAO::getByPublicationId()`), which is truthy in Smarty even when empty. Similarly, `citationsRaw` is now a `Stringable` proxy object, also truthy. The `$parsedCitations` check short-circuits, so the `(string)` cast on citationsRaw never runs.

Result: every article renders an empty `<section class="item references">` with just the "References" heading and no content — even when no citations exist.

Root cause: `Publication\DAO.php` line 180 (`$citations = $citationDao->getByPublicationId(...)`) returns a Collection object, not a plain array. The Smarty template predates the Laravel migration and assumes falsy-when-empty.

**Workaround:** Hide via CSS in the inline HTML galley plugin when inline content is present.

- **Not yet reported upstream** — should be filed as a template bug.

