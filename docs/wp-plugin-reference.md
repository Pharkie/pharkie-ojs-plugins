# WP Plugin Reference: wpojs-sync

## Overview

The `wpojs-sync` plugin pushes WooCommerce Subscription membership data from WordPress to OJS (Open Journal Systems). When a member purchases, renews, cancels, or expires a subscription in WooCommerce, the plugin automatically creates/updates their OJS user account and journal subscription via the OJS plugin's REST API. All sync operations are processed asynchronously through Action Scheduler.

Members are determined by two paths: active WooCommerce Subscriptions (product-based) and WordPress roles (manually assigned, e.g. committee members). Both paths result in the same OJS subscription creation.

## WP hooks

| Hook | Event | Sync action scheduled |
|---|---|---|
| `woocommerce_subscription_status_active` | Subscription activated (new or reactivated) | `wpojs_sync_activate` |
| `woocommerce_subscription_status_expired` | Subscription expired | `wpojs_sync_expire` (if no other active sub) |
| `woocommerce_subscription_status_cancelled` | Subscription cancelled | `wpojs_sync_expire` (if no other active sub) |
| `woocommerce_subscription_status_on-hold` | Subscription put on hold | `wpojs_sync_expire` (if no other active sub) |
| `profile_update` | User profile saved (email or password change detected) | `wpojs_sync_email_change` and/or `wpojs_sync_password_change` |
| `after_password_reset` | Password reset via "Lost your password?" flow | `wpojs_sync_password_change` |
| `delete_user` | User deletion initiated (pre-delete) | Captures email + OJS user ID before data is removed |
| `deleted_user` | User deletion complete (post-delete) | `wpojs_sync_delete_user` |

**Dedup logic:** Before scheduling, each hook checks `as_has_scheduled_action()` to prevent duplicate queue entries. For email and password changes, pending actions for the same user are cancelled and replaced (cancel-and-reschedule pattern) so only the latest change is synced.

**Multi-subscription guard:** The expire hooks check whether the user still has another active subscription (or a manual member role) before scheduling expiration. The just-cancelled subscription is excluded from this check to avoid stale cache issues.

## Sync actions

Each action is registered as an Action Scheduler callback in `WPOJS_Sync::register()`.

### activate

1. Calls `find_or_create_user` on OJS with email, name, and WP password hash
2. Caches the returned OJS `userId` in WP usermeta (`_wpojs_user_id`)
3. Resolves subscription data (type, start date, end date) via `WPOJS_Resolver`
4. Calls `create_subscription` on OJS with the resolved type and dates

**OJS API calls:** `POST /wpojs/users` (find-or-create), `POST /wpojs/subscriptions` (create/upsert)

The password hash is sent so members can log into OJS with their existing WP password. It is only applied when the OJS user is newly created -- existing users' passwords are not overwritten.

### expire

1. Resolves OJS user ID from usermeta or API lookup
2. Calls `expire_subscription_by_user` on OJS
3. Treats 404 (no subscription found) as success

**OJS API call:** `PUT /wpojs/subscriptions/{userId}/expire`

### email_change

1. Checks for staleness (if WP email has changed again since queuing, skips)
2. Resolves OJS user ID using the old email
3. Calls `update_user_email` on OJS
4. 409 (email conflict) triggers an admin alert email and is not retried

**OJS API call:** `PUT /wpojs/users/{userId}/email`

### password_change

1. Reads the current WP password hash fresh at processing time (not stored in queue args)
2. Resolves OJS user ID from usermeta or API lookup
3. Calls `update_user_password` on OJS

**OJS API call:** `PUT /wpojs/users/{userId}/password`

### delete_user

1. Uses email and OJS user ID captured before WP deletion (via `pre_delete_user` hook)
2. Falls back to API lookup by email if OJS user ID wasn't cached
3. Calls `delete_user` on OJS
4. Anonymizes all sync log entries for the user (GDPR compliance)

**OJS API call:** `DELETE /wpojs/users/{userId}`

### Error handling

