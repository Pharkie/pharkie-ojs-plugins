# Plan Review Findings

> **PARTIALLY SUPERSEDED (2026-03-08):** Findings about welcome emails, "set your password" flow, 500ms fixed delays, and email rate limiting no longer apply. These were replaced by WP password hash sync and adaptive throttling. Other findings (security, data integrity, testing) remain relevant.

Last updated: 2026-02-17

Six-perspective review of the push-sync plan: senior developer, QA engineer, UX expert, business strategist, security consultant, ops/SRE. Findings deduplicated and grouped by theme.

For the plan itself, see [`plan.md`](./plan.md). For the task checklist, see [`TODO.md`](../../TODO.md).

---

## Critical: Resolve before writing any code

### 1. OJS user creation API is unverified

The entire integration depends on creating OJS user accounts. The swagger spec shows read-only user endpoints. No one has tested `POST /api/v1/users` against a real OJS instance. If it doesn't exist, the OJS plugin must implement full user creation via internal PHP classes — a significantly larger scope.

**Action:** Test `POST /api/v1/users` on the real OJS 3.5 staging instance as the very first task. Record the result in `ojs-sync-plugin-api.md`. Gate all plugin design on the answer.

*Flagged by: senior dev, QA*

### 2. No API contract for the custom OJS endpoints

The plan says "REST endpoints for subscription CRUD" but never defines: URL paths, HTTP methods, request/response schemas, error codes, or idempotency guarantees. Both plugins cannot be independently built or tested without this contract.

**Action:** Write an endpoint specification before implementation. At minimum:
- `POST /find-or-create-user` — fields, response, uniqueness handling
- `POST /subscriptions` — fields, upsert vs insert, response
- `PUT /subscriptions/{id}/expire` — by ID or by email?
- `GET /subscriptions` — filter params
- `POST /send-welcome-email` — per-user, with dedup flag
- `/ping` for reachability, `/preflight` for compatibility check

*Flagged by: senior dev, security*

### 3. Apache Authorization header stripping not verified

`CGIPassAuth on` is required for Bearer token auth with Apache+PHP-FPM. If not set, every API call silently returns 401. Neither the TODO nor launch sequence includes a verification step. The `?apiToken=` query param fallback leaks the key into access logs.

**Action:** Add an explicit Phase 0 verification step: `curl` the OJS API with a Bearer token from the WP server's IP. Fix `CGIPassAuth` or confirm it works. Never use the query param fallback in production.

*Flagged by: senior dev, security, ops/SRE*

### 4. Non-expiring subscription end dates not decided

WCS returns `0` for non-expiring subscriptions. The plan says "use a far-future date or match OJS subscription type duration" but defers the decision. If WP sends `0` and the OJS plugin doesn't handle it, subscriptions could expire immediately or fail to insert.

**Action:** Decide now: use `date_end = NULL` (OJS supports non-expiring via `subscription_types.non_expiring`) or a far-future sentinel date (2099-12-31). Document as a resolved decision.

*Flagged by: QA, senior dev*

---

## High: Resolve before launch

### 5. No idempotency on user creation or subscription creation

If `status_active` fires twice, or bulk sync is interrupted and restarted, or UM hooks double-fire alongside WCS hooks, the OJS endpoints could create duplicate users or subscriptions. No deduplication logic is specified.

**Action:** The OJS plugin must:
- `find-or-create-user`: wrap lookup+insert in a transaction; return existing user if email already exists
- `create/renew subscription`: check for existing `(user_id, journal_id)` via `getByUserIdForJournal()`; upsert, don't blind-insert
- All endpoints must be safe to call repeatedly with the same payload

*Flagged by: QA, senior dev, security*

### 6. Sync calls are synchronous on the WCS checkout hook

The WP plugin makes HTTP calls to OJS within the WCS hook callback. If OJS is slow or down, the WP request hangs, potentially blocking checkout. No async dispatch is specified.

**Action:** Use `wp_schedule_single_event()` to dispatch OJS API calls via WP Cron, not inline. Log the sync intent to a local table at hook time; process it asynchronously. This also provides natural retry capability.

*Flagged by: senior dev, ops/SRE*

### 7. Retry logic is undefined

The plan says "retry logic for failed API calls" but specifies no strategy. Without backoff, retries during OJS downtime can thundering-herd the server. Without a retry cap, failures loop indefinitely.

