# Implementation Plan: Push-sync

Last updated: 2026-02-20

WP pushes subscription changes to OJS via a custom plugin on each side. For how we arrived at this decision, see [`discovery.md`](../discovery.md). For the full review that shaped this plan, see [`review-findings.md`](./review-findings.md).

---

## How it works

```
Bulk sync (~700 existing members at launch)
  → WP-CLI command reads all active WCS subscriptions + users with manual member roles
  → For each member: calls OJS endpoint to find-or-create user by email (with WP password hash), then creates subscription
  → Members log into OJS with their existing WP password (no separate "set your password" step)
  → OJS custom hasher verifies the WP hash at login, then lazy-rehashes to native bcrypt (cost 12)
  → OJS paywall checks its own database → finds valid subscription → access granted

Member signs up / renews on WordPress (ongoing, after launch)
  → WP plugin fires on WooCommerce Subscription status change
  → Schedules sync action via Action Scheduler (not inline HTTP)
  → Action Scheduler calls OJS: find-or-create user + create/renew subscription
  → If OJS is down: retries via Action Scheduler (up to 5 attempts at 5-minute intervals); permanent failures (4xx) trigger immediate admin alert
  → Member visits OJS, logs in, hits paywalled content → access granted

Member lapses / cancels
  → WP plugin fires on status change → schedules expire action
  → Action Scheduler calls OJS: expire subscription
  → OJS paywall denies access → shows purchase options + "Member? Contact support"

Non-member visits paywalled content
  → No subscription exists in OJS
  → OJS shows standard purchase page (£3 / £25 / £18)
  → Completely unaffected by the integration
```

---

## What gets built

### OJS plugin (`wpojs-subscription-api`)

Installed in `plugins/generic/wpojsSubscriptionApi/`:

- Registers REST endpoints for subscription CRUD (see [endpoint spec](#ojs-endpoint-spec) below)
- All endpoints are **idempotent** — safe to call repeatedly with the same payload
- Uses OJS's own `IndividualSubscriptionDAO` internally (classes already exist and are well-tested)
- Authenticated via OJS API key (Bearer token) on a **dedicated service account** (not Site Admin)
- **Role-based authorization**: requires Journal Manager or Site Admin role (checked via `user_user_groups` DB query — defence-in-depth beyond IP allowlist)
- **IP allowlisting**: only accepts requests from the WP server's IP address (uses `REMOTE_ADDR` directly, not `$request->ip()` which trusts `X-Forwarded-For`)
- **Input validation**: email format, integer casting on IDs, max string lengths, HTTP 400 on invalid input
- No modifications to OJS core code — standard plugin, dropped into a folder
- Requires OJS 3.5+ for the clean plugin API pattern ([pkp-lib #9434](https://github.com/pkp/pkp-lib/issues/9434))
- Password hash sync: stores WP bcrypt hash directly, custom hasher (`WpCompatibleHasher`) verifies at login, lazy-rehashes to native bcrypt cost 12
- UI messages via plugin hooks: login hint, paywall hint for non-subscribers, site footer. Config: `[wpojs] wp_member_url`, `support_email`
- Delete-user endpoint for GDPR erasure propagation from WP
- **API log auto-cleanup**: old log entries (>30 days) are automatically deleted during API requests (at most once per hour). Manual cleanup also runs when an admin opens the status page.

### WP plugin (`wpojs-sync`)

Installed like any WP plugin:

- Settings page: OJS URL (HTTPS enforced), subscription type ID mapping (WooCommerce Product → OJS Subscription Type), journal ID(s), "Test connection" button
- API key stored as `wp-config.php` constant (`WPOJS_API_KEY`), not in the database
- Hooks into **WooCommerce Subscriptions** lifecycle events (`status_active`, `status_expired`, `status_cancelled`, `status_on-hold`) as primary triggers
- **Async dispatch**: hooks schedule sync actions via Action Scheduler (not inline HTTP calls)
- **Retry logic**: delegates to Action Scheduler (up to 5 attempts at 5-minute intervals); permanent failures (4xx except 404/429) trigger immediate admin alert without retry
- **Email change hook**: detects WP email changes, calls OJS to update the corresponding account
- Bulk sync command (**WP-CLI only**) for initial population and drift correction, with batching and resume
- **Structured sync log**: custom DB table with per-user success/fail records, searchable in WP admin
- **Admin email alerts**: immediate notification on sync failure after retries exhausted; daily digest of failure count
- **Daily reconciliation**: Action Scheduler job comparing active WCS subscriptions + manual member roles vs OJS, retrying any drift
- See [`wp-integration.md`](../wp-integration.md) for full hook details and code patterns

---

## OJS endpoint spec

All endpoints except `/ping` require Bearer token auth + Journal Manager or Site Admin role + IP allowlist. All are idempotent.

**Fragility note:** These endpoints use OJS internal PHP classes (Repo facade, DAOs, Validation), not a stable public API. OJS could rename, merge, or remove any of these in a future release without notice — every OJS plugin has this risk. Mitigation: the `/preflight` endpoint verifies every class and method the plugin depends on. The WP "Test connection" button calls `/ping` then `/preflight`. **Run `/preflight` after any OJS upgrade.**

**Journal context:** All endpoints are scoped to the journal in the URL path (e.g., `/api/v1/{journal-path}/wpojs/subscriptions`). There is no `journalId` parameter in request bodies — the journal is always derived from the URL context via OJS's `has.context` middleware.

| Method | Path | Request body | Response | Notes |
|---|---|---|---|---|
| `GET` | `/ping` | — | `200 {status: "ok"}` | **No auth, no IP check** — pure reachability probe. If ping succeeds but preflight returns 403, the IP is not allowlisted. |
| `GET` | `/preflight` | — | `200 {compatible: bool, checks: [...]}` | Verifies all PHP classes, methods, and DB tables the plugin depends on. Checks: `Repo::user()` methods, `Repo::userGroup()` methods, `Repo::emailTemplate()` methods, `IndividualSubscriptionDAO` methods, `SubscriptionTypeDAO`, `Validation` methods, `Core::getCurrentDate()`, `Role::ROLE_ID_READER`, `user_user_groups` table, `wpojs_api_log` table, subscription types exist for journal. |
| `GET` | `/users?email=` | — | `200 {found, userId, email, username, disabled}` or `200 {found: false}` | Read-only user lookup by email. No side effects. |
| `POST` | `/users/find-or-create` | `{email, firstName, lastName, passwordHash?}` | `200 {userId, created: bool}` | Finds existing user by email or creates new one with Reader role. `passwordHash`: optional WP password hash — if provided, stored directly on the new OJS user with `mustChangePassword: false`. OJS custom hasher (`WpCompatibleHasher`) verifies WP hashes at login and lazy-rehashes to native bcrypt (cost 12). Only applied on user creation; existing users' passwords are never overwritten. |
| `PUT` | `/users/{userId}/email` | `{newEmail}` | `200 {userId}` | Checks email uniqueness before updating (OJS doesn't enforce it in `edit()`). Returns `409` if email in use by another account. |
| `DELETE` | `/users/{userId}` | — | `200 {deleted: true, userId}` | GDPR erasure: anonymises all PII (email, username, name, affiliation, biography, orcid, url, phone, mailing address), disables account, expires any active subscription. Does **not** hard-delete (safe for accounts with submission history). |
| `POST` | `/subscriptions` | `{userId, typeId, dateStart, dateEnd}` | `200 {subscriptionId}` | Idempotent upsert. **Reactivation** (expired→active): applies all incoming values (typeId, dateStart, dateEnd). **Already active**: extends `dateEnd` only if later (prevents shortening from out-of-order events). `dateEnd: null` = non-expiring. Returns at most one subscription per user per journal. |
| `PUT` | `/subscriptions/{id}/expire` | — | `200 {subscriptionId}` | Sets status to `SUBSCRIPTION_STATUS_OTHER`. Idempotent. |
| `PUT` | `/subscriptions/expire-by-user/{userId}` | — | `200 {subscriptionId}` | Convenience: expires subscription by userId (saves WP plugin a lookup call). Returns `404` if no subscription found. |
| `GET` | `/subscriptions?email=&userId=` | — | `200 [{subscriptionId, userId, journalId, typeId, status, dateStart, dateEnd}]` | Returns array with at most one item per user per journal (OJS enforces one-subscription-per-user-per-journal). Returns `[]` if user or subscription not found. |
| `POST` | `/subscriptions/status-batch` | `{emails: ["a@b.com", ...]}` | `200 {results: {email: {active: bool, ...}}}` | Check subscription status for multiple users by email. Used by reconciliation to batch-query OJS. |
| `PUT` | `/users/{userId}/password` | `{passwordHash}` | `200 {userId}` | Update user password hash. Used by ongoing password sync when a member changes their WP password. WP plugin resolves the OJS userId first, then sends the new hash. Returns `404` if user not found. |

Error responses: `400` (invalid input), `401` (bad/missing auth), `403` (IP not allowed or insufficient role), `404` (not found), `409` (conflict), `500` (server error). All errors return `{error: "message"}`. Error messages never include internal details (exception messages, stack traces, IP addresses).

---

## WP plugin spec

### Configuration

| Setting | Storage | Notes |
|---|---|---|
| OJS base URL | Settings page (`wp_options`) | HTTPS enforced — reject `http://` URLs. Includes journal path, e.g. `https://journal.example.org/index.php/t1`. The API URL is `{base}/api/v1/wpojs/...` — the journal context is embedded in the URL path, not sent as a parameter. |
| API key | `wp-config.php` constant `WPOJS_API_KEY` | Never stored in database |
| Subscription type mapping | Settings page (`wp_options`) | WooCommerce Product ID → OJS Subscription Type ID |
| WP server IP | Display only on settings page | Shown so admin can configure OJS IP allowlist |

### Database tables

**Async queue: Action Scheduler** — sync actions are scheduled via `as_schedule_single_action()` (bundled with WooCommerce). No custom queue table — Action Scheduler manages state, retries, and execution. View queue status in WP Admin → Tools → Scheduled Actions, filter by group `wpojs-sync`.

**WP usermeta: `_wpojs_user_id`** — cached OJS userId per WP user, set on first successful `find-or-create`. Used by expire/email_change/delete actions to avoid an extra HTTP lookup. Removed on GDPR deletion.

**`wpojs_sync_log`** — audit trail

| Column | Type | Notes |
|---|---|---|
| `id` | BIGINT PK | Auto-increment |
| `wp_user_id` | BIGINT | |
| `email` | VARCHAR(255) | |
| `action` | VARCHAR(30) | `activate`, `expire`, `create_user`, `email_change`, `password_change`, `delete_user` |
| `status` | VARCHAR(10) | `success`, `fail` |
| `ojs_response_code` | SMALLINT | HTTP status from OJS |
| `ojs_response_body` | TEXT | Truncated response for debugging |
| `attempt_count` | TINYINT | How many attempts it took |
| `created_at` | DATETIME | |

### Hook → action mapping

| WP hook | Condition | OJS action |
|---|---|---|
| `woocommerce_subscription_status_active` | — | Queue: find-or-create user (with WP password hash) + create/renew subscription |
| `woocommerce_subscription_status_expired` | — | Queue: expire subscription |
| `woocommerce_subscription_status_cancelled` | — | Queue: expire subscription |
| `woocommerce_subscription_status_on-hold` | — | Queue: expire subscription (immediate, no grace period) |
| `profile_update` | Email changed | Queue: update OJS user email (old email → new email) |
| `after_password_reset` | — | Queue: push updated WP password hash to OJS |
| `profile_update` | Password changed | Queue: push updated WP password hash to OJS |
| `delete_user` / `deleted_user` | — | Queue: call OJS delete-user endpoint (GDPR) |

When a member has **multiple active WCS subscriptions**, the plugin resolves to one OJS action: active if any subscription is active, `date_end` = latest end date across all active subscriptions.

### Queue processor — API call sequences

Each queue action maps to a specific sequence of OJS API calls. The queue processor stores `ojs_user_id` in WP usermeta (`_wpojs_user_id`) after the first successful find-or-create, so subsequent actions can skip the lookup.

**`activate`** (new subscription or renewal):
1. `POST /users/find-or-create` — `{email, firstName, lastName, passwordHash}`. Returns `{userId, created}`. If `created: true` and `passwordHash` provided, WP hash stored directly with `mustChangePassword: false`. Store `userId` in usermeta.
2. `POST /subscriptions` — `{userId, typeId, dateStart, dateEnd}`. Uses the `userId` from step 1. `typeId` from settings mapping. `dateEnd` = latest across all active WCS subs for this user (or `null` for non-expiring).

**`expire`** (cancellation, expiry, on-hold):
1. Resolve OJS userId: check `_wpojs_user_id` usermeta first. If not cached, call `GET /users?email=` to look up. If user not found, log and skip (never synced).
2. `PUT /subscriptions/expire-by-user/{userId}` — single call, no need to look up subscriptionId separately. Returns `404` if no subscription (log and skip — subscription may have already been expired or never created).

**`email_change`**:
1. Resolve OJS userId: usermeta or `GET /users?email={oldEmail}`. If not found, log and skip.
2. `PUT /users/{userId}/email` — `{newEmail}`. Returns `409` if new email already in use on OJS (alert admin — manual resolution needed).

**`password_change`** (WP password update):
1. Triggered by `after_password_reset` or `profile_update` (when password changes).
2. `POST /password` — `{email, passwordHash}`. Sends the new WP password hash to OJS. Returns `404` if user not found (log and skip — never synced).

**`delete_user`** (GDPR):
1. Resolve OJS userId: usermeta or `GET /users?email=`. If not found, log and skip (nothing to delete).
2. `DELETE /users/{userId}` — anonymises all PII, disables account, expires subscription.
3. Clean up: remove `_wpojs_user_id` from usermeta.

**Error handling in queue processor:**
- `200`: success → mark completed, log response
- `400`: bad input → permanent fail (data issue, won't improve on retry), alert admin
- `401`/`403`: auth/IP/role failure → permanent fail (config issue, won't improve on retry), alert admin
- `404` on expire/email_change/delete: user or subscription doesn't exist on OJS → mark completed (nothing to do), log as warning
- `409`: conflict (email in use) → permanent fail, alert admin for manual resolution
- `500` or network error: transient → increment retry count, schedule next attempt
- Any other 4xx: permanent fail, alert admin

### WP-CLI commands

| Command | Description |
|---|---|
| `wp ojs-sync sync --bulk --dry-run` | Report what bulk sync would do without making changes |
| `wp ojs-sync sync --bulk --yes` | Run bulk sync: creates users + subscriptions with WP password hash (members can log in with existing WP password). Batched (50 users), adaptive throttling. Requires `--bulk` flag to prevent accidental full sync. Rejects unknown flags (e.g. `--user=` errors instead of silently running bulk). |
| `wp ojs-sync sync --member=<id or email>` | Sync a single member (sends WP password hash) |
| `wp ojs-sync reconcile` | Run reconciliation now (compare WCS ↔ OJS, retry drift) |
| `wp ojs-sync status` | Show sync stats: total synced, pending, failed, last reconciliation |
| `wp ojs-sync test-connection` | Hit OJS `/ping` (reachability, no auth) then `/preflight` (auth + IP + compatibility). Reports specific diagnostic: reachable but IP blocked, reachable but auth failed, reachable but incompatible, or all clear. |

### WP Cron schedule

| Event | Frequency | What it does |
|---|---|---|
| `wpojs_daily_reconcile` | Daily | Compare active WCS subscriptions vs OJS, queue any drift |
| `wpojs_daily_digest` | Daily | Email admin: failure count in last 24 hours (skip if zero) |
| `wpojs_log_cleanup` | Weekly | Prune old sync log entries |

Individual sync actions (activate, expire, email_change, delete_user) are processed by **Action Scheduler**, not WP Cron. View queue in WP Admin → Tools → Scheduled Actions, filter by group `wpojs-sync`.

### Admin pages

- **Settings**: OJS URL (includes journal path), subscription type mapping, test connection button, current WP server IP display
- **Sync log**: filterable list table (by status, date range, email search). Shows last 500 entries by default.

---

## Key decisions

| Decision | Detail |
|---|---|
| **Email is the key** | Same email required on both systems. No mapping table. WP email change hook propagates changes to OJS. |
| **Bulk push creates accounts** | Don't wait for members to self-register. Push user accounts + subscriptions from WP upfront (~700 existing members at launch). |
| **All tiers grant access** | Any active WCS subscription → OJS access. Multiple WooCommerce products exist but all grant journal access. If a member has multiple subscriptions, use the latest `date_end` across all of them. |
| **Password sync** | Both bulk sync and ongoing sync send WP password hash (bcrypt cost 10) to OJS — members log in with their existing WP password. OJS custom hasher (`WpCompatibleHasher`) verifies at login and lazy-rehashes to native bcrypt (cost 12). Rehash must happen at login because only the plaintext (which the user just typed) can be hashed at a new cost — you can't rehash a hash. After first login the stored hash is native OJS and no further rehashing occurs. Ongoing WP password changes are also pushed to OJS via Action Scheduler. No welcome emails needed. |
| **Content loaded gradually** | Launch with ~60 recent articles. Back issues loaded over time. |
| **Non-expiring subs** | WCS subscriptions with no end date → OJS subscription with `date_end = NULL` and `non_expiring` subscription type. |
| **Async sync dispatch** | WCS hooks schedule sync actions via Action Scheduler. No inline HTTP calls on checkout. |
| **Daily reconciliation at launch** | Not deferred. A daily Action Scheduler job compares WCS ↔ OJS and retries any drift. |
| **Dedicated OJS service account** | API key belongs to a purpose-built OJS account with **Journal Manager** role (minimum required — the OJS plugin enforces Manager or Site Admin). Not a human admin account. |

---

## Facts established

| Fact | Detail |
|---|---|
| Staging test | Connection test + bulk backfill successful (2026-03-02). WC Product 23042 → OJS Type 1 confirmed working. Remaining: test all 6 products, cancellation/expiry, password flow. |
| OJS version | Live: 3.4.0-9. Staging: 3.5.0.3 (upgraded 2026-02-19). |
| OJS admin access | Yes. Site Administrator level. Can install plugins. |
| WP membership stack | Ultimate Member 2.11.2, WooCommerce 10.5.2, WooCommerce Subscriptions 8.4.0, WooCommerce Memberships 1.27.5. WCS is authority on subscription status. |
| WC Memberships | Active on live site. Handles membership plans and role assignment between WCS and UM. Does not affect our sync — we hook WCS status events directly. |
| Active subscriptions | 698 (confirmed 2026-02-19). |
| WC subscription products | 6 products: IDs 1892, 1924, 1927, 23040, 23041, 23042. See [`wp-integration.md`](../wp-integration.md#woocommerce-subscription-products) for full table. |
| Membership tiers | All nine WP roles grant journal access (six standard, three manual/admin-assigned). |
| Membership role slugs | Standard: `um_custom_role_1` through `_6`. Manual: `um_custom_role_7`, `_8`, `_9`. See [`wp-integration.md`](../wp-integration.md#membership-roles) for full table. |
| Manual member roles | Admin-assigned (Exco/life members). Currently 1 member. Bypass WCS checkout — bulk sync and reconciliation must detect these via WP roles directly. |
| Non-standard access | Editorial board, reviewers, etc. managed manually in OJS admin UI. Not part of WP sync. |
| Hosting | Different servers. WP and OJS communicate over HTTP. |
| OJS state | Live: fresh install, admin logins only, ~60 test articles. Docker dev: 2 issues / 43 articles imported from live export. |
| OJS journals | One journal. Sync targets one journal ID. |
| OJS self-registration | Enabled. Non-members need it for paywall purchases. |
| OJS email | Transactional relay (Mailgun/SES/Postmark) required on OJS for password resets and editorial notifications. SPF/DKIM/DMARC must be configured. No longer a hard prerequisite for bulk sync (password hashes are synced instead of welcome emails). |
| Docker dev environment | Running. WP (localhost:8080) with 727 anonymized test users, WooCommerce, Ultimate Member. OJS (localhost:8081) with 2 issues, 43 articles from live export. See `docs/docker-setup.md`. |
| WP email uniqueness | Enforced at DB level. UM email changes require confirmation. |
| WP users total | 1,418 users. 695 in membership roles + 1 manual = ~696 active members. |

---

## Launch sequence

1. **Upgrade OJS 3.4 → 3.5** — the biggest risk. Significant breaking changes (Slim→Laravel, Vue 2→3). Staging upgraded to 3.5.0.3 (2026-02-19). Still need: verify acceptance criteria on staging, write rollback runbook, agree go/no-go threshold with SEA, then upgrade production.
2. **Verify OJS API prerequisites** — test Bearer token auth from WP server IP (`CGIPassAuth on`), test user creation API, confirm email config (SPF/DKIM/DMARC), document OJS server specs.
3. **Build and deploy OJS plugin** (`wpojs-subscription-api`) — code review + PHPStan before production. See [`non-docker-setup.md`](../non-docker-setup.md) for non-Docker setup steps (folder naming, API route mount, config).
4. **Build and deploy WP plugin** (`wpojs-sync`) — code review before production.
5. **Create OJS subscription type(s)** — at least one Individual subscription type must exist in OJS before sync can work. Go to OJS Admin → Subscriptions → Subscription Types → Create. Note the `type_id` for use in WP mapping. The preflight check will warn if none exist.
6. **Configure WP plugin mapping** — set OJS Base URL (with journal path), product mappings (WC Product → OJS Type), and optionally WordPress role-based access. All six WC products (IDs 1892, 1924, 1927, 23040, 23041, 23042) should be mapped.
7. **Smoke test** — end-to-end with 10 test users: create subscription → OJS account created → subscription active → paywall grants access → expire subscription → paywall denies access. Also test non-member purchase flow.
8. **Bulk sync ~700 existing members** — `wp ojs-sync sync --bulk --dry-run` then `wp ojs-sync sync --bulk --yes`. Creates users + subscriptions on OJS with WP password hashes. Members can immediately log into OJS with their existing WP password. Batched (50 at a time), adaptive throttling. Verify: `wp ojs-sync status` shows correct synced count, spot-check a few users in OJS admin, test OJS login with a WP password.
9. **OJS template changes** — DONE. Login hint ("Member? Log in with your membership email and password."), paywall hint for logged-in non-subscribers ("Contact support"), customisable member footer message (default: "Your journal access may be linked to your membership elsewhere"). Implemented via plugin hooks (`TemplateManager::display`, `Templates::Article::Footer::PageFooter`, `Templates::Common::Footer::PageFooter`). Messages configurable via `.env` and OJS plugin settings page.
10. **WP member dashboard** — DONE. "Access [Journal Name]" card on WooCommerce My Account page. Shows active/inactive status with expiry date.
11. **Member announcement** — via the organisation's normal channel (newsletter/email), sent only after steps 8-10 are confirmed working. "Visit the journal and log in with your membership email and password."

If the OJS 3.5 upgrade hits serious problems, fallback options are documented in [`discovery.md`](../discovery.md).

---

## Rollback procedures

### OJS 3.5 upgrade rollback

1. **Before upgrading**: take a full DB snapshot and filesystem backup of the OJS installation.
2. **If the upgrade fails**: restore the DB snapshot and filesystem backup. OJS 3.4 should come up cleanly.
3. **Go/no-go**: agree a threshold with the client before starting (e.g. "if any of these 5 acceptance criteria fail on staging, we don't upgrade production").

### OJS plugin rollback (`wpojs-subscription-api`)

1. Disable the plugin via OJS Admin → Website → Plugins → Generic → WP-OJS Subscription API → Disable.
2. The paywall continues working — subscriptions are real OJS subscription records and are independent of the plugin.
3. Sync stops (WP calls will return 404), but existing user accounts and subscriptions remain.
4. To fully remove: delete the `plugins/generic/wpojsSubscriptionApi/` directory.

### WP plugin rollback (`wpojs-sync`)

1. Deactivate via WP Admin → Plugins → WP-OJS Sync → Deactivate.
2. OJS accounts and subscriptions persist (they're in the OJS database). Sync simply stops.
3. Pending queue items are abandoned. Daily reconciliation and digest emails stop.
4. To fully remove: deactivate, then delete. Custom DB tables (`wpojs_sync_log`) are retained for diagnostics.

### Bulk sync partial failure

1. Bulk sync is idempotent — safe to re-run. `find-or-create` returns existing users, `create_subscription` upserts.
2. Check results: `wp ojs-sync status` shows synced count. Sync log shows per-user success/fail.
3. Retry failures: `wp ojs-sync sync --bulk --yes` again (only processes unsynced members). Or target specific users: `wp ojs-sync sync --member=<email>`.

### Password / login issues

1. Members log in to OJS with their WP email and password — no separate setup needed.
2. If a member can't log in: verify their OJS account exists (`wp ojs-sync status` or check OJS admin). Re-sync if needed: `wp ojs-sync sync --member=<email>`.
3. Members can always use OJS's built-in "Forgot Password" link as a fallback.
4. After first login, OJS lazy-rehashes the WP hash (cost 10) to native bcrypt (cost 12). This is transparent to the user.

---

## Testing approach

Write tests alongside the code for all logic. Don't test framework glue (hook registration, settings rendering, cron scheduling) — trust WordPress and OJS for that. Focus test coverage on the things we own: idempotency, queue state machine, edge cases, validation, and auth.

### OJS plugin: integration tests against a test database

The OJS plugin's value is in its idempotency and upsert logic. These run against the OJS database via DAO classes, so unit tests with mocks would just be testing the mock. Integration tests against a real (test) OJS database are the right level.

**Test cases to write:**

| Test | What it verifies |
|---|---|
| Create user, then create same email again → one user returned | `find-or-create` idempotency |
| Create user with two concurrent requests → one user, no error | Race condition safety |
| `GET /users?email=existing` → `{found: true, userId}` | User lookup |
| `GET /users?email=nonexistent` → `{found: false}` | User lookup miss |
| Create subscription for new `(user, journal)` → subscription inserted | Happy path |
| Create subscription for existing `(user, journal)` with later `dateEnd` → `dateEnd` extended | Upsert extends |
| Create subscription for existing active `(user, journal)` with earlier `dateEnd` → `dateEnd` unchanged | Upsert ignores shorter (already active) |
| Expire subscription, then create with shorter `dateEnd` → reactivated with new dates | Reactivation applies all incoming values |
| Expire subscription → status set to OTHER | Happy path |
| Expire already-expired subscription → no error | Idempotent expire |
| `PUT /subscriptions/expire-by-user/{userId}` → subscription expired | Expire by user convenience endpoint |
| `PUT /subscriptions/expire-by-user/{nonexistent}` → 404 | Expire by user miss |
| Create user with `passwordHash` → WP hash stored, `mustChangePassword: false` | Password hash sync |
| Login with WP password on hash-synced user → success, hash rehashed to cost 12 | Lazy rehash |
| Re-sync existing user → password not overwritten | Re-sync safety |
| Create user with invalid email format → HTTP 400 | Input validation |
| Create subscription with non-integer `typeId` → HTTP 400 | Input validation |
| `GET /subscriptions?email=<invalid>` → HTTP 400 | Input validation on lookup |
| `GET /ping` with no auth → 200 | Ping bypasses auth |
| Request with valid auth but Reader role → HTTP 403 | Role-based authorization |
| Request with Journal Manager role → success | Role-based authorization |
| Request from non-allowlisted IP → HTTP 403 | IP allowlist |
| Non-expiring WCS subscription (`dateEnd = null`) → OJS subscription with `date_end = NULL` | Non-expiring handling |
| Update user email → email changed on existing account | Email propagation |
| Update user email to already-used email → HTTP 409 | Email uniqueness check |
| Delete user → all PII blanked, account disabled, subscription expired | GDPR erasure completeness |

**Test environment:** OJS staging instance with a dedicated test journal and test subscription type. Tests create/clean up their own data. Can run via PHPUnit within the OJS test harness.

### WP plugin: unit tests for queue processor and sync logic

The WP plugin's critical logic is the queue state machine, multi-subscription resolution, and API call sequencing. These are pure logic that can be tested without a full WP environment by extracting them into standalone classes.

**Test cases to write:**

**Queue state machine:**

| Test | What it verifies |
|---|---|
| Queue item: pending → processing → completed (on success) | Happy path state machine |
| Queue item: pending → processing → failed → Action Scheduler retries at +5min | Retry timing (Action Scheduler default) |
| Queue item: 5 failures → Action Scheduler marks failed | Retry exhaustion (Action Scheduler default) |
| Queue item: already completed → skip on re-process | Idempotent queue |
| `status_active` fires twice for same user quickly → one queue item (dedup) | Hook dedup |
| OJS returns 400 → permanent fail immediately (no retry) | Client error = no retry |
| OJS returns 401/403 → permanent fail immediately + admin alert | Auth/config error = no retry |
| OJS returns 500 → retry | Server error = retry |
| Network timeout → retry | Transient error = retry |
| OJS returns 404 on expire → mark completed, log warning | Nothing to expire |
| OJS returns 409 on email change → permanent fail + admin alert | Email conflict needs manual resolution |

**Subscription resolution:**

| Test | What it verifies |
|---|---|
| Member with 1 active WCS sub → `dateEnd` from that sub | Single subscription |
| Member with 2 active WCS subs → `dateEnd` = latest of the two | Multi-subscription resolution |
| Member with 1 active + 1 expired WCS sub → `dateEnd` from active only | Expired sub ignored |
| WCS sub with `date_end = 0` → OJS `dateEnd = null` | Non-expiring mapping |
| Member with 1 non-expiring + 1 dated active sub → `dateEnd = null` | Non-expiring wins |
| Member with manual role (no WCS sub) → `dateEnd = null` | Manual role = non-expiring |

**API call sequences (mock HTTP):**

| Test | What it verifies |
|---|---|
| `activate` action → calls find-or-create then create-subscription | Correct two-step sequence |
| `activate` for existing user (`created: false`) → password hash not overwritten | Re-sync safety |
| `activate` stores returned `userId` in `_wpojs_user_id` usermeta | OJS userId cached |
| `expire` with cached usermeta → calls expire-by-user directly (1 HTTP call) | Usermeta cache hit |
| `expire` without usermeta → calls GET /users then expire-by-user (2 HTTP calls) | Usermeta cache miss |
| `expire` when GET /users returns `found: false` → mark completed, log skip | Never-synced user |
| `email_change` → calls GET /users (old email) then PUT /users/{id}/email | Correct sequence |
| `email_change` when user not on OJS → skip | Nothing to update |
| `delete_user` → calls GET /users then DELETE /users/{id}, removes usermeta | GDPR sequence |
| `delete_user` when user not on OJS → skip, clean up usermeta | Nothing to delete |

**Hook detection:**

| Test | What it verifies |
|---|---|
| WP email change detected → `email_change` action queued with old + new email | Email change hook |
| WP user deletion → `delete_user` action queued | GDPR hook |

**Test connection:**

| Test | What it verifies |
|---|---|
| Ping OK + preflight OK → "Connection successful" | Happy path |
| Ping OK + preflight 403 → "OJS reachable but IP not allowlisted" | IP mismatch diagnostic |
| Ping fails → "OJS not reachable" | Network/URL problem |
| Ping OK + preflight returns `compatible: false` → show failed checks | Incompatible OJS version |

**Bulk sync and reconciliation:**

| Test | What it verifies |
|---|---|
| Bulk sync re-run → already-synced users skipped (via usermeta) | Resume capability |
| Bulk sync dry-run → no HTTP calls, correct count reported | Dry-run safety |
| Bulk sync batching → 50 users per batch, adaptive throttling | Pacing |
| Reconciliation: active WCS user with no OJS subscription → queues activate | Drift detection |
| Reconciliation: expired WCS user with active OJS subscription → queues expire | Drift detection (reverse) |
| Reconciliation: manual role member included | Non-WCS members detected |

**Test environment:** PHPUnit with WP test framework (`WP_UnitTestCase`). Mock the HTTP layer (don't call real OJS during unit tests) — the OJS side has its own integration tests.

### What we don't test

- WP hook registration (trust WordPress)
- Settings page rendering and form handling
- Admin list table display
- OJS DAO internals (tested by OJS itself)
- WP Cron scheduling mechanics (trust WordPress)
- CSS, template rendering, UI layout

### End-to-end smoke tests

The smoke test checklist in TODO.md covers the full integration path. Most are now automated as Playwright browser tests in `e2e/`:

| Spec file | What it tests |
|---|---|
| `expanded-resilience.spec.ts` | Out-of-order events, multiple subs, rapid changes, reconciliation drift, API validation (18 tests) |
| `admin-monitoring.spec.ts` | Sync Log page stats, nonce, retry actions (single + bulk) |
| `ojs-login.spec.ts` | Synced user sets OJS password and logs in |
| `password-sync.spec.ts` | WP password hash sync: bulk creates with hash, OJS login works, lazy rehash to cost 12, re-sync doesn't overwrite |
| `wp-dashboard.spec.ts` | My Account journal access widget (active member + non-member) |
| `ojs-ui-messages.spec.ts` | Login hint, footer message, paywall hint for non-subscriber |
| `ojs-api-log.spec.ts` | OJS API request logging and `wpojs_created_by_sync` flag |
| `email-change.spec.ts` | WP email change → OJS user email updated |
| `user-deletion.spec.ts` | WP user deletion → OJS user anonymised, disabled, subscription expired |
| `test-connection.spec.ts` | Settings page "Test Connection" AJAX reports success |
| `error-recovery.spec.ts` | Sync fails when OJS URL is bad, succeeds after restoring |

58 tests across 15 spec files. Tests run against the Docker dev environment (`--with-sample-data`). Each test creates unique users, processes the Action Scheduler queue, and cleans up in `afterAll`. Serial execution (`workers: 1`) avoids data conflicts. Run with `npm test`.

---

## Why this works

- OJS paywall operates completely normally — subscriptions are real OJS subscription records
- Non-member purchases are completely unaffected
- WP remains source of truth — OJS subscriptions are a downstream projection
- Uses OJS's own validated DAO layer — no raw SQL, no bypassed validation
- Both sides are standard plugins — no core code changes on either platform
- Subscriptions visible in OJS admin UI for troubleshooting
- Daily reconciliation catches any drift automatically
- Async dispatch means OJS downtime doesn't block WP checkout

## Trade-offs

- **Two plugins to build and maintain.** Both can be written in 1-2 Claude Code sessions. Real time is in testing, deployment, and the OJS upgrade.
- **Requires OJS 3.5+.** The upgrade has significant breaking changes. This is the biggest risk in the whole plan.
- **Members need separate OJS accounts.** Two logins, matched by email. Mitigated by password hash sync (same password works on both), permanent login page prompt, and cross-links between systems.
- **OJS upgrades could break the OJS plugin.** Uses internal PHP classes, not a stable public API. Mitigated by `/preflight` endpoint (verifies all dependencies after upgrade) and using the DAO layer (more stable than raw SQL). Still needs testing on each OJS upgrade.
