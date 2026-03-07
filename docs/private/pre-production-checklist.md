# Pre-Production Checklist

Information to gather and steps to complete before deploying to production. Items marked **[GATHER]** require information from the live WP site.

---

## 1. Live WP plugin audit [GATHER]

Get the full active plugin list from the live WP site. Go to **WP Admin → Tools → Site Health → Info → Active Plugins** (copyable text list) or screenshot **WP Admin → Plugins**.

Check each plugin against these categories:

### Likely to affect sync

| Category | Examples | What to check |
|---|---|---|
| **Caching** | WP Super Cache, W3 Total Cache, LiteSpeed Cache, Redis Object Cache | Could cache REST API responses or stale WP options. Object cache could delay Action Scheduler pickup. May need cache exclusion rules for `/wp-json/` and Action Scheduler. |
| **Security / WAF** | Wordfence, Sucuri, iThemes Security, All In One Security | Could block outbound HTTP to OJS, rate-limit WP-CLI, or flag sync API calls as suspicious. Check firewall rules and whitelists. |
| **Other WooCommerce extensions** | Any plugin hooking into `woocommerce_subscription_status_*` | Could conflict with sync hooks or change execution order. List all WC extensions active on live. |
| **SMTP / email plugins** | WP Mail SMTP, FluentSMTP, Post SMTP | Need to know which service/credentials are used — may reuse for OJS. |
| **Cron plugins** | WP Crontrol, Advanced Cron Manager | Could interfere with Action Scheduler's cron runner. |
| **UM extensions** | Beyond um-notifications and um-woocommerce | Could modify user creation/deletion hooks that sync relies on. |

### Unlikely to affect sync (but note them)

- SEO plugins (Yoast, Rank Math)
- Form plugins (Gravity Forms, WPForms)
- Page builders (Elementor, Beaver Builder)
- Analytics (Google Analytics, MonsterInsights)
- Backup plugins (UpdraftPlus, BlogVault)

### Action items

- [ ] Get full active plugin list from live WP
- [ ] Identify any plugins in the "likely to affect" categories above
- [ ] Install same plugins on staging and test sync still works
- [ ] Note any custom `wp-config.php` constants (beyond standard WP defaults)

---

## 2. Live WP configuration [GATHER]

| Item | How to check | Why it matters |
|---|---|---|
| **Active theme** | WP Admin → Appearance → Themes | Custom WooCommerce template overrides or subscription hooks |
| **PHP version** | Tools → Site Health → Info → Server | Plugin requires PHP 8.2+ |
| **PHP memory limit** | Tools → Site Health → Info → Server | Bulk sync needs adequate memory |
| **PHP max execution time** | Tools → Site Health → Info → Server | Long-running WP-CLI commands |
| **WP-Cron setup** | Check `wp-config.php` for `DISABLE_WP_CRON` | Action Scheduler relies on WP-Cron (or system cron replacement) |
| **Multisite?** | Tools → Site Health → Info → WordPress | Affects plugin activation and hooks |
| **WP version** | Dashboard → Updates | Should be current |
| **WC version** | Plugins page | Plugin tested against WC 9.8+ |

### Action items

- [ ] Record PHP version, memory limit, max execution time
- [ ] Check WP-Cron setup (native vs system cron)
- [ ] Check if multisite
- [ ] Note active theme name

---

## 3. Email setup

### Gather from live site [GATHER]

- [ ] How does live WP currently send emails? (PHP `mail()`, SMTP plugin, hosting relay?)
- [ ] Which SMTP service/credentials? (Mailgun, Postmark, SES, hosting SMTP?)
- [ ] What sending domain? (e.g. `yourdomain.org` — need same domain's DNS for OJS)
- [ ] Are SPF/DKIM/DMARC records already set up for that domain?

### Configure for OJS

- [ ] Choose SMTP service (can reuse live WP's service if it supports multiple senders)
- [ ] Add SPF/DKIM/DMARC DNS records for OJS sending domain (if different from WP's)
- [ ] Set OJS SMTP credentials in `.env`:
  ```
  OJS_SMTP_ENABLED=On
  OJS_SMTP_HOST=smtp.example.com
  OJS_SMTP_PORT=587
  OJS_SMTP_AUTH=tls
  OJS_SMTP_USER=...
  OJS_SMTP_PASSWORD=...
  OJS_MAIL_FROM=journal@yourdomain.org
  ```
- [ ] Test OJS email delivery (send to yourself first)
- [ ] Verify DKIM passes (Gmail → "Show original" → `dkim=pass`)

### Configure for WP (if needed)

- [ ] If live WP uses PHP `mail()` and hosting handles it: check if Docker WP needs an SMTP plugin
- [ ] If live WP uses an SMTP plugin: install same plugin on staging with same credentials
- [ ] Test WP email delivery from staging

---

## 4. WP plugin settings for production

- [ ] **OJS Base URL** — production OJS URL (with journal path)
- [ ] **Product mappings** — map all 6 WC products to OJS subscription types:
  | WC Product ID | Product name | OJS Type ID |
  |---|---|---|
  | 1892 | | |
  | 1924 | | |
  | 1927 | | |
  | 23040 | | |
  | 23041 | | |
  | 23042 | | |
- [ ] **Manual role mappings** (if applicable)
- [ ] **API key** — set `WPOJS_API_KEY` in `wp-config.php`

---

## 5. OJS configuration for production

- [ ] `config.inc.php` `[wpojs]` section:
  - `api_key_secret` — must match WP's `WPOJS_API_KEY`
  - `allowed_ips` — WP server's outbound IP
  - `wp_member_url` — production WP URL
  - `support_email` — support contact shown in paywall message
- [ ] At least one subscription type created in OJS admin
- [ ] Journal metadata (name, ISSN, contact info) set correctly

---

## 6. Pre-launch verification

Run in order:

- [ ] `wp ojs-sync test-connection` — verifies connectivity, auth, IP allowlist, compatibility
- [ ] `wp ojs-sync sync --dry-run --yes` — preview what bulk sync would do
- [ ] Review dry-run output — member count matches expectations
- [ ] `wp ojs-sync sync --yes` — run bulk sync
- [ ] `wp ojs-sync status` — verify sync counts
- [ ] `wp ojs-sync reconcile` — check for drift
- [ ] Test new member flow manually (create subscription → verify OJS access)
- [ ] Test cancellation flow (cancel subscription → verify OJS access removed)
- [ ] `wp ojs-sync send-welcome-emails --dry-run` — preview email count
- [ ] Send test email to yourself first
- [ ] `wp ojs-sync send-welcome-emails` — send to all members

---

## 7. Post-launch monitoring

- [ ] Check WP Admin → OJS Sync → Sync Log for failures
- [ ] Verify Action Scheduler is processing jobs (WP Admin → Tools → Scheduled Actions)
- [ ] Monitor email delivery (check service dashboard for bounces/complaints)
- [ ] Check OJS API log for errors (`wpojs_api_log` table)
- [ ] Verify non-member purchase flow still works (OJS paywall → buy article)
