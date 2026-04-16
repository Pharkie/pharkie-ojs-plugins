# OJS Issues Log

Problems encountered with OJS during this project. Evidence for the Janeway backup evaluation.

Issues marked **[reported]** were filed upstream. Others were found by other users — the same problems were encountered independently.

## Summary

| # | Issue | Status | Action |
|---|-------|--------|--------|
| 1 | ROR dataset crash during install | Fixed in 3.5.0-3 | None |
| 2 | Docker CLI install broken for 3.5 | PR submitted | Follow up on pkp/containers#28 |
| 3 | Failed install leaves corrupt state | Design issue | Report issue only |
| 4 | Native XML import fails on own export | Confirmed bug | **Submit PR** (pkp/pkp-lib) |
| 5 | No subscription REST API | Known gap | Report issue only |
| 6 | User creation API unconfirmed | Known gap | Comment on pkp/pkp-lib#4952 |
| 7 | Docker image doesn't enable pdftotext | By-design per maintainer | Docs PR or env-var PR |
| 8 | PHPStan cannot fully analyse plugins | Architectural limitation | None |
| 9 | No ARM64 Docker image | Confirmed | Report issue on pkp/docker-ojs |
| 10 | "Editorial Team" → "Editorial Masthead" | Deliberate 3.5 change | None |
| 11 | Serialized plugin cache blocks sidebar | Expected cache behaviour | None |
| 12 | Manager/Editor share role_id | By-design | None |
| 13 | API/DB field names ≠ UI labels | Cosmetic | Report issue only |
| 14 | Empty "References" section renders | Fixed upstream | None (backported to stable-3_5_0) |
| 15 | `$hasAccess` false for admins | Confirmed bug, unreported | **Submit PR** (pkp/ojs) |
| 16 | Payment settings shows all plugin fields | By-design | None |
| 17 | Manual Payment plugin has no approval UI | Known gap (6 years) | Report 500 bug (#11121) |
| 18 | PayPal sandbox rejects UK accounts | Third-party issue | None (switched to Stripe) |
| 19 | MathML XSD 403 during Crossref deposit | Confirmed | Report issue |
| 20 | Crossref deposit doesn't persist errors | Confirmed bug | Report issue |
| 21 | `--stop-when-empty` flag silently ignored | Confirmed bug (parsing) | **Submit PR** (pkp/pkp-lib) |
| 22 | `runScheduledTasks.php` Fatal in CLI | Plausible, needs repro | Report issue |
| 23 | Native XML import — no idempotent update | By-design | None |
| 24 | Scheduler `flock()` crash on cache lock | OJS bug | Excluded from monitoring |

## Install bugs

### 1. OJS 3.5.0-2: ROR dataset crash during install [fixed]

The web installer crashes with `ValueError: Path cannot be empty` in `UpdateRorRegistryDataset.php`. A logic bug (`empty($pathCsv || ...)` instead of `empty($pathCsv) || ...`) lets an empty CSV path through to `fopen('', 'r')`. Fresh install is impossible on 3.5.0-2.

The primary root cause was that the ROR dataset on Zenodo changed its filename convention — the CSV filename filter `ror-data_schema_v2.csv` no longer matched the new files named `ror-data.csv`, so `$pathCsv` came back empty. The `empty()` precedence bug was a secondary issue that masked the file-existence check.

- **Reported by others:** [pkp/pkp-lib#12144](https://github.com/pkp/pkp-lib/issues/12144)
- **Fixed in:** OJS 3.5.0.3 / Docker tag `3_5_0-3` (PRs: pkp/pkp-lib#12146 for 3.5 branch, pkp/pkp-lib#12145 for main, merged 2025-12-17)

### 2. Docker CLI install script broken for OJS 3.5 [pr-submitted]

The `pkp-cli-install` script (used when `OJS_CLI_INSTALL=1`) fails silently on 3.5.x. Multiple bugs: missing required `timeZone` field (new in 3.5), no session cookie (OJS 3.5 rejects the POST without one), wrong locale codes (`en_US` → `en`), wrong DB driver (`mysql` → `mysqli`), and timing issues (script runs before Apache starts).

- **Reported:** [pkp/containers#26](https://github.com/pkp/containers/issues/26)
- **PR submitted:** [pkp/containers#28](https://github.com/pkp/containers/pull/28) — maintainer (marcbria) engaged positively, confirmed human testing. May need a follow-up ping if no review activity.

### 3. Failed install leaves database and config in corrupt state [design-issue]

If the install crashes partway through (e.g. due to bug #1), OJS sets `installed = On` in config and creates partial database tables. On next visit it tries to use the half-built database and 500s. Recovery requires three separate resets: database wipe, config flag reset, AND volume deletion. There's no self-healing or install rollback.

A proper fix would require wrapping the entire install in a database transaction and only writing `installed = On` after successful completion. Complicated by MySQL auto-committing DDL statements and non-DB side effects (file creation, ROR download).

- **Not reported upstream** — general design issue, not a single fixable bug.

## Import/Export bugs

### 4. Native XML import fails on own export [pr-candidate]

Exporting issues from one OJS 3.5 instance and importing to another fails with:

> SQLSTATE[23000]: Integrity constraint violation: 1452 Cannot add or update a child row: a foreign key constraint fails (`ojs`.`submission_files`, CONSTRAINT `submission_files_source_submission_file_id_foreign`...)

The exporter writes `source_submission_file_id` references to IDs that don't exist yet in the target database. The importer doesn't resolve the ordering. This is OJS exporting data that OJS can't import.

The codebase already has ID-remapping infrastructure (`$deployment->getSubmissionFileDBId()`) used for dependent files, but `source_submission_file_id` was never wired into it. The fix is to defer setting `source_submission_file_id` until after all submission files are created, then remap using the existing old-to-new mapping. Single file change: `NativeXmlSubmissionFileFilter.php`.

- **Reported:** [pkp/pkp-lib#12276](https://github.com/pkp/pkp-lib/issues/12276) — open, not yet triaged by PKP.
- **Workaround:** Strip `source_submission_file_id` references from the XML before importing. Loses some file linkage but metadata imports fine.

### 23. Native XML import always creates new IDs — no idempotent import/update [by-design]

OJS's Native Import Export Plugin always creates new database rows for imported issues, articles, submissions, and galleys. There is no "update existing" mode — reimporting the same XML creates duplicates with new IDs. This means:

- Article URLs change (new `submission_id`)
- DOIs must be re-deposited (new IDs → new URL mappings)
- Completed payments and subscription access break (reference old IDs)
- Search index must be fully rebuilt

The `<id type="internal" advice="ignore">` attribute in the XML is the only ID-related hint, and OJS ignores it (as the attribute says). There is no `advice="update"` for submissions or issues.

A prior PR attempt ([pkp/pkp-lib#5331](https://github.com/pkp/pkp-lib/pull/5331), 2019, stalled) revealed that the update path triggers destructive behaviour in the DAO layer — submissions getting deleted mid-update. Related upstream issues: [pkp/pkp-lib#5132](https://github.com/pkp/pkp-lib/issues/5132), [pkp/pkp-lib#7898](https://github.com/pkp/pkp-lib/issues/7898).

- **Not reported upstream** — appears to be by-design. The importer is built for one-time migration, not iterative sync.
- **Workaround:** `backfill/html_pipeline/pipe9_issue_galleys.sh` inserts issue galleys directly via SQL + file copy, bypassing the XML importer entirely. For article-level fixes, edit the database directly.

## API gaps

### 5. No subscription REST API [known-gap]

OJS has no REST endpoints for subscription CRUD. This is why a custom plugin was needed (`wpojs-subscription-api`). See `docs/ojs-sync-plugin-api.md`.

A `ROLE_ID_SUBSCRIPTION_MANAGER` constant exists (value 2097152) and a "Subscription Manager" user group is defined in `registry/userGroups.xml`, but there is no API surface for subscription operations.

- **Not reported upstream** — known long-standing gap, not a bug. The REST API meta-issue [pkp/pkp-lib#1503](https://github.com/pkp/pkp-lib/issues/1503) is closed without covering subscriptions.

### 6. User creation API unconfirmed [known-gap]

The Swagger spec shows read-only user endpoints plus one PUT route (`/users/{userId}/endRole/{userGroupId}`). Creating users programmatically requires either the custom plugin or direct DB access. OJS 3.5 replaced direct user creation with an invitation workflow (UI-only, not API-exposed).

- **Upstream issue:** [pkp/pkp-lib#4952](https://github.com/pkp/pkp-lib/issues/4952) — "Extend User API functionality", open since 2019.
- **Not reported upstream** — known limitation. PKP's direction is toward the invitation workflow.

## Configuration gaps

### 7. Docker image installs pdftotext but doesn't enable it [by-design]

The `pkpofficial/ojs:3_5_0-3` Docker image installs `poppler-utils` (which provides `pdftotext`), but the default config has all search indexing helpers commented out. PDF files import successfully but are never full-text indexed. The only indication is a `Skipped indexation: No suitable parser` log message during import — easy to miss.

The fix is adding the `[search]` section with the `index[application/pdf]` line pointing to `pdftotext`. OJS's own `config.TEMPLATE.inc.php` has this as a commented-out example, but the Docker image doesn't uncomment it despite installing the binary.

- **Reported:** [pkp/containers#27](https://github.com/pkp/containers/issues/27) — maintainer (marcbria) responded this is by design: "The idea behind the images is offering the necessary stack to let users enable the feature they like." Suggested a documentation PR or environment variable toggle.
- **Impact:** Search won't find content within PDFs until config is fixed
- **Workaround:** Add to `config.inc.php`:
  ```ini
  [search]
  index[application/pdf] = "/usr/bin/pdftotext -enc UTF-8 -nopgbrk %s - | /usr/bin/tr '[:cntrl:]' ' '"
  ```

## Static analysis

### 8. PHPStan cannot fully analyse OJS plugins [architectural]

OJS uses a non-standard autoloader (`PKP\core\PKPContainer` + runtime classmap) that PHPStan can't resolve without booting the full application. Running PHPStan on the `wpojs-subscription-api` plugin produces ~70 false positives:

- **`Response::HTTP_*` constants** (~40): `Illuminate\Http\Response` extends Symfony's `Response` which defines these. PHPStan can't find them because the Symfony package lives inside `laravel/framework/src/Illuminate` rather than as a standalone vendor package.
- **DAO magic methods** (~15): `IndividualSubscriptionDAO::updateObject()`, `getByUserIdForJournal()`, etc. exist via OJS's `__call` delegation but aren't visible to static analysis.
- **Unscanned classes** (~10): `AccessKeyManager`, `APIRouter::registerPluginApiControllers()` — exist at runtime but live in directories PHPStan doesn't scan.
- **Intentional `method_exists()` checks** (~5): The `/preflight` endpoint deliberately checks whether OJS methods exist for version compatibility. PHPStan correctly notes they always return true on 3.5 — that's the point.

**One real bug found:** `authorize()` method on `WpojsApiController` shadowed `PKPBaseController::authorize()` with a different signature. Renamed to `checkAuth()`.

PKP does not use PHPStan in their CI. A more feasible contribution than a code fix would be a standalone `phpstan-pkp` extension package (similar to [phpstan-wordpress](https://github.com/szepeviktor/phpstan-wordpress)) providing stub files and classmap configuration.

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

## Docker / platform

### 9. No ARM64 Docker image — Mac development requires Rosetta emulation [confirmed]

The official `pkpofficial/ojs` images are amd64-only. PKP's CI (`pkp/containers`) uses plain `docker build` with no `buildx` or `--platform` flags. No upstream issues have been filed requesting ARM64 support. The upstream README mentions exploratory ARM64 work in GitLab pipelines with "promising preliminary results" but no shipped images.

On Apple Silicon Macs, Docker Desktop runs the image under Rosetta emulation at ~3–5x slower than native. This is mostly fine for browsing OJS, but bulk API operations (e.g. creating 684 users during sync) overwhelm the emulated container — requests time out with 500 errors even though the DB writes succeed. Load-based backpressure on the OJS side now handles this automatically: OJS self-monitors response times and returns 429 with `Retry-After` when under pressure, and WP's adaptive throttling backs off accordingly.

Adding `--platform linux/amd64,linux/arm64` to the Docker build pipeline is a well-understood change. The main risk is ensuring all Alpine packages and PHP extensions compile on ARM64, but Alpine has good ARM64 support and OJS is pure PHP with no arch-specific binaries.

- **Not reported upstream** — no ARM64 issues filed on `pkp/containers`.
- **Workaround:** OJS load-based backpressure handles this automatically. No manual `--delay` needed.

## Caching

### 11. Serialized plugin cache (`cache/HTML/*.ser`) prevents new sidebar blocks from appearing [expected]

After adding a new custom block via direct DB inserts (Custom Block Manager plugin), the block doesn't render in the sidebar until `cache/HTML/*.ser` files are deleted. Clearing `t_compile/` and `opcache/` alone is insufficient — OJS serializes plugin settings into `cache/HTML/` and serves from that cache until it's manually purged.

This is not a bug per se — it's expected cache behaviour — but it's a trap when provisioning via setup scripts. A full `find cache/ -type f -delete` is needed after any plugin setting changes.

- **Not reported upstream** — expected behaviour, easy to miss.
- **Workaround:** Delete all cache files after setup, which `setup-ojs.sh` already does.

## Breaking changes in 3.5

### 10. "Editorial Team" renamed to "Editorial Masthead" [by-design]

OJS 3.5 renamed the Editorial Team page to Editorial Masthead. This affects the route (`/about/editorialTeam` → `/about/editorialMasthead`), the page title locale key (`common.editorialMasthead`), and the nav menu item type (`NMI_TYPE_MASTHEAD`). The new page auto-populates from assigned editorial roles rather than being manually edited — there's no longer a free-text field for the editorial team list.

This is a deliberate change: "masthead" is the traditional publishing term. PKP is still iterating on it.

- **Upstream:** [pkp/pkp-lib#9271](https://github.com/pkp/pkp-lib/issues/9271) (Masthead Management, closed), [pkp/pkp-lib#10200](https://github.com/pkp/pkp-lib/issues/10200) (Remove editorialTeam.tpl, closed), [pkp/pkp-lib#11800](https://github.com/pkp/pkp-lib/issues/11800) (edit user masthead settings, open), [pkp/pkp-lib#11805](https://github.com/pkp/pkp-lib/issues/11805) (unchecking masthead checkboxes, closed).
- **Impact:** Nav menu item needs updating from "Editorial Team" to "Editorial Masthead". The editorial team content is now driven by OJS role assignments rather than a static page — ensure editorial board members are assigned appropriate roles in OJS.
- **Fixed in:** `setup-ojs.sh` sets the nav label to "Editorial Masthead".

## Role model

### 12. "Journal manager" and "Journal editor" share the same role_id [by-design]

Both `Journal manager` (user_group_id 2) and `Journal editor` (user_group_id 3) map to `role_id = 16` in the `user_groups` table. They are functionally the same permission level — the distinction is purely a workflow label. There is no role hierarchy; manager does not supersede editor. A user assigned to both gains nothing over having just one.

This makes role planning confusing: you'd expect manager > editor > section editor, but the first two are identical under the hood. OJS uses `user_group_id` for UI filtering (who sees what dashboard tabs) but `role_id` for actual permission checks.

PKP addressed the differentiation need via [pkp/pkp-lib#5504](https://github.com/pkp/pkp-lib/issues/5504) (merged), which added a "Permit changes to Settings" toggle to distinguish manager-level roles without changing role_id.

- **Not reported upstream** — by-design OJS architecture, unlikely to change.

### 13. API/DB field names don't match UI labels [cosmetic]

Several OJS settings use different names in the API/database versus the admin UI:

| UI label | API/DB field | Notes |
|---|---|---|
| Journal Initials | `acronym` | Locale-aware (`{en: "EA"}`) |
| Journal Abbreviation | `abbreviation` | Locale-aware (`{en: "Existential Analysis"}`) |

Verified in source: `PKPMastheadForm.php` defines a field with key `'acronym'` but labels it with locale key `manager.setup.contextInitials` (rendered as "Journal Initials" in the UI).

- **Not reported upstream** — cosmetic inconsistency, unlikely to change. A locale string change PR risks being seen as churn, and renaming the DB field would be a breaking change.

## Template / UI bugs

### 14. Empty "References" section renders on every article page (OJS 3.5) [fixed]

The article details template (`article_details.tpl`) has `{if $parsedCitations || (string) $publication->getData('citationsRaw')}` to conditionally show the References section. In OJS 3.5, `$parsedCitations` is always set to a `LazyCollection` object (from `CitationDAO::getByPublicationId()`), which is truthy in Smarty even when empty. Similarly, `citationsRaw` is now a `Stringable` proxy object, also truthy. The `$parsedCitations` check short-circuits, so the `(string)` cast on citationsRaw never runs.

Result: every article renders an empty `<section class="item references">` with just the "References" heading and no content — even when no citations exist.

Root cause: `Publication\DAO.php` line 180 (`$citations = $citationDao->getByPublicationId(...)`) returns a Collection object, not a plain array. The Smarty template predates the Laravel migration and assumes falsy-when-empty.

- **Known issue:** [pkp/pkp-lib#12184](https://github.com/pkp/pkp-lib/issues/12184), fixed in [pkp/ojs#5249](https://github.com/pkp/ojs/pull/5249) (merged 2026-01-06, `stable-3_5_0` branch). Fix: `{if count($parsedCitations)}` instead of `{if $parsedCitations}`. Backported to `stable-3_5_0` — available in next 3.5.x point release.
- **Workaround:** JS in inline HTML galley plugin hides the section when `.value` has no text content.

### 16. Payment settings page shows all plugins' fields regardless of selection [by-design]

The Distribution Settings → Payments page renders every installed payment plugin's configuration fields (PayPal, Manual Payment, Stripe) in a single long form, regardless of which plugin is selected in the "Payment Plugins" dropdown. There's no show/hide logic — all fields are always visible, making the page confusing and error-prone.

This is OJS core behaviour: `PKPPaymentSettingsForm` fires a `Form::config::before` hook that each payment plugin uses to append its fields. The form has no mechanism to conditionally show only the selected plugin's fields.

- **Not reported upstream** — by-design architecture, not a bug.
- **Impact:** Admin UX confusion. Three sets of payment credentials visible at once.
- **Workaround:** None applied. The page is visited once to configure keys and then rarely again.

## Access control

### 15. `$hasAccess` template variable is false for site admins and journal managers [pr-candidate]

OJS's `ArticleHandler` computes the `hasAccess` template variable based on subscription/purchase/open-access status only. Site admins (`ROLE_ID_SITE_ADMIN`) and journal managers (`ROLE_ID_MANAGER`) get `hasAccess=false` on paywalled articles, even though they have full editorial access. This means any template or plugin relying on `$hasAccess` to gate content will incorrectly hide it from admin users.

The root cause is in `ArticleHandler::view()` — it checks `IssueAction::subscribedUser()` and `IssueAction::purchasedArticle()` but doesn't check editorial roles. No existing upstream issue found.

The fix is a small addition to `pages/article/ArticleHandler.php` in `pkp/ojs`: add a role check for `ROLE_ID_SITE_ADMIN` and `ROLE_ID_MANAGER` to the `$hasAccess` assignment.

- **Not reported upstream** — design issue in access logic, affects any paywalled journal.
- **Workaround:** Inline HTML galley plugin checks `userRoles` template var for `ROLE_ID_SITE_ADMIN` / `ROLE_ID_MANAGER` and overrides `$hasAccess` for those users.

## Crossref / DOI

### 19. MathML XSD 403 during Crossref deposit [confirmed]

OJS's `XMLTypeDescription.php` (line 141) calls `DOMDocument::schemaValidate()` against `http://www.w3.org/Math/XMLSchema/mathml3/mathml3.xsd` during Crossref XML generation. W3C now returns HTTP 403 for automated schema requests (they've long asked tools to use local copies rather than hitting their servers — [announced Dec 2022](https://www.w3.org/blog/2022/12/w3c-xml-related-schema-and-dtd-files-now-hosted-by-github-pages/)).

The MathML URL is not hardcoded in OJS — it's imported transitively from the Crossref XSD. The Crossref plugin's `filterConfig.xml` declares `outputType="xml::schema(https://www.crossref.org/schemas/crossref5.4.0.xsd)"`, and PHP's XML validation recursively fetches all `<xs:import>` schemas including MathML3.

The warning fires on every `DepositSubmission` job:

```
PHP Warning: DOMDocument::schemaValidate(http://www.w3.org/Math/XMLSchema/mathml3/mathml3.xsd):
Failed to open stream: HTTP request failed! HTTP/1.1 403 Forbidden
in /var/www/html/lib/pkp/classes/xslt/XMLTypeDescription.php on line 141
```

Jobs still process successfully — the schema validation fails but `schemaValidate()` returns false, and OJS continues. The warning is cosmetic noise.

- **Not reported upstream** — W3C policy change. Related closed issues (#3391, #1955) addressed different root causes (proxy, `allow_url_fopen`). [PKP Forum thread](https://forum.pkp.sfu.ca/t/php-warning-domdocument-schemavalidate-http-www-w3-org-math-xmlschema-mathml3-mathml3-content-xsd-failed-to-open-stream/60061) reports the same symptom.
- **Impact:** Log noise during bulk DOI deposit (hundreds of warnings). No functional impact.
- **Fix:** Bundle the Crossref XSD (and its MathML3 dependency) locally, or use an XML Catalog to redirect remote schema URLs to local copies. Affects `pkp/pkp-lib/classes/xslt/XMLTypeDescription.php` and the Crossref plugin's `filterConfig.xml`.

### 20. Crossref deposit errors: OJS doesn't persist failure details [confirmed]

Two separate bugs:

1. **`$msgSave` not set on success-with-failures path:** When Crossref returns HTTP 200 but with `failure_count > 0` in the response XML, OJS marks DOIs as ERROR but `$msgSave` is null (only populated in the HTTP exception handler). The error detail from Crossref's response is discarded.

2. **Schema sanitization drops plugin settings:** Even when `$msgSave` IS populated (e.g. on a 401 exception), `updateDepositStatus()` passes it to `Repo::doi()->edit()` but it never appears in `doi_settings`. The OJS 3.5 `PKPSchemaService::sanitize()` (line 323-326) strips any property not defined in the entity schema (`schemas/doi.json`), and `crossrefplugin_failedMsg`, `_batchId`, `_successMsg` are not in that schema. The `getObjectAdditionalSettings()` method exists but is never consulted by `EntityDAO::_update()`.

**Location:** `plugins/generic/crossref/CrossrefExportPlugin.php`, `depositXML()` (~line 367) and `updateDepositStatus()` (~line 403).

**Impact:** DOI management UI shows "Error" status but never shows error details. Debugging deposit failures requires checking container/CLI logs directly.

**Fix applied (permanent):** Core patch in `docker/ojs/patches/crossref-error-details.php`, applied at image build time via Dockerfile. Two changes:
1. Sets `$msgSave = (string)$response->getBody()` in the `failureCount > 0` path so error details are captured.
2. Writes `crossrefplugin_failedMsg`, `_batchId`, and `_successMsg` directly to `doi_settings` via `DB::table()` after `Repo::doi()->edit()`, bypassing the broken entity schema.

Patch is idempotent (safe to re-run). Verified on live — error details now visible in DOI management UI.

- **Not reported upstream** — the schema sanitization issue may affect other plugins beyond Crossref (Datacite, etc.).

## Scheduler / Jobs

### 21. `jobs.php work --stop-when-empty` doesn't exit [pr-candidate]

Laravel queue workers started with `jobs.php work --stop-when-empty` don't actually exit when the queue is empty. Root cause is a flag-parsing bug in `gatherWorkerOptions()`:

```php
'stopWhenEmpty' => $this->getParameterValue('stop-when-empty',
    in_array('stop-when-empty', $parameters) ? true : $workerConfig->getStopWhenEmpty()),
```

`HasParameterList::setParameterList()` stores bare flags as numeric-indexed values with the `--` prefix intact (e.g. `$parameters[2] = '--stop-when-empty'`). Both `getParameterValue('stop-when-empty')` (looks for a named key) and `in_array('stop-when-empty', $parameters)` (looks for value without `--`) fail to match. The flag is silently ignored. The same bug affects `--force`. The default `$stopWhenEmpty` in `WorkerConfiguration` is `false`, so the worker runs as a daemon indefinitely.

The fix is a one-line change in `pkp/pkp-lib/tools/jobs.php`: match `'--stop-when-empty'` (with dashes) in the `in_array` check, or use a proper `hasFlagSet()` method.

- **Not reported upstream** — related issues exist about worker behaviour (#9742, #9682, #9823) but none identify this specific parsing bug.
- **Impact:** Stale workers process jobs with old PHP code (opcache doesn't help — they loaded files at startup). Any code patches applied after workers start have no effect until workers are killed.
- **Fix applied:** Updated `blast-queue.sh` with three safeguards:
  1. Auto-kills stale `jobs.php work` processes before starting new ones
  2. Wraps workers in `timeout` (default 30 min) so they can't live forever
  3. Added `--kill` flag for manual cleanup (`blast-queue.sh --host=<your-server> --kill`)

### 22. `runScheduledTasks.php` throws PHP Fatal on every invocation (OJS 3.5) [needs-repro]

`runScheduledTasks.php` (the CLI cron entry point) triggers a `NotFoundHttpException` in `PKPRouter::getContext()` on every run. The call chain: `PKPScheduler::registerPluginSchedules()` → `PluginRegistry::loadAllPlugins()` → `PKPApplication::getEnabledProducts()` → `PKPRouter::getContext()`. `getContext()` expects an HTTP request to resolve the journal context, but there is no request in CLI mode.

```
PHP Fatal error: Uncaught Symfony\Component\HttpKernel\Exception\NotFoundHttpException
  in /var/www/html/lib/pkp/classes/core/PKPRouter.php:205
```

The error fires hourly (matching the OJS cron schedule). Despite the "Fatal", the scheduled tasks appear to complete — the heartbeat ping after `pkp-run-scheduled` succeeds. This suggests the exception may be caught by an error handler rather than being a true Fatal that halts execution. Needs reproduction on current `main` branch to confirm.

An earlier variant ([pkp/pkp-lib#6671](https://github.com/pkp/pkp-lib/issues/6671), "no router object when executing scheduled tasks") was fixed in January 2021 for OJS 3.3. The current issue may be a regression from the Slim→Laravel router migration in 3.5.

- **Not reported upstream** — likely introduced by the Slim→Laravel router migration.
- **Impact:** Log noise. One PHP Fatal per hour in the OJS container. No functional impact on scheduled tasks.
- **Workaround:** Excluded from the hourly monitoring check (`monitor-safe.sh`) — the grep now filters out `NotFoundHttpException.*PKPRouter` to avoid false alerts.

### 24. Scheduler `flock()` crash on cache lock file [ojs-bug]

OJS's web-based scheduler (`PKPScheduler::runWebBasedScheduleTaskRunner()`) crashes with a PHP Fatal when the file-based cache lock can't be acquired:

```
PHP Fatal error: Uncaught TypeError: flock(): Argument #1 ($stream) must be of type resource, false given
  in /var/www/html/lib/pkp/lib/vendor/laravel/framework/src/Illuminate/Filesystem/LockableFile.php:153
```

Call chain: `ScheduleServiceProvider` → `PKPScheduler::runWebBasedScheduleTaskRunner()` → `ScheduleTaskRunner::run()` → `CacheEventMutex::exists()` → `FileLock::acquire()` → `flock(false, ...)`. The underlying `fopen()` for the lock file returns `false` (likely a race condition or `/tmp` permissions issue), and `flock()` throws a TypeError on the non-resource.

Transient — fires occasionally (observed once per ~12 hours), self-resolves on next scheduler run. Same scheduler subsystem as issue #22 (both are web-based scheduler bugs from the Slim→Laravel migration).

- **Not reported upstream** — related to the same scheduler architecture as #22.
- **Impact:** Log noise. One PHP Fatal per occurrence. No functional impact on scheduled tasks.
- **Workaround:** Excluded from the hourly monitoring check (`monitor-safe.sh`) — the grep now filters out `flock().*LockableFile` to avoid false alerts.

### 25. `rebuildSearchIndex.php` queues jobs only — never indexes inline [by-design]

OJS 3.5's `tools/rebuildSearchIndex.php` dispatches one `UpdateSubmissionSearchIndexJob` per submission and returns. The actual indexing happens only when a queue worker (`jobs.php run`) processes those jobs. The tool's output says "scheduled" — not "completed" — for exactly this reason.

Consequence: any workflow that runs `rebuildSearchIndex.php` and then wipes the `jobs` table (e.g. `DELETE FROM jobs`) leaves the search index empty. Articles already indexed via the normal publish path (each `Publication::publish` dispatches its own indexing job) may still be searchable if those jobs later get processed by the cron scheduler, but the bulk rebuild produces no indexing work.

- **Impact:** Silent failure. Search returns no results (or only recently-published articles, picked up by the hourly scheduler cron). `submission_search_objects` stays empty or sparse. No error log.
- **Correct pattern:** always drain the queue after scheduling a rebuild:
  ```
  php tools/rebuildSearchIndex.php
  scripts/ojs/blast-queue.sh            # or jobs.php run --once in a loop
  ```
- **Regression history:** `backfill/html_pipeline/pipe7_import.sh` was changed in commit `82ba72d` (2026-03-31) from a drain loop to `DELETE FROM jobs`, with a misleading comment claiming "rebuildSearchIndex.php processes the core indexing inline". It doesn't. The live site ran with only the 2 most recent issues searchable from the March backfill until the bug was caught 2026-04-16. Monitoring didn't catch it because the check was `COUNT(*) > 0` — passed with any non-zero index. Fix: compare `COUNT(DISTINCT submission_id)` against the published submission count (now in `monitor-deep.sh`, `smoke-test.sh`, and `e2e/tests/wpojs-sync/ojs-search.spec.ts`).
- **Related gotcha:** pipe7's *pre-import* `DELETE FROM jobs` at line 266 is also destructive if legitimate pending jobs (DOI deposits, WPOJS sync, notifications) are queued. Out of scope for the #25 fix but worth auditing.

### 26. `recommendBySimilarity` query scales O(corpus × keyword-frequency) per article view [plugin-design]

The "Similar Articles" plugin (`plugins/generic/recommendBySimilarity`) runs a query on every article view that searches for other submissions matching any of up to 20 keywords extracted from the current article. For a thematically narrow journal whose corpus-wide keywords all match ≥50% of submissions, this query becomes catastrophically slow.

**Query shape** (from `APP\submission\Collector::searchPhrase()`):

```sql
SELECT s.* FROM submissions s WHERE s.submission_id IN (
  -- branch per keyword: keyword-index match OR title LIKE '%kw%' OR author LIKE '%kw%'
  SELECT ... FROM submission_search_objects ...       -- keyword arm
  UNION SELECT ... FROM publication_settings WHERE setting_value LIKE CONCAT('%', ?, '%')  -- title arm (leading wildcard = full scan)
  UNION SELECT ... FROM author_settings WHERE setting_value LIKE CONCAT('%', ?, '%')       -- author arm
) AND s.submission_id NOT IN (?)  -- self-exclude
ORDER BY match-count DESC LIMIT 6
```

Plus two correlated subqueries in the `ORDER BY` that each repeat the keyword-index join for ranking.

**Why it silently works for most journals:** keyword frequency is roughly Zipfian across a broad corpus — no single keyword matches more than a few percent of articles. The OR-branches stay small. Index lookups are quick.

**Why it failed here:** a journal-for-one-topic has its topic word in every article. For this journal's post-backfill state:

| keyword      | matching submissions | corpus share |
|--------------|---------------------:|-------------:|
| existential  | 1400                 | 100%         |
| analysis     | 1400                 | 100%         |
| world        | 1167                 |  83%         |
| experience   | 1143                 |  82%         |

Once the plugin's 20-keyword set includes "existential" or "analysis" (inevitable here), the query scans ~100% of the corpus three times over (three OR arms × keyword-index join × ranking subqueries). Measured: 60-2000 seconds per call. With 8 Apache workers, a dozen concurrent article views saturates the pool — site hangs.

**Why this was latent until 2026-04-16:** issue #25 left the search index near-empty (30 of 1400 submissions indexed). The `EXISTS (submission_search_objects ...)` subqueries returned empty fast regardless of keyword frequency. Fixing #25 populated the index, which unveiled this. Load went from ~0.5 to 14.8 within minutes of the index rebuild.

- **Not reported upstream** — the plugin behaviour is correct by design; it only pathologises on thematically-narrow corpora.
- **Impact:** catastrophic on journals like ours. Whole-site hang while apache workers wait on DB.
- **Mitigation (active):** stock plugin disabled via `UPDATE plugin_settings SET setting_value = 0 WHERE plugin_name = 'recommendbysimilarityplugin'`. Site responsive again.
- **Detection:** `monitor-deep.sh` now runs a slow-query probe (any `information_schema.processlist` entry >10s fails). `content-check.sh` now sweeps 10 random article pages with a 5s timeout. The slow-query probe would have caught this outage in under a minute. (An initial keyword-skew warning check was added and removed — the query itself took ~2:46 because of the same `GROUP BY k.keyword_text` over 4.2M rows that made the stock plugin slow; once `similarArticles` took over, it was redundant with the slow-query probe and no longer worth its cost.)
- **Replacement plugin:** `plugins/similar-articles/` (`similarArticles` in OJS). Renders the same "Related articles" sidebar from a pre-computed cache table (`similar_articles`). Similarity is computed offline by `scripts/ojs/build_similar_articles.py` — sklearn TF-IDF (min_df=2, max_df=0.5, 1-2-grams) over editor-curated keywords×3 + title + abstract, cosine similarity, top 5 per article. max_df=0.5 auto-filters corpus-wide tokens like "existential" that broke the stock plugin. Render path is a primary-key cache lookup — sub-millisecond, no corpus-skew exposure. Monitor-deep.sh now has cache coverage (fail <50%, warn <80%) and staleness (fail >168h, warn >48h) checks.

## Payments

### 17. Manual Payment plugin has no admin approval UI [known-gap]

The Manual Payment plugin (`plugins/paymethod/manual`) lets non-subscribers request article purchases: the buyer sees a "Send notification of payment" button, which emails the journal manager. But there is no admin UI to approve/fulfil the payment. The payment appears in Payments > Payments as a log entry, but with no action button. The only way to complete a manual payment is direct DB insertion into `completed_payments` or calling `OJSPaymentManager::fulfillQueuedPayment()` programmatically.

Additionally, a related bug ([pkp/pkp-lib#11121](https://github.com/pkp/pkp-lib/issues/11121), open) shows that in OJS 3.4 and 3.5, the "Send notification of payment" button itself throws an HTTP 500 due to a missing "From" header in the email. This makes the Manual Payment plugin essentially non-functional in current versions.

- **Confirmed upstream:** [pkp/pkp-lib#6136](https://github.com/pkp/pkp-lib/issues/6136) — open since 2020, unresolved. The issue confirms the plugin has no completion/approval mechanism. Six years without a fix.
- **Impact:** Cannot use Manual Payment as a fallback when PayPal is unavailable.
- **Workaround:** Use PayPal plugin (which auto-completes payments on callback).

### 18. PayPal sandbox rejects all payments for non-US developer accounts [third-party]

Two separate issues confirmed:

**a) Expired sandbox credentials (fixed 2026-03-21).** Original sandbox app credentials returned 401 "Authentication failed". Fixed by creating a fresh sandbox app at developer.paypal.com.

**b) Sandbox rejects buyer approval (confirmed 2026-03-21).** With valid credentials, the PayPal REST API successfully creates the payment order and redirects to `sandbox.paypal.com`. The buyer logs in, the "Pay" button appears briefly, then rejects with "This transaction has been declined in order to comply with international regulations." Tested and fails with:

- UK and US sandbox buyer accounts
- GBP and USD currencies
- UK IP and US VPN
- Via OJS and via standalone script (`scripts/dev/test-paypal.js`) — confirms the issue is 100% PayPal sandbox, not the integration

Upstream reports confirm the problem affects non-US accounts generally (Japan, Germany also confirmed), not just UK.

- **Reported upstream:** [paypal/paypal-js#511](https://github.com/paypal/paypal-js/issues/511) — closed for inactivity (2025-03-26). PayPal's Checkout team could not reproduce. Related: [woocommerce/woocommerce-paypal-payments#3608](https://github.com/woocommerce/woocommerce-paypal-payments/issues/3608) (same error message).
- **Standalone reproduction:** `node scripts/dev/test-paypal.js` — creates a PayPal order via REST API, opens sandbox checkout in Playwright, logs in as buyer, captures the rejection. No OJS involved. Screenshot saved to `scripts/paypal-sandbox-result.png`.
- **Impact:** Cannot complete the PayPal purchase flow from a developer environment. The OJS integration is correct (creates order, redirects to PayPal, return URL wired up), but the sandbox buyer approval step is blocked.
- **Resolution:** PayPal abandoned in favour of Stripe (2026-03-22). See `private/stripe-live-checklist.md`.
