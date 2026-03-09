# WP Admin Reference: wpojs-sync

> **This is the admin UI guide** -- what you see in WordPress admin. For CLI commands, see [WP-CLI reference](wp-cli-reference.md). For how the plugin works internally, see [WP plugin reference](wp-plugin-reference.md).

<table><tr><td>

## Settings page

Located at **OJS Sync > Settings** in WP admin. Requires `manage_options` capability.

**OJS Connection** — live status indicator (green check or red X). Set the OJS Base URL including journal path.

**Product-Based Access** — maps WooCommerce subscription product IDs to OJS subscription type IDs. OJS types loaded dynamically from the API.

**Role-Based Access** — WP roles that grant OJS access without a purchase (e.g. committee members, life members).

**Display** — journal name shown in the My Account widget.

**Status** — API key status, WP server IP (for OJS allowlist), sync queue link.

</td><td width="45%">

<img src="images/wp-settings-page.png" alt="OJS Sync settings page in WordPress admin">

</td></tr></table>

### Settings reference

| Setting | Option key | Description |
|---|---|---|
| OJS Base URL | `wpojs_url` | Full URL including journal path, e.g. `https://journal.example.org/index.php/t1`. Must be HTTPS. |
| WC Product to OJS Type mapping | `wpojs_type_mapping` | Maps WooCommerce subscription product IDs to OJS subscription type IDs. Multiple mappings supported. OJS types are loaded dynamically from the OJS API. |
| WordPress Roles | `wpojs_manual_roles` | WP roles that grant OJS access without a WooCommerce purchase (e.g. committee members, life members). Roles are listed in two groups: custom/membership roles and standard WordPress roles. |
| OJS Type | `wpojs_default_type_id` | The OJS subscription type assigned to all role-based members. Also used as fallback when no product-specific type mapping is found. |
| Journal Name | `wpojs_journal_name` | Shown in the My Account dashboard widget (e.g. "Journal of Example Studies"). |

> **This is your main monitoring tool.** Check the sync log regularly -- if things are working, you'll see a stream of green "success" entries. Red entries need attention.

<table><tr><td>

## Sync log

Located at **OJS Sync > Sync Log** in WP admin.

**Summary cards** — Members Synced, Failures (24h), Failures (7d), Success Rate (7d), Queue status. Color-coded: green/yellow/red.

**Log table** — Date, Email, Action, Status, HTTP Code, Response, Attempts. Filterable by status, email, date range. Sortable. 20 per page.

**Retry** — single retry via row action (AJAX), or bulk retry via checkbox. `delete_user` failures can't be retried (WP user already gone).

</td><td width="45%">

<img src="images/wp-sync-log.png" alt="OJS Sync Log page showing summary cards and log entries">

</td></tr></table>

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