**Action:** Implement: retries via Action Scheduler (up to 5 attempts at 5-minute intervals). Permanent failures (4xx except 404/429) trigger immediate admin alert without retry. All retries depend on idempotent OJS endpoints (#5).

*Flagged by: ops/SRE, security, QA*

### 8. Nightly reconciliation and failure alerts must be Phase 1, not Phase 2

If a sync call fails silently and no reconciliation exists, lapsed members retain access indefinitely (revenue leak) and active members lose access indefinitely (support burden). Email alerts on failure are also deferred to Phase 2, meaning failures are invisible unless someone checks WP admin logs.

**Action:** Move to Phase 1:
- Daily WP Cron job: compare active WCS subscriptions vs OJS, retry any that are out of sync
- `wp_mail()` alert to admin on any sync failure after retries exhausted
- Daily digest email: count of failures in the last 24 hours

*Flagged by: business, QA, ops/SRE, senior dev*

### 9. Bulk sync needs batching, rate limiting, and resume capability

500 sequential HTTP calls with no delays will strain OJS. 500 welcome emails in a burst will trigger spam filters. Partial failure leaves an inconsistent state with no way to resume.

**Action:**
- Run via WP-CLI only (not admin button) to avoid HTTP timeouts
- ~~Add 500ms delay between API calls~~ → replaced with load-based backpressure: OJS self-monitors response times and returns 429 with `Retry-After` when under pressure. WP adaptive throttling reads the header and backs off. No fixed delays. Result: 684 users in ~40s on Hetzner.
- Send welcome emails in batches of 50/hour max
- Write per-user success/fail log that persists across runs; skip already-synced users on re-run
- Track "welcome email sent" flag per user to prevent duplicate emails
- Smoke test with 10 users before running full 500
- Verify OJS server specs and email config (SPF/DKIM/DMARC) before bulk send

*Flagged by: ops/SRE, QA, security, business*

### 10. Email change in WP breaks the email-as-key model

If a member changes their WP email after bulk sync, the next sync call uses the new email, creates a second OJS account, and the old OJS account is orphaned with the subscription still attached.

**Action:** Hook into WP's `profile_update` action. When email changes, call OJS to update the email on the existing account (found by old email). The OJS plugin needs an update-user-email endpoint. Also: enforce WP email uniqueness and email change confirmation.

*Flagged by: QA, UX, security*

### 11. Welcome email and password setup flow needs proper design

The welcome email is the primary onboarding path for 500 members. If it looks like phishing, has an expired token, or explains the email-key constraint at the wrong moment, members won't complete it. The "set your password" login page prompt must be permanent, not just a launch feature.

**Action:**
- Draft actual email copy (not just bullet points) — open with what the member gets, single CTA, include which email address was used
- Remove email-change instructions from the welcome email (put in FAQ instead)
- Check OJS reset token expiry; extend to 7 days minimum for bulk send
- Test the expired-token error page in OJS
- Send from a recognisable SEA address with proper SPF/DKIM
- Login page "Set your password" prompt is permanent and accessible (WCAG 2.1 AA)

*Flagged by: UX, business, security*

### 12. No member-facing error states or support path

When sync fails, the member sees a paywall with purchase options and no explanation. No "contact support" message. No WP dashboard status. No staff runbook.

**Action:**
- OJS paywall for logged-in users with no subscription: "SEA member? Contact [support email]"
- WP member dashboard: "Your journal access is active / not yet set up / contact us"
- Write a 1-page support runbook: how to check WP subscription status, check OJS subscription, manually trigger sync, handle email mismatch
- Ensure WP sync logs are readable by non-technical admin staff

*Flagged by: UX, business*

### 13. Cross-links between WP and OJS

Members have no bridge between the two systems. The "Access journal" link on WP dashboard is deferred to Phase 3, but it's the single highest-value low-effort UX improvement.

**Action:** Move to Phase 1:
- WP member dashboard: "Access Existential Analysis" link to OJS
- OJS footer/sidebar: "Your access is provided by your SEA membership. Manage at [WP URL]"

*Flagged by: UX, business*

### 14. Security: API key storage, scope, and rotation

The OJS API key is stored plaintext in `wp_options`, scoped to a Site Administrator account, with no rotation procedure. Compromise grants full OJS access.

**Action:**
- Store API key as a `wp-config.php` constant, not in the database
- Create a dedicated OJS service account with minimum required role (not Site Admin)
- Add IP allowlisting on the OJS plugin endpoints (WP server IP only)
- Document key rotation procedure
- WP plugin must alert admin on 401 responses from OJS
- Enforce HTTPS only; reject non-HTTPS OJS URLs in settings

*Flagged by: security*

### 15. Security: Input validation and GDPR

No input validation spec for OJS endpoints. No GDPR deletion propagation from WP to OJS.

**Action:**
- OJS plugin: validate email format, cast IDs to integers, enforce max string lengths, return HTTP 400 for invalid input
- Add a delete-user endpoint to the OJS plugin; call it when a WP account is deleted (GDPR erasure)
- Define WP-OJS data controller/processor relationship in writing
- Run static analysis (PHPStan) on both plugins before production deploy

*Flagged by: security*

### 16. OJS 3.5 upgrade needs explicit go/no-go criteria and rollback runbook

The upgrade is the single biggest risk. The plan says "back up everything" but has no rollback procedure, no acceptance criteria for a successful upgrade, and no staging environment confirmed.

**Action:**
- Confirm staging environment exists (or create one)
- Write step-by-step rollback runbook: restore DB, restore files, verify 3.4 is back
- Test the rollback procedure on staging
- Define acceptance criteria: paywall works, purchase flow works, existing content intact, admin UI works
- Define go/no-go threshold: if staging upgrade takes >X days to stabilise, escalate Janeway decision
- Run a test non-member purchase after upgrade to confirm payment flow

*Flagged by: senior dev, business, ops/SRE, QA*

---

## Medium: Address during implementation

### 17. New members after launch may not get welcome email

The welcome email is described in the bulk sync section only. It's unclear whether the ongoing `status_active` hook also triggers it. If not, post-launch members get an OJS account with no password and no notification.

**Action:** Confirm the `status_active` hook path triggers the same welcome email. Add explicitly to Phase 1 checklist.

*Flagged by: UX*

### 18. Multiple WCS subscriptions per user — "longest wins" logic undefined

The plan identifies the edge case but doesn't specify the resolution logic.

**Action:** Rule: if any active WCS subscription exists, OJS access is active. OJS end date = latest end date across all active WCS subscriptions. If subscription type IDs differ, use a single default "SEA member" type (plan already says all tiers grant the same access).

*Flagged by: QA, UX, senior dev*

### 19. Multi-journal scope not specified

OJS has 2 journals. Subscriptions are per-journal. The WP settings page stores subscription type mapping but not journal ID.

**Action:** Clarify with SEA: one journal or two? Add journal ID(s) to WP plugin settings. If all members get access to all journals, iterate over configured journal IDs during sync.

*Flagged by: senior dev*

### 20. UM secondary hooks may double-fire or fire inappropriately

Both WCS and UM hooks trigger OJS sync. If both fire for the same event, duplicate calls hit OJS.

**Action:** Either remove UM hooks entirely (simpler) or add deduplication: record last sync time per WP user in `wp_usermeta`, skip if same action within last 60 seconds. If UM hooks are kept, check that the new role is specifically a membership role.

*Flagged by: senior dev*

### 21. WCS `on-hold` immediately expires access with no grace period

Payment failure → `on-hold` → OJS access immediately revoked. Member receives no notification about journal access loss.

**Action:** Decide: immediate expiry or 7-day grace period? If immediate, document as a design decision. Either way, the WP plugin should send a "journal access paused" notification, and a "journal access restored" message when status returns to active.

*Flagged by: QA, UX*

### 22. Structured sync logging

"Log all sync operations" is specified but no schema is defined. Diagnosing "why can't member X access OJS?" requires searching unstructured text.

**Action:** Use a custom WP DB table: `id`, `wp_user_id`, `email`, `action`, `status`, `ojs_response_code`, `ojs_response_body`, `attempt_count`, `created_at`. Admin list table with filters.

*Flagged by: ops/SRE*

### 23. Launch communication sequence

No plan for when/how to tell members about the journal. The welcome email is an account setup email, not an announcement. If the announcement goes out before bulk sync completes, members arrive at OJS before accounts exist.

**Action:** Define sequence: (1) complete bulk sync, verify counts → (2) send welcome emails → (3) send member announcement via SEA's normal channel (newsletter/email). The announcement should say "check your email for instructions" not "visit the journal now."

*Flagged by: business, UX*

### 24. Consider disabling OJS self-registration

If non-members can self-register on OJS with any email, they could create an account with a member's email before the sync runs, causing a mismatch. Disabling self-registration prevents this.

**Action:** Discuss with SEA. If the paywall handles non-member purchases without requiring registration, disable self-registration.

*Flagged by: business, security*

### 25. Success metrics

No definition of what "working" looks like post-launch.

**Action:** Track from launch: (1) active WCS subscriptions vs active OJS subscriptions (should match), (2) OJS logins per month, (3) sync failures per week, (4) support contacts about journal access.

*Flagged by: business*

---

## Low: Nice to have / deferred

### 26. Code review and static analysis before deploy

Both plugins are written on a tight budget. A vulnerability in the OJS plugin exposes all editorial data.

**Action:** Second-pair-of-eyes review + PHPStan before production. No `eval()`, no raw SQL, no dynamic file includes.

*Flagged by: security*

### 27. OJS plugin upgrade testing procedure

Each OJS upgrade could break the custom plugin.

**Action:** Document the upgrade testing procedure. Allocate a small annual budget for maintenance.

*Flagged by: business*

### 28. OJS server capacity unknown

No documented information about OJS server specs, PHP limits, or concurrent connection capacity.

**Action:** Before bulk sync, document OJS server spec and run a 10-user smoke test.

*Flagged by: ops/SRE*

### 29. OJS password reset token mechanism

The plan calls for generating reset tokens via OJS internals. These are application-layer functions that may not be stable across versions.

**Action:** Locate the specific OJS 3.5 class for password reset tokens. Verify it's accessible from plugin context. Document in `ojs-sync-plugin-api.md`.

*Flagged by: senior dev*
