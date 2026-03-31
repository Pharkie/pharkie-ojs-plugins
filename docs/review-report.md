# Full Codebase Review Report

**Date:** 2026-03-08
**Reviewers:** 5-agent panel (Security, Code Quality, Test Coverage, Documentation, Production Readiness)
**Scope:** Entire codebase, docs, tests, operational scripts

---

## Executive Summary

**Overall verdict: PRODUCTION-READY with 1 critical fix required.**

The codebase demonstrates excellent engineering: idempotent sync operations, dual-layer API auth, comprehensive admin UI, adaptive throttling, and strong error handling. One critical security issue (password hashes in Action Scheduler queue) must be fixed before production. Documentation needs updating for the password sync feature added in recent commits.

| Severity | Count | Summary |
|----------|-------|---------|
| **Critical** | 1 | Password hashes stored in job queue |
| **Important** | 8 | Docs gaps, test gaps, operational items |
| **Minor** | 8 | Polish, log retention, UX improvements |

---

## CRITICAL (must fix before production)

### C1. Password hashes stored in Action Scheduler job queue

**Agent:** Security
**Severity:** CRITICAL
**Files:**
- `plugins/wpojs-sync/includes/class-wpojs-hooks.php:183-186`
- `plugins/wpojs-sync/includes/class-wpojs-sync.php` (`handle_password_change`)

**Problem:** `schedule_password_sync()` passes the WP password hash as an Action Scheduler argument. This hash is serialized and stored in `wp_actionscheduler_actions.args` — visible to anyone with DB access, included in backups, and queryable via WP-CLI.

```php
as_schedule_single_action( time(), 'wpojs_sync_password_change', array( array(
    'wp_user_id'    => $user_id,
    'password_hash' => $password_hash,  // STORED IN QUEUE
) ), 'wpojs-sync' );
```

**Fix:** Store only `wp_user_id` in the queue. Retrieve the current password hash from `wp_users.user_pass` at processing time in `handle_password_change()`. This matches the existing pattern used by `handle_activate()` and is actually more correct — it automatically picks up the latest hash if the user changes their password again before the job runs.

**Changes needed:**
1. `class-wpojs-hooks.php`: Remove `$password_hash` parameter from `schedule_password_sync()`. Only pass `wp_user_id` to `as_schedule_single_action()`.
2. `class-wpojs-sync.php`: In `handle_password_change()`, retrieve hash via `get_userdata($wp_user_id)->user_pass` instead of reading from `$args['password_hash']`.
3. Remove the now-unnecessary staleness check (comparing queued hash vs current hash) — the handler always uses the latest hash.

---

## IMPORTANT (should fix before or soon after launch)

### I1. Password sync feature undocumented in plan.md

**Agent:** Documentation
**Files:** `private/plan.md` lines 85-97, 127, 134-143

Three gaps from the recently-added password sync feature:
1. **Missing endpoint:** `PUT /users/{userId}/password` absent from endpoint spec table
2. **Missing hooks:** `profile_update` (password changed) and `after_password_reset` absent from hook mapping table
3. **Missing action type:** `password_change` absent from sync log action column values

**Fix:** Add 1 row to endpoint table, 2 rows to hook table, add `password_change` to action column list, add password_change sequence description to queue processor section.

### I2. Test count outdated (56 → 61)

**Agent:** Documentation
**Files:** `private/plan.md:447`, `TODO.md:80`

Documentation says "56 tests across 15 spec files" but actual count is 61 tests (password-sync and other additions).

**Fix:** Update both files to say 61.

### I3. Smoke test lacks password login verification

**Agent:** Test Coverage
**File:** `scripts/monitoring/smoke-test.sh`

The smoke test creates a user, syncs them to OJS, verifies the user exists, then deletes. But it never verifies the user can **log in to OJS with their WP password** — the #1 user-facing feature.

**Fix:** After the sync round-trip (line ~220), add an OJS login test via curl (POST to `/login/signIn`, verify redirect to dashboard).

### I4. Only 1 of 6 WC products tested

**Agent:** Test Coverage
**Files:** `e2e/helpers/wp.ts:120`, all e2e tests

All e2e tests use a single product SKU (`wpojs-uk-no-listing`). Production has 6 WC products mapped to OJS subscription types. Broken mappings for products 2-6 would go undetected.

**Fix:** Either parametrize an existing test across all 6 products, or add a smoke test that cycles through each product-to-type mapping.

### I5. Bulk sync has no resume checkpoint

**Agent:** Code Quality
**File:** `plugins/wpojs-sync/includes/cli/class-wpojs-cli.php:138-231`

If a bulk sync of 10,000+ members is interrupted mid-process, there's no way to resume — re-running re-processes all members. This is safe (idempotent) but wasteful.

**Fix:** Track last-processed user ID in a transient. Add `--resume` flag to skip already-synced users. Low urgency at current scale (684 members, 101 seconds).

### I6. No startup config validation

**Agent:** Production Readiness
**File:** `plugins/wpojs-sync/includes/class-wpojs-activator.php`

If OJS URL is blank, API key is missing, or subscription type mapping is empty, the plugin activates silently and sync operations fail with confusing errors.

**Fix:** Add activation check that validates required config (OJS URL, API key constant, at least one type mapping) and shows admin notice if incomplete. ~15 min effort.

### I7. Email change retry creates potential duplicates

**Agent:** Production Readiness
**File:** `plugins/wpojs-sync/includes/admin/class-wpojs-log-actions.php:152-153`

