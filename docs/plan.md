# Implementation Plan: Push-sync

Last updated: 2026-02-20

WP pushes subscription changes to OJS via a custom plugin on each side. For how we arrived at this decision, see [`discovery.md`](./discovery.md). For the full review that shaped this plan, see [`review-findings.md`](./review-findings.md).

---

## How it works

```
Bulk sync (~700 existing members at launch)
  → WP-CLI command reads all active WCS subscriptions + users with manual member roles
  → For each member: calls OJS endpoint to find-or-create user by email
  → Calls OJS endpoint: create subscription
  → "Set your password" welcome email sent to member
  → Member visits OJS, sets password, hits paywalled content:
  → OJS paywall checks its own database → finds valid subscription → access granted

Member signs up / renews on WordPress (ongoing, after launch)
  → WP plugin fires on WooCommerce Subscription status change
  → Schedules sync action via Action Scheduler (not inline HTTP)
  → Action Scheduler calls OJS: find-or-create user + create/renew subscription
  → If OJS is down: retries 3 times (5min / 15min / 1hr), then alerts admin
  → Member visits OJS, logs in, hits paywalled content → access granted

Member lapses / cancels
  → WP plugin fires on status change → schedules expire action
  → Action Scheduler calls OJS: expire subscription
  → OJS paywall denies access → shows purchase options + "SEA member? Contact support"

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
- "Set your password" welcome email: generates reset token, sends branded email on user creation
- UI messages via plugin hooks: login hint, paywall hint for non-subscribers, site footer. Config: `[wpojs] wp_member_url`, `support_email`
- Delete-user endpoint for GDPR erasure propagation from WP
- **API log auto-cleanup**: old log entries (>30 days) are automatically deleted during API requests (at most once per hour). Manual cleanup also runs when an admin opens the status page.

### WP plugin (`wpojs-sync`)

Installed like any WP plugin:

- Settings page: OJS URL (HTTPS enforced), subscription type ID mapping (WooCommerce Product → OJS Subscription Type), journal ID(s), "Test connection" button
- API key stored as `wp-config.php` constant (`WPOJS_API_KEY`), not in the database
- Hooks into **WooCommerce Subscriptions** lifecycle events (`status_active`, `status_expired`, `status_cancelled`, `status_on-hold`) as primary triggers
- **Async dispatch**: hooks schedule sync actions via Action Scheduler (not inline HTTP calls)
- **Retry logic**: 3 attempts at 5min / 15min / 1hr intervals via Action Scheduler; after 3 failures, mark as permanently failed and email admin
- **Email change hook**: detects WP email changes, calls OJS to update the corresponding account
- Bulk sync command (**WP-CLI only**) for initial population and drift correction, with batching and resume
- **Structured sync log**: custom DB table with per-user success/fail records, searchable in WP admin
- **Admin email alerts**: immediate notification on sync failure after retries exhausted; daily digest of failure count
- **Daily reconciliation**: WP Cron job comparing active WCS subscriptions + manual member roles vs OJS, retrying any drift
- See [`wp-integration.md`](./wp-integration.md) for full hook details and code patterns

---

## OJS endpoint spec

All endpoints except `/ping` require Bearer token auth + Journal Manager or Site Admin role + IP allowlist. All are idempotent.

**Fragility note:** These endpoints use OJS internal PHP classes (Repo facade, DAOs, Validation), not a stable public API. OJS could rename, merge, or remove any of these in a future release without notice — every OJS plugin has this risk. Mitigation: the `/preflight` endpoint verifies every class and method the plugin depends on. The WP "Test connection" button calls `/ping` then `/preflight`. **Run `/preflight` after any OJS upgrade.**

**Journal context:** All endpoints are scoped to the journal in the URL path (e.g., `/api/v1/{journal-path}/wpojs/subscriptions`). There is no `journalId` parameter in request bodies — the journal is always derived from the URL context via OJS's `has.context` middleware.

| Method | Path | Request body | Response | Notes |
|---|---|---|---|---|
| `GET` | `/ping` | — | `200 {status: "ok"}` | **No auth, no IP check** — pure reachability probe. If ping succeeds but preflight returns 403, the IP is not allowlisted. |
| `GET` | `/preflight` | — | `200 {compatible: bool, checks: [...]}` | Verifies all PHP classes, methods, and DB tables the plugin depends on. Checks: `Repo::user()` methods, `Repo::userGroup()` methods, `Repo::emailTemplate()` methods, `IndividualSubscriptionDAO` methods, `SubscriptionTypeDAO`, `Validation` methods, `AccessKeyManager`, `PasswordResetRequested`, `Core::getCurrentDate()`, `Role::ROLE_ID_READER`, `user_user_groups` table, `wpojs_api_log` table, subscription types exist for journal. |
| `GET` | `/users?email=` | — | `200 {found, userId, email, username, disabled}` or `200 {found: false}` | Read-only user lookup by email. No side effects. |
| `POST` | `/users/find-or-create` | `{email, firstName, lastName, sendWelcomeEmail?}` | `200 {userId, created: bool}` | Finds existing user by email or creates new one with Reader role. `sendWelcomeEmail` is only honoured when `created: true` (existing users already have a password). |
| `PUT` | `/users/{userId}/email` | `{newEmail}` | `200 {userId}` | Checks email uniqueness before updating (OJS doesn't enforce it in `edit()`). Returns `409` if email in use by another account. |
| `DELETE` | `/users/{userId}` | — | `200 {deleted: true, userId}` | GDPR erasure: anonymises all PII (email, username, name, affiliation, biography, orcid, url, phone, mailing address), disables account, expires any active subscription, removes welcome email dedup flag. Does **not** hard-delete (safe for accounts with submission history). |
| `POST` | `/subscriptions` | `{userId, typeId, dateStart, dateEnd}` | `200 {subscriptionId}` | Idempotent upsert. **Reactivation** (expired→active): applies all incoming values (typeId, dateStart, dateEnd). **Already active**: extends `dateEnd` only if later (prevents shortening from out-of-order events). `dateEnd: null` = non-expiring. Returns at most one subscription per user per journal. |
| `PUT` | `/subscriptions/{id}/expire` | — | `200 {subscriptionId}` | Sets status to `SUBSCRIPTION_STATUS_OTHER`. Idempotent. |
| `PUT` | `/subscriptions/expire-by-user/{userId}` | — | `200 {subscriptionId}` | Convenience: expires subscription by userId (saves WP plugin a lookup call). Returns `404` if no subscription found. |
| `GET` | `/subscriptions?email=&userId=` | — | `200 [{subscriptionId, userId, journalId, typeId, status, dateStart, dateEnd}]` | Returns array with at most one item per user per journal (OJS enforces one-subscription-per-user-per-journal). Returns `[]` if user or subscription not found. |
| `POST` | `/welcome-email` | `{userId}` | `200 {sent: true}` or `200 {sent: false, reason}` | Generates password reset access key via `AccessKeyManager::createKey()`, builds reset URL, sends `PasswordResetRequested` mailable. Dedup: atomic `insertOrIgnore` on `wpojs_welcome_email_sent` flag — concurrent requests are safe. If mail send fails, dedup flag is removed so it can be retried. Token expiry configured via `password_reset_timeout` in `config.inc.php` (set to 7 for bulk welcome emails). |

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
| `action` | VARCHAR(30) | `activate`, `expire`, `create_user`, `email_change`, `welcome_email`, `delete_user` |
| `status` | VARCHAR(10) | `success`, `fail` |
| `ojs_response_code` | SMALLINT | HTTP status from OJS |
| `ojs_response_body` | TEXT | Truncated response for debugging |
| `attempt_count` | TINYINT | How many attempts it took |
| `created_at` | DATETIME | |

### Hook → action mapping

| WP hook | Condition | OJS action |
|---|---|---|
| `woocommerce_subscription_status_active` | — | Queue: find-or-create user + create/renew subscription + welcome email (if new user) |
| `woocommerce_subscription_status_expired` | — | Queue: expire subscription |
| `woocommerce_subscription_status_cancelled` | — | Queue: expire subscription |
| `woocommerce_subscription_status_on-hold` | — | Queue: expire subscription (immediate, no grace period) |
| `profile_update` | Email changed | Queue: update OJS user email (old email → new email) |
| `delete_user` / `deleted_user` | — | Queue: call OJS delete-user endpoint (GDPR) |

When a member has **multiple active WCS subscriptions**, the plugin resolves to one OJS action: active if any subscription is active, `date_end` = latest end date across all active subscriptions.

### Queue processor — API call sequences

Each queue action maps to a specific sequence of OJS API calls. The queue processor stores `ojs_user_id` in WP usermeta (`_wpojs_user_id`) after the first successful find-or-create, so subsequent actions can skip the lookup.

**`activate`** (new subscription or renewal):
1. `POST /users/find-or-create` — `{email, firstName, lastName, sendWelcomeEmail: true}`. Returns `{userId, created}`. If `created: true`, welcome email is sent automatically. Store `userId` in usermeta.
2. `POST /subscriptions` — `{userId, typeId, dateStart, dateEnd}`. Uses the `userId` from step 1. `typeId` from settings mapping. `dateEnd` = latest across all active WCS subs for this user (or `null` for non-expiring).

**`expire`** (cancellation, expiry, on-hold):
1. Resolve OJS userId: check `_wpojs_user_id` usermeta first. If not cached, call `GET /users?email=` to look up. If user not found, log and skip (never synced).
2. `PUT /subscriptions/expire-by-user/{userId}` — single call, no need to look up subscriptionId separately. Returns `404` if no subscription (log and skip — subscription may have already been expired or never created).

**`email_change`**:
1. Resolve OJS userId: usermeta or `GET /users?email={oldEmail}`. If not found, log and skip.
2. `PUT /users/{userId}/email` — `{newEmail}`. Returns `409` if new email already in use on OJS (alert admin — manual resolution needed).

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
| `wp ojs-sync sync --dry-run` | Report what bulk sync would do without making changes |
| `wp ojs-sync sync` | Run bulk sync: creates users + subscriptions (no welcome emails). Batched (50 users), 500ms delay, resume from last run. Rejects unknown flags (e.g. `--user=` errors instead of silently running bulk). |
| `wp ojs-sync sync --member=<id or email>` | Sync a single member (sends welcome email — it's a targeted action) |
| `wp ojs-sync send-welcome-emails --dry-run` | Report how many welcome emails would be sent |
| `wp ojs-sync send-welcome-emails` | Send "set your password" emails to all synced users. OJS dedup prevents duplicates — safe to re-run |
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
| **Password via welcome email** | Bulk sync triggers "set your password" email inline per member via `sendWelcomeEmail: true` (not a separate step). Requires transactional email relay on OJS (Mailgun/SES/Postmark). Login page prompt as permanent fallback. |
| **Content loaded gradually** | Launch with ~60 recent articles. Back issues loaded over time. |
| **Non-expiring subs** | WCS subscriptions with no end date → OJS subscription with `date_end = NULL` and `non_expiring` subscription type. |
| **Async sync dispatch** | WCS hooks log intent to a queue table. WP Cron processes the queue. No inline HTTP calls on checkout. |
| **Daily reconciliation at launch** | Not deferred. A daily WP Cron job compares WCS ↔ OJS and retries any drift. |
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
| WC subscription products | 6 products: IDs 1892, 1924, 1927, 23040, 23041, 23042. See [`wp-integration.md`](./wp-integration.md#woocommerce-subscription-products-live-site) for full table. |
| Membership tiers | All nine WP roles grant journal access (six standard, three manual/admin-assigned). |
| Membership role slugs | Standard: `um_custom_role_1` through `_6`. Manual: `um_custom_role_7`, `_8`, `_9`. See [`wp-integration.md`](./wp-integration.md#membership-roles-on-live-site) for full table. |
| Manual member roles | Admin-assigned (Exco/life members). Currently 1 member. Bypass WCS checkout — bulk sync and reconciliation must detect these via WP roles directly. |
| Non-standard access | Editorial board, reviewers, etc. managed manually in OJS admin UI. Not part of WP sync. |
| Hosting | Different servers. WP and OJS communicate over HTTP. |
| OJS state | Live: fresh install, admin logins only, ~60 test articles. Docker dev: 2 issues / 43 articles imported from live export. |
| OJS journals | One journal (*Existential Analysis*). Sync targets one journal ID. |
| OJS self-registration | Enabled. Non-members need it for paywall purchases. |
| OJS email | Transactional relay (Mailgun/SES/Postmark) required on OJS — hard prerequisite for bulk sync. SPF/DKIM/DMARC must be configured. All ~700 welcome emails sent inline during bulk sync, no batching. |
| Docker dev environment | Running. WP (localhost:8080) with 727 anonymized test users, WooCommerce, Ultimate Member. OJS (localhost:8081) with 2 issues, 43 articles from live export. See `docker/README.md`. |
| WP email uniqueness | Enforced at DB level. UM email changes require confirmation. |
| WP users total | 1,418 users. 695 in membership roles + 1 manual = ~696 active members. |

---

## Launch sequence

1. **Upgrade OJS 3.4 → 3.5** — the biggest risk. Significant breaking changes (Slim→Laravel, Vue 2→3). Staging upgraded to 3.5.0.3 (2026-02-19). Still need: verify acceptance criteria on staging, write rollback runbook, agree go/no-go threshold with SEA, then upgrade production.
2. **Verify OJS API prerequisites** — test Bearer token auth from WP server IP (`CGIPassAuth on`), test user creation API, confirm email config (SPF/DKIM/DMARC), document OJS server specs.
3. **Build and deploy OJS plugin** (`wpojs-subscription-api`) — code review + PHPStan before production. See [`deployment.md`](./deployment.md) for non-Docker deployment steps (folder naming, API route mount, config).
4. **Build and deploy WP plugin** (`wpojs-sync`) — code review before production.
5. **Create OJS subscription type(s)** — at least one Individual subscription type must exist in OJS before sync can work. Go to OJS Admin → Subscriptions → Subscription Types → Create. Note the `type_id` for use in WP mapping. The preflight check will warn if none exist.
6. **Configure WP plugin mapping** — set OJS Base URL (with journal path), Default OJS Subscription Type ID, and product mappings (each WooCommerce Subscription Product ID → OJS Type ID). All six WC products (IDs 1892, 1924, 1927, 23040, 23041, 23042) should be mapped.
7. **Smoke test** — end-to-end with 10 test users: create subscription → OJS account created → subscription active → paywall grants access → expire subscription → paywall denies access. Also test non-member purchase flow.
8. **Bulk sync ~700 existing members** — `wp ojs-sync sync --dry-run` then `wp ojs-sync sync`. Creates users + subscriptions on OJS. **Does not send welcome emails** — that's a separate step. Batched (50 at a time), 500ms delay, ~12 minutes. Verify: `wp ojs-sync status` shows correct synced count, spot-check a few users in OJS admin.
9. **Send welcome emails** — `wp ojs-sync send-welcome-emails --dry-run` then `wp ojs-sync send-welcome-emails`. Sends "set your password" email to all synced users. OJS dedup prevents duplicates — safe to re-run if interrupted. Token expiry 7 days. **Requires transactional email relay on OJS.**
10. **OJS template changes** — DONE. Login hint ("First time? Set your password"), paywall hint for logged-in non-subscribers ("Contact support"), site footer ("Access provided by SEA membership"). Implemented via plugin hooks (`TemplateManager::display`, `Templates::Article::Footer::PageFooter`, `Templates::Common::Footer::PageFooter`). Messages read `wp_member_url` + `support_email` from `config.inc.php [wpojs]`.
11. **WP member dashboard** — DONE. "Access Existential Analysis" card on WooCommerce My Account page. Shows active/inactive status with expiry date.
12. **Member announcement** — via SEA's normal channel (newsletter/email), sent only after steps 8-11 are confirmed working. "Check your email for instructions" not "visit the journal now."

If the OJS 3.5 upgrade hits serious problems, fallback options are documented in [`discovery.md`](./discovery.md).

---

## Rollback procedures

### OJS 3.5 upgrade rollback

1. **Before upgrading**: take a full DB snapshot and filesystem backup of the OJS installation.
2. **If the upgrade fails**: restore the DB snapshot and filesystem backup. OJS 3.4 should come up cleanly.
3. **Go/no-go**: agree a threshold with SEA before starting (e.g. "if any of these 5 acceptance criteria fail on staging, we don't upgrade production").

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
3. Retry failures: `wp ojs-sync sync` again (only processes unsynced members). Or target specific users: `wp ojs-sync sync --member=<email>`.

### Welcome email issues

1. Welcome emails are idempotent — OJS dedup prevents duplicates. Safe to re-run `wp ojs-sync send-welcome-emails`.
2. If the email template needs changes: OJS Admin → Workflow → Emails → search for "PasswordResetRequested".
3. If emails aren't arriving: check OJS SMTP config, SPF/DKIM/DMARC records, and the OJS error log.
4. Members can always use OJS's built-in "Forgot Password" link as a fallback.

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
| Send welcome email, then send again → only one email sent | Welcome email dedup |
| Send welcome email → password reset access key created, URL in email | Reset link works |
| Send welcome email failure → dedup flag removed, returns error | Retry-safe on mail failure |
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
| Queue item: pending → processing → failed → retry at +5min | First retry timing |
| Queue item: failed → retry at +15min → retry at +1hr | Escalating retry intervals |
| Queue item: 3 failures → permanent_fail + admin email triggered | Retry exhaustion |
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
| `activate` for existing user (`created: false`) → no welcome email param sent | Welcome email only for new users |
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
| Bulk sync batching → 50 users per batch, 500ms delay between calls | Pacing |
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
| `expanded-resilience.spec.ts` | Out-of-order events, multiple subs, rapid changes, reconciliation drift, welcome email dedup, API validation (19 tests) |
| `admin-monitoring.spec.ts` | Sync Log page stats, nonce, retry actions (single + bulk) |
| `ojs-login.spec.ts` | Synced user sets OJS password and logs in |
| `wp-dashboard.spec.ts` | My Account journal access widget (active member + non-member) |
| `ojs-ui-messages.spec.ts` | Login hint, footer message, paywall hint for non-subscriber |
| `ojs-api-log.spec.ts` | OJS API request logging and `wpojs_created_by_sync` flag |
| `email-change.spec.ts` | WP email change → OJS user email updated |
| `user-deletion.spec.ts` | WP user deletion → OJS user anonymised, disabled, subscription expired |
| `test-connection.spec.ts` | Settings page "Test Connection" AJAX reports success |
| `error-recovery.spec.ts` | Sync fails when OJS URL is bad, succeeds after restoring |

35 tests across 10 spec files. Tests run against the Docker dev environment (`--with-sample-data`). Each test creates unique users, processes the Action Scheduler queue, and cleans up in `afterAll`. Serial execution (`workers: 1`) avoids data conflicts. Run with `npm test`.

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
- **Members need separate OJS accounts.** Two logins, matched by email. Mitigated by welcome email, permanent login page prompt, and cross-links between systems.
- **OJS upgrades could break the OJS plugin.** Uses internal PHP classes, not a stable public API. Mitigated by `/preflight` endpoint (verifies all dependencies after upgrade) and using the DAO layer (more stable than raw SQL). Still needs testing on each OJS upgrade.
