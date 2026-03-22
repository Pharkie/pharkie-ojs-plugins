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

### 13. API/DB field names don't match UI labels

Several OJS settings use different names in the API/database versus the admin UI:

| UI label | API/DB field | Notes |
|---|---|---|
| Journal Initials | `acronym` | Locale-aware (`{en: "EA"}`) |
| Journal Abbreviation | `abbreviation` | Locale-aware (`{en: "Existential Analysis"}`) |

This causes confusion when configuring via API or setup scripts — you write `acronym` expecting it to be the abbreviation.

- **Not reported upstream** — cosmetic inconsistency, unlikely to change.

### 14. Empty "References" section renders on every article page (OJS 3.5)

The article details template (`article_details.tpl`) has `{if $parsedCitations || (string) $publication->getData('citationsRaw')}` to conditionally show the References section. In OJS 3.5, `$parsedCitations` is always set to a `LazyCollection` object (from `CitationDAO::getByPublicationId()`), which is truthy in Smarty even when empty. Similarly, `citationsRaw` is now a `Stringable` proxy object, also truthy. The `$parsedCitations` check short-circuits, so the `(string)` cast on citationsRaw never runs.

Result: every article renders an empty `<section class="item references">` with just the "References" heading and no content — even when no citations exist.

Root cause: `Publication\DAO.php` line 180 (`$citations = $citationDao->getByPublicationId(...)`) returns a Collection object, not a plain array. The Smarty template predates the Laravel migration and assumes falsy-when-empty.

**Workaround:** Hide via CSS in the inline HTML galley plugin when inline content is present.

- **Known issue:** [pkp/pkp-lib#12184](https://github.com/pkp/pkp-lib/issues/12184), fixed in [pkp/ojs#5249](https://github.com/pkp/ojs/pull/5249) (merged 2026-01-06, `stable-3_5_0` branch). Fix: `{if count($parsedCitations)}` instead of `{if $parsedCitations}`. Not yet in the `3_5_0-3` Docker image we use.
- **Our workaround:** JS in inline HTML galley plugin hides the section when `.value` has no text content.

## Access control

### 15. `$hasAccess` template variable is false for site admins and journal managers

OJS's `ArticleHandler` computes the `hasAccess` template variable based on subscription/purchase/open-access status only. Site admins (`ROLE_ID_SITE_ADMIN`) and journal managers (`ROLE_ID_MANAGER`) get `hasAccess=false` on paywalled articles, even though they have full editorial access. This means any template or plugin relying on `$hasAccess` to gate content will incorrectly hide it from admin users.

The root cause is in `ArticleHandler::view()` — it checks `IssueAction::subscribedUser()` and `IssueAction::purchasedArticle()` but doesn't check editorial roles.

- **Not reported upstream** — design issue in access logic, affects any paywalled journal.
- **Our workaround:** Inline HTML galley plugin checks `userRoles` template var for `ROLE_ID_SITE_ADMIN` / `ROLE_ID_MANAGER` and overrides `$hasAccess` for those users.

## Schema validation

### 16. W3C MathML XSD returns 403, causing PHP warnings during DOI deposit

OJS's `XMLTypeDescription.php` (line 141) calls `DOMDocument::schemaValidate()` against `http://www.w3.org/Math/XMLSchema/mathml3/mathml3.xsd` during Crossref XML generation. W3C now returns HTTP 403 for automated schema requests (they've long asked tools to use local copies rather than hitting their servers).

The warning fires on every `DepositSubmission` job:

```
PHP Warning: DOMDocument::schemaValidate(http://www.w3.org/Math/XMLSchema/mathml3/mathml3.xsd):
Failed to open stream: HTTP request failed! HTTP/1.1 403 Forbidden
in /var/www/html/lib/pkp/classes/xslt/XMLTypeDescription.php on line 141
```

Jobs still process successfully — the schema validation fails but `schemaValidate()` returns false, and OJS continues. The warning is cosmetic noise.

- **Not reported upstream** — W3C policy change, not an OJS bug. Many projects hit this.
- **Impact:** Log noise during bulk DOI deposit (hundreds of warnings). No functional impact.
- **Workaround:** Could add a local XML catalog (`/etc/xml/catalog`) mapping the URL to a bundled XSD copy, avoiding the HTTP fetch entirely. Not worth doing unless the warnings cause log rotation issues.

## Payments

### 17. Manual Payment plugin has no admin approval UI

The Manual Payment plugin (`plugins/paymethod/manual`) lets non-subscribers request article purchases: the buyer sees a "Send notification of payment" button, which emails the journal manager. But there is no admin UI to approve/fulfil the payment. The payment appears in Payments > Payments as a log entry, but with no action button. The only way to complete a manual payment is direct DB insertion into `completed_payments` or calling `OJSPaymentManager::fulfillQueuedPayment()` programmatically.

This makes the Manual Payment plugin essentially unusable as a self-service purchase flow — it requires developer intervention to grant access after payment.

- **Confirmed upstream:** [pkp/pkp-lib#6136](https://github.com/pkp/pkp-lib/issues/6136) — open since 2020, unresolved. The issue confirms the plugin has no completion/approval mechanism. Six years without a fix.
- **Impact:** Cannot use Manual Payment as a fallback when PayPal is unavailable.
- **Workaround:** Use PayPal plugin (which auto-completes payments on callback).

### 18. PayPal sandbox rejects all payments for UK-based developer account

Two separate issues confirmed:

**a) Expired sandbox credentials (fixed 2026-03-21).** Original sandbox app credentials returned 401 "Authentication failed". Fixed by creating a fresh sandbox app at developer.paypal.com.