If an `email_change` sync action fails and is retried via the admin UI, the retry logic converts it to a full `activate` (because the log doesn't store old/new email separately). This could create a duplicate OJS account if the old-email account still exists.

**Fix:** Document this limitation in the support runbook. Consider preventing email_change retries entirely (require manual resolution).

### I8. Smoke test missing status change, email change, and GDPR verification

**Agent:** Test Coverage
**File:** `scripts/monitoring/smoke-test.sh`

The smoke test only tests the create path. It doesn't verify:
- Subscription expiry/cancellation
- Email change propagation
- GDPR anonymisation after user deletion (currently deletes but doesn't assert OJS state)

**Fix:** Extend the existing smoke test round-trip to include at least one status change and verify OJS anonymisation after deletion.

---

## MINOR (nice to have)

### M1. Log retention too short
**Agent:** Production Readiness
**Files:** `class-wpojs-cron.php` (90 days), `WpojsApiLog.php` (30 days)
**Fix:** Extend to 180 days (WP) and 90 days (OJS). Change 2 numbers.

### M2. No post-deployment health check in deploy script
**Agent:** Production Readiness
**File:** `scripts/infra/deploy.sh`
**Fix:** Add `wp ojs-sync test-connection` after setup completes.

### M3. Admin alert specification lacks detail
**Agent:** Documentation
**File:** `private/plan.md:67-71`
**Fix:** Document which HTTP codes trigger permanent failures, email template, retry exhaustion rules.

### M4. `usleep(500ms)` workaround in `find_after_fail()`
**Agent:** Code Quality
**File:** `plugins/wpojs-sync/includes/class-wpojs-sync.php:367`
**Fix:** Replace with idempotency tokens on OJS API (future optimization).

### M5. Response body only visible via hover tooltip in sync log
**Agent:** Production Readiness
**File:** `plugins/wpojs-sync/includes/admin/class-wpojs-log-page.php:242`
**Fix:** Add modal/expander for full response body.

### M6. OJS plugin doesn't declare 3.5+ version requirement
**Agent:** Production Readiness
**File:** `WpojsSubscriptionApiPlugin.php`
**Fix:** Add version requirement to plugin manifest.

### M7. Connection status cache can mask transient OJS outages
**Agent:** Production Readiness
**File:** `plugins/wpojs-sync/includes/admin/class-wpojs-settings.php:255`
**Fix:** Add manual "Refresh" button next to connection status.

### M8. No manual reconciliation trigger in admin UI
**Agent:** Production Readiness
**Fix:** Add "Run Reconciliation Now" button (currently CLI-only via `wp ojs-sync reconcile`).

---

## Verified Strengths (no action needed)

These areas were thoroughly reviewed and found to be solid:

| Area | Assessment |
|------|-----------|
| **API Authentication** | Dual-layer (IP allowlist + Bearer token), constant-time comparison, REMOTE_ADDR (not X-Forwarded-For) |
| **SQL Injection** | All queries parameterized across both plugins |
| **XSS Prevention** | All admin page output properly escaped (`esc_html`, `esc_attr`) |
| **CSRF Protection** | Nonces on all admin AJAX endpoints |
| **Idempotency** | All 5 sync handlers verified safe to retry |
| **Race Conditions** | Concurrent find-or-create, duplicate key, email/password staleness — all handled |
| **Error Handling** | All `wp_remote_*` calls checked for `WP_Error`, JSON parsing has fallbacks |
| **GDPR Compliance** | User deletion anonymises logs, disables OJS account, cascading deletes |
| **Adaptive Throttling** | Response-time-based delays + 429 Retry-After + 5xx backoff |
| **Admin UI** | Health cards, filterable log, retry mechanism, connection diagnostics |
| **Daily Reconciliation** | Drift detection and correction, catches missed events |
| **Eliminated Approaches** | Consistently marked across all documentation |
| **Internal Doc Links** | All 37+ markdown links verified resolving |
| **WP-CLI Commands** | All 5 subcommands match documentation |
| **Support Runbook** | Matches actual admin UI and CLI commands |
| **Test Cleanup** | Consistent `afterAll` blocks, minimal state leakage |
| **Test Infrastructure** | Robust helpers, no ordering dependencies, explicit timeouts |

---

## Action Plan

### Before Production Launch

| # | Item | Effort | Priority |
|---|------|--------|----------|
| 1 | Fix C1: Remove password hash from AS queue | 30 min | **CRITICAL** |
| 2 | Fix I1: Document password sync in plan.md | 20 min | Important |
| 3 | Fix I2: Update test count (56 → 61) | 5 min | Important |
| 4 | Fix I3: Add password login to smoke test | 30 min | Important |
| 5 | Fix I6: Add startup config validation | 15 min | Important |
| 6 | Fix M1: Extend log retention | 5 min | Minor |

### Soon After Launch

| # | Item | Effort | Priority |
|---|------|--------|----------|
| 7 | Fix I4: Test all 6 WC products | 1-2 hours | Important |
| 8 | Fix I7: Document email change retry limitation | 20 min | Important |
| 9 | Fix I8: Extend smoke test coverage | 1 hour | Important |
| 10 | Fix M2: Add health check to deploy script | 10 min | Minor |

### When Convenient

| # | Item | Effort | Priority |
|---|------|--------|----------|
| 11 | Fix I5: Bulk sync resume checkpoint | 2-3 hours | Important |
| 12 | Fix M3-M8: Various polish items | 2-3 hours | Minor |
