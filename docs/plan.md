# Implementation Plan: Push-sync

Last updated: 2026-02-17

WP pushes subscription changes to OJS via a custom plugin on each side. For how we arrived at this decision, see [`discovery.md`](./discovery.md). For the full review that shaped this plan, see [`review-findings.md`](./review-findings.md).

---

## How it works

```
Bulk sync (~500 existing members at launch)
  → WP-CLI command reads all active WCS subscriptions
  → For each member: calls OJS endpoint to find-or-create user by email
  → Calls OJS endpoint: create subscription
  → "Set your password" welcome email sent to member
  → Member visits OJS, sets password, hits paywalled content:
  → OJS paywall checks its own database → finds valid subscription → access granted

Member signs up / renews on WordPress (ongoing, after launch)
  → WP plugin fires on WooCommerce Subscription status change
  → Logs sync intent to queue table (not inline HTTP)
  → WP Cron picks up queue item, calls OJS: find-or-create user + create/renew subscription
  → If OJS is down: retries 3 times (5min / 15min / 1hr), then alerts admin
  → Member visits OJS, logs in, hits paywalled content → access granted

Member lapses / cancels
  → WP plugin fires on status change → queues expire action
  → WP Cron calls OJS: expire subscription
  → OJS paywall denies access → shows purchase options + "SEA member? Contact support"

Non-member visits paywalled content
  → No subscription exists in OJS
  → OJS shows standard purchase page (£3 / £25 / £18)
  → Completely unaffected by the integration
```

---

## What gets built

### OJS plugin (`sea-subscription-api`)

Installed in `plugins/generic/seaSubscriptionApi/`:

