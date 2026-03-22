# Support Runbook: WP-OJS Sync

Quick reference for support staff handling member access issues.

> **No technical background needed** for the first three sections (common scenarios, admin pages, retry). The troubleshooting section at the bottom is more technical — skip it unless the simple steps didn't help.

---

## Common scenarios

<table><tr><td>

### "I can't access journal articles"

1. **Check WP subscription status** — WooCommerce → Subscriptions → search by email. Is the subscription `active`?
2. **Check sync log** — WP Admin → OJS Sync → Sync Log → search by email. Look for recent `activate` entries.
   - If last entry is `success`: the sync worked. Check OJS directly.
   - If last entry is `fail`: note the error, check OJS connectivity, retry (see below).
   - If no entries: the member's subscription status change may not have fired. Run manual sync (see below).
3. **Check OJS** — OJS Admin → Users & Roles → search by email. Is the user present? Check Subscriptions → Individual Subscriptions. Is the subscription `Active`?

### "I changed my email and lost access"

1. Check the sync log for `email_change` entries.
2. If the new email already existed on OJS (409 conflict), the sync will have failed. Manually update the OJS user's email in OJS Admin → Users & Roles → Edit User.

</td><td width="45%">

<img src="images/wp-sync-log.png" alt="Sync Log page with summary cards and filterable log table">

</td></tr></table>

> **Most common issue.** Members don't realize they use their WordPress (membership site) password for the journal — not a separate OJS password.

<table><tr><td>

### "I can't log in to the journal"

