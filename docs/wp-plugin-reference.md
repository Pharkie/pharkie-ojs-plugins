# WP Plugin Reference: wpojs-sync

> **Who is this for?** Developers maintaining the WP plugin or debugging sync issues. For day-to-day admin tasks, see the [admin reference](wp-admin-reference.md). For CLI commands, see the [CLI reference](wp-cli-reference.md).

## Overview

The `wpojs-sync` plugin pushes WooCommerce Subscription membership data from WordPress to OJS (Open Journal Systems). When a member purchases, renews, cancels, or expires a subscription in WooCommerce, the plugin automatically creates/updates their OJS user account and journal subscription via the OJS plugin's REST API. All sync operations are processed asynchronously through Action Scheduler.

Members are determined by two paths: active WooCommerce Subscriptions (product-based) and WordPress roles (manually assigned, e.g. committee members). Both paths result in the same OJS subscription creation.

See also: [CLI reference](wp-cli-reference.md) · [Admin reference](wp-admin-reference.md)

> **How sync works in a nutshell:** When a WooCommerce subscription changes status (new, renewed, cancelled, expired), the plugin queues an async job via Action Scheduler. The job calls the OJS API to create/update/expire the member's journal access. Nothing happens inline during checkout — the queue handles everything in the background.

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

> **Key principle:** Permanent failures (bad email, missing user) are logged and stop. Transient failures (OJS down, network timeout) are retried automatically by Action Scheduler.

### Error handling

All actions follow the same pattern:
- **Permanent failures** (4xx except 429): logged, admin alerted via email, not retried
- **Transient failures** (5xx, network errors, 429): logged, exception thrown so Action Scheduler marks the action as failed and retries automatically
- **find-after-fail recovery:** If `find_or_create_user` fails with a retryable error, the plugin waits 500ms and checks whether the user was actually created (handles cases where OJS commits but the HTTP response fails)

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