- Registers REST endpoints for subscription CRUD (see [endpoint spec](#ojs-endpoint-spec) below)
- All endpoints are **idempotent** — safe to call repeatedly with the same payload
- Uses OJS's own `IndividualSubscriptionDAO` internally (classes already exist and are well-tested)
- Authenticated via OJS API key (Bearer token) on a **dedicated service account** (not Site Admin)
- **IP allowlisting**: only accepts requests from the WP server's IP address
- **Input validation**: email format, integer casting on IDs, max string lengths, HTTP 400 on invalid input
- No modifications to OJS core code — standard plugin, dropped into a folder
- Requires OJS 3.5+ for the clean plugin API pattern ([pkp-lib #9434](https://github.com/pkp/pkp-lib/issues/9434))
- "Set your password" welcome email: generates reset token, sends branded email on user creation
- Custom login page message (permanent): "SEA member? First time here? Set your password"
- Delete-user endpoint for GDPR erasure propagation from WP

### WP plugin (`sea-ojs-sync`)

Installed like any WP plugin:

- Settings page: OJS URL (HTTPS enforced), subscription type ID mapping (WooCommerce Product → OJS Subscription Type), journal ID(s), "Test connection" button
- API key stored as `wp-config.php` constant (`SEA_OJS_API_KEY`), not in the database
- Hooks into **WooCommerce Subscriptions** lifecycle events (`status_active`, `status_expired`, `status_cancelled`, `status_on-hold`) as primary triggers
- **Async dispatch**: hooks log sync intent to a local queue table; WP Cron processes the queue (not inline HTTP calls)
- **Retry logic**: 3 attempts at 5min / 15min / 1hr intervals via WP Cron; after 3 failures, mark as permanently failed and email admin
- **Email change hook**: detects WP email changes, calls OJS to update the corresponding account
- Bulk sync command (**WP-CLI only**) for initial population and drift correction, with batching and resume
- **Structured sync log**: custom DB table with per-user success/fail records, searchable in WP admin
- **Admin email alerts**: immediate notification on sync failure after retries exhausted; daily digest of failure count
- **Daily reconciliation**: WP Cron job comparing active WCS subscriptions vs OJS, retrying any drift
- See [`wp-integration.md`](./wp-integration.md) for full hook details and code patterns

---

## OJS endpoint spec

All endpoints require Bearer token auth. All are idempotent.

| Method | Path | Request body | Response | Notes |
|---|---|---|---|---|
| `GET` | `/ping` | — | `200 {status: "ok"}` | Health check / connection test |
| `POST` | `/users/find-or-create` | `{email, firstName, lastName, sendWelcomeEmail?}` | `200 {userId, created: bool}` | Atomic: lookup + insert in transaction. Returns existing user if email exists. |
| `PUT` | `/users/{userId}/email` | `{newEmail}` | `200 {userId}` | For WP email change propagation |
| `DELETE` | `/users/{userId}` | — | `200` or `204` | GDPR erasure. Anonymises or deletes. |
| `POST` | `/subscriptions` | `{userId, journalId, typeId, dateStart, dateEnd}` | `200 {subscriptionId}` | Upsert: if subscription exists for `(userId, journalId)`, extends `dateEnd` if later. |
| `PUT` | `/subscriptions/{id}/expire` | — | `200` | Sets status to expired. |
| `GET` | `/subscriptions` | `?email=` or `?userId=` | `200 [{...}]` | For verification / reconciliation. |
| `POST` | `/welcome-email` | `{userId}` | `200` | Sends "set your password" email. Skips if already sent (dedup flag). |

Error responses: `400` (invalid input), `401` (bad/missing auth), `403` (IP not allowed), `404` (not found), `409` (conflict), `500` (server error). All errors return `{error: "message"}`.

---

## WP plugin spec

### Configuration

| Setting | Storage | Notes |
|---|---|---|
| OJS base URL | Settings page (`wp_options`) | HTTPS enforced — reject `http://` URLs |
| API key | `wp-config.php` constant `SEA_OJS_API_KEY` | Never stored in database |
| Subscription type mapping | Settings page (`wp_options`) | WooCommerce Product ID → OJS Subscription Type ID |
| Journal ID(s) | Settings page (`wp_options`) | Which OJS journal(s) to create subscriptions for |
| WP server IP | Display only on settings page | Shown so admin can configure OJS IP allowlist |

### Database tables

**`sea_ojs_sync_queue`** — async dispatch queue

| Column | Type | Notes |
|---|---|---|
| `id` | BIGINT PK | Auto-increment |
| `wp_user_id` | BIGINT | FK to WP users |
| `email` | VARCHAR(255) | Member email at time of event |
| `action` | VARCHAR(30) | `activate`, `expire`, `email_change`, `delete_user` |
| `payload` | TEXT (JSON) | Full data needed for the OJS call |
| `status` | VARCHAR(20) | `pending`, `processing`, `failed`, `permanent_fail`, `completed` |
| `attempts` | TINYINT | Current retry count (max 3) |
| `next_retry_at` | DATETIME | When to retry next (null if completed/permanent_fail) |
| `created_at` | DATETIME | When the event was queued |
| `completed_at` | DATETIME | When it succeeded or permanently failed |

**`sea_ojs_sync_log`** — audit trail

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

### WP-CLI commands

| Command | Description |
|---|---|
| `wp sea-ojs sync --dry-run` | Report what bulk sync would do without making changes |
| `wp sea-ojs sync` | Run bulk sync: batched (50 users), 500ms delay between calls, resume from last run |
| `wp sea-ojs sync --user=<id or email>` | Sync a single member (for support/troubleshooting) |
| `wp sea-ojs send-welcome-emails --dry-run` | Report which members would receive welcome emails |
| `wp sea-ojs send-welcome-emails` | Send welcome emails in batches (50/hour), skip already-sent |
| `wp sea-ojs reconcile` | Run reconciliation now (compare WCS ↔ OJS, retry drift) |
| `wp sea-ojs status` | Show sync stats: total synced, pending, failed, last reconciliation |
| `wp sea-ojs test-connection` | Hit OJS `/ping` endpoint and report result |

### WP Cron schedule

| Event | Frequency | What it does |
|---|---|---|
| `sea_ojs_process_queue` | Every 1 minute | Process pending/retry items from sync queue |
| `sea_ojs_daily_reconcile` | Daily | Compare active WCS subscriptions vs OJS, queue any drift |
| `sea_ojs_daily_digest` | Daily | Email admin: failure count in last 24 hours (skip if zero) |

### Admin pages

- **Settings**: OJS URL, subscription type mapping, journal IDs, test connection button, current WP server IP display
- **Sync log**: filterable list table (by status, date range, email search). Shows last 500 entries by default.
- **Sync queue**: current queue status — pending items, retrying items, permanently failed items with member email and error detail

---

## Key decisions

| Decision | Detail |
|---|---|
| **Email is the key** | Same email required on both systems. No mapping table. WP email change hook propagates changes to OJS. |
| **Bulk push creates accounts** | Don't wait for members to self-register. Push user accounts + subscriptions from WP upfront (~500 existing members at launch). |
| **All tiers grant access** | Any active WCS subscription → OJS access. Multiple WooCommerce products exist but all grant journal access. If a member has multiple subscriptions, use the latest `date_end` across all of them. |
| **Password via welcome email** | Bulk sync triggers "set your password" email per member (not "forgot password"). Login page prompt as permanent fallback. |
| **Content loaded gradually** | Launch with ~60 recent articles across 2 journals. Back issues loaded over time. |
| **Non-expiring subs** | WCS subscriptions with no end date → OJS subscription with `date_end = NULL` and `non_expiring` subscription type. |
| **Async sync dispatch** | WCS hooks log intent to a queue table. WP Cron processes the queue. No inline HTTP calls on checkout. |
| **Daily reconciliation at launch** | Not deferred. A daily WP Cron job compares WCS ↔ OJS and retries any drift. |
| **Dedicated OJS service account** | API key belongs to a purpose-built OJS account with minimum required role, not a human admin account. |

---

## Facts established

| Question | Answer |
|---|---|
| OJS version | **3.4.0-9.** Upgrade to 3.5 required. |
| OJS admin access | **Yes.** Site Administrator level. |
| Can install OJS plugins | **Yes.** |
| WP membership plugin | **Ultimate Member + WooCommerce + WooCommerce Subscriptions.** |
| Membership tiers | **All tiers grant journal access.** |
| Hosting | **Different servers.** WP and OJS on separate hosts. HTTP calls over network. |
| OJS state | **Fresh install.** A handful of admin logins, ~60 test articles, no existing member accounts. |
| OJS journals | **One journal** (*Existential Analysis*). Sync targets one journal ID. |
| OJS self-registration | **Enabled.** Non-members need it to buy articles/issues via paywall. |
| WP email uniqueness | **Enforced.** Unique index on `wp_users.user_email`. UM email changes require confirmation. |

---

## Launch sequence

1. **Upgrade OJS 3.4 → 3.5** — the biggest risk. Significant breaking changes (Slim→Laravel, Vue 2→3). Back up everything first. Test on staging. Written rollback runbook. Acceptance criteria: paywall works, purchases work, content intact. Go/no-go threshold agreed with SEA.
2. **Verify OJS API prerequisites** — test Bearer token auth from WP server IP (`CGIPassAuth on`), test user creation API, confirm email config (SPF/DKIM/DMARC), document OJS server specs.
3. **Build and deploy OJS plugin** (`sea-subscription-api`) — code review + PHPStan before production.
4. **Build and deploy WP plugin** (`sea-ojs-sync`) — code review before production.
5. **Smoke test** — end-to-end with 10 test users: create subscription → OJS account created → subscription active → paywall grants access → expire subscription → paywall denies access. Also test non-member purchase flow.
6. **Bulk sync ~500 existing members** — WP-CLI, batched (50 at a time), 500ms delay between calls. Dry-run first, verify counts. Per-user success/fail log.
7. **"Set your password" emails** — sent in batches of 50/hour. Token expiry extended to 7 days. "Welcome email sent" flag prevents duplicates on re-run.
8. **OJS template changes** — permanent "SEA member? Set your password" on login page. "SEA member? Log in for free access" above purchase options on paywall. "Your access is provided by SEA membership" in OJS footer. WCAG 2.1 AA.
9. **WP member dashboard** — "Access Existential Analysis" link to OJS. Journal access status indicator.
10. **Member announcement** — via SEA's normal channel (newsletter/email), sent only after steps 6-8 are confirmed working. "Check your email for instructions" not "visit the journal now."

If the OJS 3.5 upgrade hits serious problems, fallback options are documented in [`discovery.md`](./discovery.md).

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
| Create subscription for new `(user, journal)` → subscription inserted | Happy path |
| Create subscription for existing `(user, journal)` with later `dateEnd` → `dateEnd` extended | Upsert extends |
| Create subscription for existing `(user, journal)` with earlier `dateEnd` → `dateEnd` unchanged | Upsert ignores shorter |
| Expire subscription → status set to expired | Happy path |
| Expire already-expired subscription → no error | Idempotent expire |
| Send welcome email, then send again → only one email sent | Welcome email dedup |
| Create user with invalid email format → HTTP 400 | Input validation |
| Create subscription with non-integer `typeId` → HTTP 400 | Input validation |
| Request with no Bearer token → HTTP 401 | Auth enforcement |
| Request from non-allowlisted IP → HTTP 403 | IP allowlist |
| Non-expiring WCS subscription (`dateEnd = null`) → OJS subscription with `date_end = NULL` | Non-expiring handling |
| Update user email → email changed on existing account | Email propagation |
| Delete user → user anonymised/removed | GDPR erasure |

**Test environment:** OJS staging instance with a dedicated test journal and test subscription type. Tests create/clean up their own data. Can run via PHPUnit within the OJS test harness.

### WP plugin: unit tests for queue processor and sync logic

The WP plugin's critical logic is the queue state machine and the multi-subscription resolution. These are pure logic that can be tested without a full WP environment by extracting them into standalone classes.

**Test cases to write:**

| Test | What it verifies |
|---|---|
| Queue item: pending → processing → completed (on success) | Happy path state machine |
| Queue item: pending → processing → failed → retry at +5min | First retry timing |
| Queue item: 3 failures → permanent_fail + admin email triggered | Retry exhaustion |
| Queue item: already completed → skip on re-process | Idempotent queue |
| Member with 1 active WCS sub → `dateEnd` from that sub | Single subscription |
| Member with 2 active WCS subs → `dateEnd` = latest of the two | Multi-subscription resolution |
| Member with 1 active + 1 expired WCS sub → `dateEnd` from active only | Expired sub ignored |
| WCS sub with `date_end = 0` → OJS `dateEnd = null` | Non-expiring mapping |
| WP email change detected → `email_change` action queued with old + new email | Email change hook |
| Bulk sync re-run → already-synced users skipped | Resume capability |
| Bulk sync re-run → already-sent welcome emails not re-sent | Email dedup |
| `status_active` fires twice for same user quickly → one queue item (dedup) | Hook dedup |

**Test environment:** PHPUnit with WP test framework (`WP_UnitTestCase`). Mock the HTTP layer (don't call real OJS during unit tests) — the OJS side has its own integration tests.

### What we don't test

- WP hook registration (trust WordPress)
- Settings page rendering and form handling
- Admin list table display
- OJS DAO internals (tested by OJS itself)
- WP Cron scheduling mechanics (trust WordPress)
- CSS, template rendering, UI layout

### End-to-end smoke tests (manual)

The smoke test checklist in TODO.md covers the full integration path. These run manually on staging after both plugins are deployed — they exercise the real WP→OJS flow and can't be meaningfully automated without both systems running together.

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
- **OJS upgrades could break the OJS plugin.** Mitigated by using the DAO layer (more stable than raw SQL), but still needs testing on each OJS upgrade.