1. Members log in with their WP (membership) email and password. No separate OJS password setup needed.
2. Verify the member has an OJS account: `wp ojs-sync sync --member=<email>` (idempotent — safe to re-run, won't duplicate).
3. If they've changed their WP password since the last sync, re-sync to push the new hash: `wp ojs-sync sync --member=<email>`.
4. The member can also use OJS's "Forgot Password" link as a fallback.

</td><td width="45%">

<img src="images/wc-subscriptions-list.png" alt="WooCommerce Subscriptions list showing active member subscriptions">

</td></tr></table>

---

## WP-CLI commands

All commands must be run on the WP server (or via SSH). Prefix with `--allow-root` if running as root.

| Command | What it does |
|---|---|
| `wp ojs-sync test-connection` | Test connectivity to OJS (ping + preflight). Run this first. |
| `wp ojs-sync status` | Show sync stats: total synced, pending, failed, last reconciliation. |
| `wp ojs-sync sync --bulk --dry-run` | Preview what bulk sync would do (no changes). |
| `wp ojs-sync sync --bulk --yes` | Run bulk sync for all active members. |
| `wp ojs-sync sync --member=<id or email>` | Sync a single member (sends WP password hash). |
| `wp ojs-sync reconcile` | Run reconciliation now (compare WP ↔ OJS, fix drift). |

---

## Admin pages

### WordPress

- **Settings**: WP Admin → OJS Sync → Settings. OJS URL, type mapping, role-based access. Connection status shown automatically.
- **Sync Log**: WP Admin → OJS Sync → Sync Log. Filterable by status, searchable by email.
- **Queue**: WP Admin → Tools → Scheduled Actions. Filter by group `wpojs-sync`.

### OJS

- **Plugin Settings**: OJS Admin Dashboard → Settings → Website → Plugins tab → Installed Plugins → Generic Plugins → WP–OJS Subscription API → **Settings**. Editable text for login hint, paywall hint, and footer message.
- **Plugin Status**: Same location → **Status**. Shows API request log, config health checks, and sync stats.
- **Subscriptions**: OJS Admin Dashboard → Payments → Subscriptions. View/manage individual subscriptions.
- **Subscription Types**: OJS Admin Dashboard → Payments → Subscriptions → Subscription Types. The type IDs referenced in WP settings.
- **Users**: OJS Admin Dashboard → Users & Roles. Search by email to check if a synced user exists.

---

> **Safe to retry.** All sync operations are idempotent — retrying won't create duplicates or break anything.

## Retry a failed sync

### Single entry

1. Go to Sync Log → filter by `Status: fail`.
2. Click the **Retry** link on the failed entry's row.

### Bulk retry

1. Go to Sync Log → filter by `Status: fail`.
2. Check the entries you want to retry.
3. Select **Retry Selected** from the Bulk Actions dropdown → Apply.

### Manual sync (bypasses queue)

```bash
wp ojs-sync sync --member=<email>
```

This calls OJS directly (not through Action Scheduler) and reports the result immediately.

---

### "I reset my password on the journal but it stopped working"

1. **What happened:** The member used OJS's "Forgot Password" to set a new password on the journal. Later, a WP password sync (triggered by a WP password change or reconciliation) overwrote it with their WP password hash.
2. **Fix:** Tell the member to reset their password on the **membership website** (WordPress), not the journal. The new WP password will sync to OJS automatically.
3. **If they didn't change their WP password:** Their original WP password still works on both systems. The OJS-set password was overwritten by the next sync cycle.
4. **Prevention:** The OJS forgot-password page now shows a hint directing members to reset on the membership site instead. If they still use OJS's reset, this scenario can recur.

---

> **For IT / server admins only.** Support staff can skip this section.

## Database access (Adminer)

Adminer is a lightweight DB admin GUI on the staging/prod server. It's bound to localhost only — you reach it via SSH tunnel.

**Connect:**

```bash
ssh -L 8082:127.0.0.1:8082 sea-staging
```

Then open `http://localhost:8082` in your browser.

**Credentials:**

| Database | Server | Username | Password | DB name |
|----------|--------|----------|----------|---------|
| WordPress | `wp-db` | `wordpress` | `WP_DB_PASSWORD` from `.env` on VPS | `wordpress` |
| OJS | `ojs-db` | `ojs` | `OJS_DB_PASSWORD` from `.env` on VPS | `ojs` |

Select **MySQL** as the system on the Adminer login page. Type the server name exactly as shown (these are Docker container hostnames).

---

## Troubleshooting

### Test connection fails

| Result | Meaning | Fix |
|---|---|---|
| "OJS not reachable" | WP can't reach OJS server at all | Check OJS URL in settings, verify OJS is running, check firewall |
| "Access denied (IP not allowlisted)" | OJS is reachable but blocking WP's IP | Add WP server IP to OJS `config.inc.php` `[wpojs] allowed_ips` |
| "Authentication failed" | API key mismatch | Verify `WPOJS_API_KEY` in `wp-config.php` matches `WPOJS_API_KEY_SECRET` env var on OJS server |
| "Incompatible" | OJS plugin API changed | Run OJS `/preflight` to see which checks failed. May need OJS plugin update. |

### How to reset an OJS password

OJS has its own password system, independent of WordPress. If a member can't log in:

1. Direct the member to the **Forgot Password** link on the OJS login page (`/login/lostPassword`).
2. They'll receive a password reset email from OJS (not WP).
3. If OJS email isn't working, an OJS admin can reset the password manually: OJS Admin → Users & Roles → Edit User → Password.

### Login hint customization

The OJS login page shows a hint message ("Member? Log in with your membership email and password."). To customize:

1. Go to OJS → Settings → Website → Plugins → WP-OJS Subscription API → Settings.
2. Edit the login hint, paywall hint, and footer message as needed.
3. Or set `WPOJS_DEFAULT_LOGIN_HINT` in the `.env` file and re-run setup.

### Daily digest shows failures

The digest email fires once per day if there were any sync failures in the last 24 hours.

1. Check the Sync Log for the specific failures.
2. Most common causes: OJS was temporarily down (transient, will auto-retry), or a configuration change broke connectivity.
3. If failures are ongoing, run `wp ojs-sync test-connection` to diagnose.

### OJS API log

OJS Admin Dashboard → Settings → Website → Plugins tab → Installed Plugins → Generic Plugins → WP–OJS Subscription API → Status. Shows recent API requests, config health checks, and sync stats. Log entries older than 30 days are automatically cleaned up.

> **Edge case — rare.** This only matters if an email change sync failed AND the member had an existing OJS account under their old email.

### Email change retry falls back to full sync

When retrying a failed `email_change` sync entry, the system falls back to a full `activate` sync (find-or-create user + subscription) because the log entry doesn't store the old/new email pair. This is correct behaviour — the retry will ensure the user has an active OJS account and subscription using their current WP email — but it won't rename an existing OJS account from old email to new email. If the member had an OJS account under their old email, that account will remain with the old email and a new account will be created with the current email.

**What to tell the member:** Their access should work with their current email. If they had content/history under the old email in OJS, an admin may need to manually merge or clean up the old account in OJS.

**If a duplicate OJS account was created:** In OJS Admin → Users & Roles, search for the member's old email. Disable or delete the old account (only delete if it has no submission history). The member's current account (under their new email) should already have an active subscription.

> **Manual intervention required.** This is the one failure type that can't be automatically retried.

### Delete user retry not available

The retry button is intentionally hidden for `delete_user` failures. When a WP user is deleted, the plugin tries to anonymise their OJS account (GDPR). If this fails, the WP user is already gone — retrying would fail because there's no WP user to look up.

**Resolution:** Log into OJS admin and manually anonymise or delete the user account. Check the sync log for the OJS user ID or email to find them.

### WP Cron disabled — manual reconciliation

If `DISABLE_WP_CRON` is set (common on managed hosting that uses server-side cron), the daily reconciliation still runs — it's scheduled via Action Scheduler, which piggybacks on WP Cron but can also be triggered by a server-side cron hitting `wp-cron.php`.

**If reconciliation isn't running:**

1. Check that the server-side cron job hits `wp-cron.php` at least once daily.
2. Or run manually: `wp action-scheduler run --hooks=wpojs_daily_reconcile`
3. Check Action Scheduler status: WP Admin → Tools → Scheduled Actions → search for "wpojs".

### Action Scheduler queue stuck

If sync jobs are queuing up but not processing:

**Symptoms:** Log shows "queued" entries that never change to "completed" or "failed". Members report access delays.

**Diagnosis:**

1. WP Admin → Tools → Scheduled Actions.
2. Look for failed or stuck actions with the `wpojs_` prefix.
3. Check if Action Scheduler's runner is active (should have a "pending" action for `action_scheduler_run_queue`).

**Resolution:**

1. If the runner is missing: `wp action-scheduler run` to kick it.
2. If actions are stuck in "in-progress" for over 5 minutes: they may have timed out. Edit and re-save them to reset to "pending".
3. If persistent: check PHP error logs for fatal errors during sync, check OJS is reachable from WP server.
4. Nuclear option: `wp action-scheduler clean` to clear completed/failed actions (doesn't affect pending ones).

---

## Monitoring endpoint

The OJS plugin exposes a health check endpoint for uptime monitoring:

```
GET {ojs-base-url}/api/v1/wpojs/ping
```

- **No authentication required** — safe for external monitoring tools (Uptime Robot, Pingdom, etc.).
- Returns `200 {"status": "ok"}` when OJS and the plugin are running.
- If this returns 200 but `wp ojs-sync test-connection` fails on preflight, the issue is authentication or IP allowlisting (not reachability).

---

> **For IT / server admins only.** Support staff can skip this section.

## Email security note

Admin alert emails (sync failure notifications, daily digest) may contain member email addresses. To protect this PII:

- SMTP configuration must use TLS (port 587 with STARTTLS or port 465 with implicit TLS).
- Do not forward admin alert emails to public Slack channels, shared inboxes, or unencrypted mailing lists.
- The sync log in WP Admin also contains email addresses — access is restricted to `manage_options` capability (administrators only).
