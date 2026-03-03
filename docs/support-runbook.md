# Support Runbook: WP-OJS Sync

Quick reference for support staff handling member access issues.

---

## Common scenarios

### "I can't access journal articles"

1. **Check WP subscription status** — WooCommerce → Subscriptions → search by email. Is the subscription `active`?
2. **Check sync log** — WP Admin → OJS Sync → Sync Log → search by email. Look for recent `activate` entries.
   - If last entry is `success`: the sync worked. Check OJS directly.
   - If last entry is `fail`: note the error, check OJS connectivity, retry (see below).
   - If no entries: the member's subscription status change may not have fired. Run manual sync (see below).
3. **Check OJS** — OJS Admin → Users & Roles → search by email. Is the user present? Check Subscriptions → Individual Subscriptions. Is the subscription `Active`?

### "I changed my email and lost access"

The sync plugin detects WP email changes and updates OJS automatically. If it didn't work:

1. Check the sync log for `email_change` entries.
2. If the new email already existed on OJS (409 conflict), the sync will have failed. Manually update the OJS user's email in OJS Admin → Users & Roles → Edit User.

### "I never received the welcome email"

1. Check OJS email configuration (SMTP relay, SPF/DKIM).
2. Resend via CLI: `wp ojs-sync sync --member=<email>` — this will re-trigger find-or-create (idempotent) and won't duplicate the user.
3. The member can also use OJS's "Forgot Password" link directly.

---

## WP-CLI commands

All commands must be run on the WP server (or via SSH). Prefix with `--allow-root` if running as root.

| Command | What it does |
|---|---|
| `wp ojs-sync test-connection` | Test connectivity to OJS (ping + preflight). Run this first. |
| `wp ojs-sync status` | Show sync stats: total synced, pending, failed, last reconciliation. |
| `wp ojs-sync sync --dry-run` | Preview what bulk sync would do (no changes). |
| `wp ojs-sync sync` | Run bulk sync for all active members. |
| `wp ojs-sync sync --member=<id or email>` | Sync a single member (sends welcome email if new). |
| `wp ojs-sync send-welcome-emails --dry-run` | Preview how many welcome emails would be sent. |
| `wp ojs-sync send-welcome-emails` | Send welcome emails to all synced users who haven't received one. |
| `wp ojs-sync reconcile` | Run reconciliation now (compare WP ↔ OJS, fix drift). |

---

## WP Admin pages

- **Settings**: WP Admin → OJS Sync → Settings. OJS URL, type mapping, test connection button.
- **Sync Log**: WP Admin → OJS Sync → Sync Log. Filterable by status, searchable by email.
- **Queue**: WP Admin → Tools → Scheduled Actions. Filter by group `wpojs-sync`.

---

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

## Troubleshooting

### Test connection fails

| Result | Meaning | Fix |
|---|---|---|
| "OJS not reachable" | WP can't reach OJS server at all | Check OJS URL in settings, verify OJS is running, check firewall |
| "Access denied (IP not allowlisted)" | OJS is reachable but blocking WP's IP | Add WP server IP to OJS `config.inc.php` `[wpojs] allowed_ips` |
| "Authentication failed" | API key mismatch | Verify `WPOJS_API_KEY` in `wp-config.php` matches `api_key_secret` in OJS `config.inc.php` |
| "Incompatible" | OJS plugin API changed | Run OJS `/preflight` to see which checks failed. May need OJS plugin update. |

### How to reset an OJS password

OJS has its own password system, independent of WordPress. If a member can't log in:

1. Direct the member to the **Forgot Password** link on the OJS login page (`/login/lostPassword`).
2. They'll receive a password reset email from OJS (not WP).
3. If OJS email isn't working, an OJS admin can reset the password manually: OJS Admin → Users & Roles → Edit User → Password.

### Welcome email customization

The welcome ("set your password") email uses OJS's built-in `PasswordResetRequested` mailable:

1. Go to OJS Admin → Workflow → Emails.
2. Search for "PasswordResetRequested".
3. Edit the subject and body as needed. Available variables: `{$recipientName}`, `{$passwordResetUrl}`.

The password reset token expiry is configured via `password_reset_timeout` in OJS `config.inc.php` (set to 7 days for bulk welcome emails).

### Daily digest shows failures

The digest email fires once per day if there were any sync failures in the last 24 hours.

1. Check the Sync Log for the specific failures.
2. Most common causes: OJS was temporarily down (transient, will auto-retry), or a configuration change broke connectivity.
3. If failures are ongoing, run `wp ojs-sync test-connection` to diagnose.

### OJS API log

OJS Admin → Website → Plugins → WP-OJS Subscription API → Status. Shows recent API requests, config health checks, and sync stats. Log entries older than 30 days are automatically cleaned up.

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

## Email security note

Admin alert emails (sync failure notifications, daily digest) may contain member email addresses. To protect this PII:

- SMTP configuration must use TLS (port 587 with STARTTLS or port 465 with implicit TLS).
- Do not forward admin alert emails to public Slack channels, shared inboxes, or unencrypted mailing lists.
- The sync log in WP Admin also contains email addresses — access is restricted to `manage_options` capability (administrators only).