All actions follow the same pattern:
- **Permanent failures** (4xx except 429): logged, admin alerted via email, not retried
- **Transient failures** (5xx, network errors, 429): logged, exception thrown so Action Scheduler marks the action as failed and retries automatically
- **find-after-fail recovery:** If `find_or_create_user` fails with a retryable error, the plugin waits 500ms and checks whether the user was actually created (handles cases where OJS commits but the HTTP response fails)

## WP-CLI commands

All commands are under the `wp ojs-sync` namespace.

### sync

Bulk sync all active members to OJS, or sync a single member.

```bash
# Sync a single member by ID or email
wp ojs-sync sync --member=42
wp ojs-sync sync --member=member@example.com

# Bulk sync all active members (--bulk required)
wp ojs-sync sync --bulk --dry-run
wp ojs-sync sync --bulk --yes

# Resume an interrupted bulk sync
wp ojs-sync sync --bulk --resume

# Control batch progress logging interval
wp ojs-sync sync --bulk --batch-size=100
```

| Flag | Description |
|---|---|
| `--member=<id-or-email>` | Sync a single member by WP user ID or email |
| `--bulk` | Sync all active members (required for bulk sync) |
| `--dry-run` | Report what would happen, no changes |
| `--batch-size=<n>` | Progress logging interval (default: 50) |
| `--yes` | Skip confirmation prompt (only with `--bulk`) |
| `--resume` | Resume from last checkpoint (stored as a transient) |

Bulk sync uses adaptive throttling based on OJS response times. If OJS responds in <200ms, no delay. If OJS slows down, delays mirror the response time. If OJS returns 429, the `Retry-After` header value is respected. Sends WP password hashes so members can log in to OJS with their existing credentials.

### test-connection

Two-step connectivity check against OJS.

```bash
wp ojs-sync test-connection
```

1. **Ping** -- checks OJS reachability (no auth)
2. **Preflight** -- validates API key authentication, IP allowlisting, and plugin compatibility

### status

Shows sync health overview.

```bash
wp ojs-sync status
```

Displays:
- Action Scheduler queue counts (pending, running, failed, complete)
- Active WP members count
- Members synced to OJS count
- Failures in last 24 hours
- Cron schedule (next reconciliation, daily digest, log cleanup)

### reconcile

Run reconciliation on demand (same logic as the daily cron).

```bash
wp ojs-sync reconcile
```

Batch-queries OJS for subscription status of all active WP members, queues activate/expire actions for any drift found.

## Settings page

Located at **OJS Sync > Settings** in WP admin. Requires `manage_options` capability.

### OJS Connection

| Setting | Option key | Description |
|---|---|---|
| OJS Base URL | `wpojs_url` | Full URL including journal path, e.g. `https://journal.example.org/index.php/t1`. Must be HTTPS. |

The page shows a live connection status indicator (green check or red X) based on whether OJS subscription types can be loaded.

### Product-Based Access

| Setting | Option key | Description |
|---|---|---|
| WC Product to OJS Type mapping | `wpojs_type_mapping` | Maps WooCommerce subscription product IDs to OJS subscription type IDs. Multiple mappings supported. OJS types are loaded dynamically from the OJS API. |

### Role-Based Access

| Setting | Option key | Description |
|---|---|---|
| WordPress Roles | `wpojs_manual_roles` | WP roles that grant OJS access without a WooCommerce purchase (e.g. committee members, life members). Roles are listed in two groups: custom/membership roles and standard WordPress roles. |
| OJS Type | `wpojs_default_type_id` | The OJS subscription type assigned to all role-based members. Also used as fallback when no product-specific type mapping is found. |

### Display

| Setting | Option key | Description |
|---|---|---|
| Journal Name | `wpojs_journal_name` | Shown in the My Account dashboard widget (e.g. "Journal of Example Studies"). |

### Status section

The settings page also displays:
- **API Key status** -- whether `WPOJS_API_KEY` is defined in `wp-config.php`
- **WP Server IP** -- the IP that OJS sees (for allowlist configuration)
- **Sync Queue link** -- direct link to Action Scheduler filtered to `wpojs` actions

## Sync log

Located at **OJS Sync > Sync Log** in WP admin.