**b) Sandbox rejects buyer approval (confirmed 2026-03-21).** With valid credentials, the PayPal REST API successfully creates the payment order and redirects to `sandbox.paypal.com`. The buyer logs in, the "Pay" button appears briefly, then rejects with "This transaction has been declined in order to comply with international regulations." Tested and fails with:

- UK and US sandbox buyer accounts
- GBP and USD currencies
- UK IP and US VPN
- Via OJS and via standalone script (`scripts/test-paypal.js`) — confirms the issue is 100% PayPal sandbox, not our integration

- **Reported upstream:** [paypal/paypal-js#511](https://github.com/paypal/paypal-js/issues/511) — "The issue is related to the Sandbox user's country. It starts working with a US account. all other countries won't work." PayPal's Checkout team could not reproduce it; closed as stale (2025-03-26).
- **Standalone reproduction:** `node scripts/test-paypal.js` — creates a PayPal order via REST API, opens sandbox checkout in Playwright, logs in as buyer, captures the rejection. No OJS involved. Screenshot saved to `scripts/paypal-sandbox-result.png`.
- **PayPal support ticket submitted** (2026-03-21), awaiting response.
- **Impact:** Cannot complete the PayPal purchase flow from our developer environment. The OJS integration is correct (creates order, redirects to PayPal, return URL wired up), but the sandbox buyer approval step is blocked.
- **Workaround:** Manual Payment plugin verifies the OJS access-granting logic works end-to-end (tested and confirmed 2026-03-21). The PayPal callback flow can only be tested with live credentials.
- **Resolution:** PayPal abandoned in favour of Stripe (2026-03-22). See `docs/private/stripe-live-checklist.md`. The `scripts/test-paypal.js` script remains as a historical artifact.

## 11. Crossref deposit errors: OJS doesn't persist failure details (2026-03-21)

**Two separate bugs:**

1. **`$msgSave` not set on success-with-failures path:** When Crossref returns HTTP 200 but with `failure_count > 0` in the response XML, OJS marks DOIs as ERROR but `$msgSave` is null (only populated in the HTTP exception handler). The error detail from Crossref's response is discarded.

2. **`Repo::doi()->edit()` doesn't persist custom settings:** Even when `$msgSave` IS populated (e.g. on a 401 exception), `updateDepositStatus()` passes it to `Repo::doi()->edit()` but it never appears in `doi_settings`. The OJS 3.5 Doi entity schema likely doesn't include `crossrefplugin_failedMsg` as a recognized property, so it's silently dropped. This means error details are never visible in the DOI management UI — the "Show error details" button never appears.

**Location:** `plugins/generic/crossref/CrossrefExportPlugin.php`, `depositXML()` (~line 367) and `updateDepositStatus()` (~line 403).

**Impact:** DOI management UI shows "Error" status but never shows error details. Debugging deposit failures requires checking container/CLI logs directly.

**Fix applied (permanent):** Core patch in `docker/ojs/patches/crossref-error-details.php`, applied at image build time via Dockerfile. Two changes:
1. Sets `$msgSave = (string)$response->getBody()` in the `failureCount > 0` path so error details are captured.
2. Writes `crossrefplugin_failedMsg`, `_batchId`, and `_successMsg` directly to `doi_settings` via `DB::table()` after `Repo::doi()->edit()`, bypassing the broken entity schema.

Patch is idempotent (safe to re-run). Verified on live — error details now visible in DOI management UI.

## 12. MathML schema validation warning during Crossref XML export (2026-03-21)

**Problem:** During Crossref XML export, OJS attempts to validate against `http://www.w3.org/Math/XMLSchema/mathml3/mathml3.xsd`. W3C returns HTTP 403 (they block automated schema downloads — [announced Dec 2022](https://www.w3.org/blog/2022/12/w3c-xml-related-schema-and-dtd-files-now-hosted-by-github-pages/)).

**Location:** `lib/pkp/classes/xslt/XMLTypeDescription.php` line 141 — `$xmlDom->schemaValidate($this->_validationSource)`.

**Impact:** Cosmetic — PHP warning in logs but does not prevent XML generation or deposit. The export continues and produces valid Crossref XML.

**Fix:** None applied. This is OJS core code (no core modifications per project constraints). Would require PKP to bundle the schema locally or skip the remote validation. Known upstream issue.

## 13. `jobs.php work --stop-when-empty` doesn't exit (2026-03-21)

**Problem:** Laravel queue workers started with `jobs.php work --stop-when-empty` don't actually exit when the queue is empty. Workers from a `blast-queue.sh` run at 18:30 were still alive at 20:30+, silently polling for new jobs with stale (pre-patch) code. This caused deposits to be processed by old code, making debugging impossible.

**Impact:** Stale workers process jobs with old PHP code (opcache doesn't help — they loaded files at startup). Any code patches applied after workers start have no effect until workers are killed.

**Fix applied:** Updated `blast-queue.sh` with three safeguards:
1. Auto-kills stale `jobs.php work` processes before starting new ones
2. Wraps workers in `timeout` (default 30 min) so they can't live forever
3. Added `--kill` flag for manual cleanup (`blast-queue.sh --host=sea-live --kill`)

