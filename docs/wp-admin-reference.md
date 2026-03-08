# WP Admin Reference: wpojs-sync

> **This is the admin UI guide** -- what you see in WordPress admin. For CLI commands, see [WP-CLI reference](wp-cli-reference.md). For how the plugin works internally, see [WP plugin reference](wp-plugin-reference.md).

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

> **This is your main monitoring tool.** Check the sync log regularly -- if things are working, you'll see a stream of green "success" entries. Red entries need attention.

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

> **Members see this automatically** -- no setup needed beyond configuring the journal name in Settings.

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

## Cron jobs

| Cron hook | Schedule | Description |
|---|---|---|
| `wpojs_daily_reconcile` | Daily | Reconciliation: compare WP members vs OJS, queue fixes for drift |
| `wpojs_daily_digest` | Daily | Emails admin if there were sync failures in the last 24h. Includes pending/failed queue counts. |
| `wpojs_log_cleanup` | Weekly | Deletes sync log entries older than 90 days |