### Summary cards

Five cards across the top of the page:
- **Members Synced** -- X of Y (color-coded: green if all synced, yellow if partial, red if zero)
- **Failures (24h)** -- red if >0
- **Failures (7d)** -- red if >5, yellow if 1-5
- **Success Rate (7d)** -- percentage, red if <80%, yellow if <95%
- **Queue** -- pending and failed Action Scheduler counts

### Log table

Columns: Date, Email, Action, Status, HTTP Code, Response, Attempts.

Filters:
- Status (All / Success / Fail)
- Email (text search)
- Date range (from/to)

Sortable by: Date, Email, Status, Action.

Paginated at 20 entries per page.

### Retry functionality

- **Single retry:** Each failed entry has a "Retry" link in the row actions (via AJAX). Schedules a new Action Scheduler action for that user.
- **Bulk retry:** Checkbox selection + "Retry Selected" bulk action. Failed entries only.
- **Limitations:** `delete_user` failures cannot be retried (the WP user is already gone -- must be resolved manually in OJS admin). `email_change` retries are converted to full `activate` actions since the original old/new email data isn't preserved in the log.

### Failure notice

When 5+ failures occur in 24 hours, a warning banner appears on the WP dashboard and OJS Sync admin pages with a link to the filtered log. Cached for 15 minutes.

## My Account widget

A "Journal Access" card appears on the WooCommerce **My Account > Dashboard** page (via the `woocommerce_account_dashboard` hook).

**For active members:**
- Green "Active" or "Active until [date]" badge
- "Your membership includes access to [Journal Name]" text
- "Read [Journal Name]" button linking to the OJS URL
- Last sync timestamp

**For non-members:**
- Red "No active access" badge
- Message to contact support if they believe they should have access

## Reconciliation

Runs daily via WP cron (`wpojs_daily_reconcile`). Can also be triggered manually via `wp ojs-sync reconcile`.

### What it checks

**1. Missing access (active WP member without OJS subscription):**
- Gets all active WP members (WCS subscribers + manual role members)
- Batch-queries OJS in chunks of 100 emails via `get_subscription_status_batch`
- For any member without an active OJS subscription, schedules `wpojs_sync_activate`
- Logged as `reconcile_activate`

**2. Stale access (synced user no longer an active WP member):**
- Queries all WP users with `_wpojs_user_id` usermeta (paginated in batches of 500)
- Compares against the active member set
- For any synced user who is no longer active, schedules `wpojs_sync_expire`
- Logged as `reconcile_expire`

### Other cron jobs

| Cron hook | Schedule | Description |
|---|---|---|
| `wpojs_daily_reconcile` | Daily | Reconciliation (see above) |
| `wpojs_daily_digest` | Daily | Emails admin if there were sync failures in the last 24h. Includes pending/failed queue counts. |
| `wpojs_log_cleanup` | Weekly | Deletes sync log entries older than 90 days |

## Action Scheduler

All sync operations are processed asynchronously via [Action Scheduler](https://actionscheduler.org/), which is bundled with WooCommerce.

- **Group:** `wpojs-sync` (used for filtering in the AS admin UI)
- **Hooks:** `wpojs_sync_activate`, `wpojs_sync_expire`, `wpojs_sync_email_change`, `wpojs_sync_delete_user`, `wpojs_sync_password_change`
- **Dedup:** `as_has_scheduled_action()` prevents duplicate entries for the same user/action
- **Retry:** Transient failures throw an exception, causing AS to mark the action as failed. AS has built-in retry with exponential backoff. Permanent failures (4xx) return normally so AS marks them complete (no retry).
- **Processing:** AS runs on every page load via WP cron (or via a real cron if configured). Actions are processed in the background.

## Config validation

On every admin page load, the plugin checks for:

1. `WPOJS_API_KEY` constant defined in `wp-config.php`
2. `wpojs_url` option set in Settings

If either is missing, a red admin notice appears:

> **OJS Sync:** Missing configuration: WPOJS_API_KEY constant (wp-config.php), OJS URL (Settings > OJS Sync)

This notice is suppressed during AJAX requests.
