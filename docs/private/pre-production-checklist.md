# Pre-Production Checklist

Three-phase rollout: OJS first (on Hetzner, WP stays on Krystal), then WP migrates to Hetzner too.

---

## Phase 1: OJS on Hetzner + WP on Krystal

Deploy wpojs-sync plugin to the live Krystal WP. OJS runs on a new Hetzner VPS. Sync works across the internet. No WP migration yet — Krystal handles payments, themes, everything.

### 0. Get Krystal hosting access [BLOCKER]

Everything below requires access to the live WP server. Get this first.

- [ ] Get SSH access to Krystal hosting
- [ ] Get cPanel access (or equivalent file manager)
- [ ] Verify you can reach `wp-content/themes/` and `wp-content/plugins/`

### With access, gather:

- [ ] **Download SEAcomm theme** — `wp-content/themes/seacomm/` + `wp-content/themes/helium/` (parent). Needed for staging to match live layout.
- [ ] **Check Swift SMTP settings** — WP Admin → Swift SMTP settings page. Note SMTP host, port, from address. This tells us what email service live WP uses — may reuse for OJS.
- [ ] **Check Wordfence firewall rules** — WP Admin → Wordfence → Firewall. Look for outbound HTTP restrictions that could block WP→OJS API calls.
- [ ] **Check miniOrange OAuth config** — WP Admin → miniOrange OAuth settings. What is using WP as an OAuth server? If nothing, candidate for removal.
- [ ] **Get `wp-config.php`** — note any custom constants, cron setup, etc.

### 1. Deploy OJS production VPS

- [ ] `scripts/init-vps.sh --name=sea-prod --ssl`
- [ ] Create `.env` with production values (real domain, real API key, SMTP credentials)
- [ ] `scripts/deploy.sh --host=sea-prod --provision`
- [ ] Configure OJS `[wpojs]` section: `api_key_secret`, `allowed_ips` (Krystal's outbound IP), `wp_member_url`, `support_email`
- [ ] Create OJS subscription type(s)
- [ ] Set up DNS A record for OJS domain → Hetzner IP
- [ ] Verify SSL working (Caddy auto-provisions Let's Encrypt)

### 2. Set up email (OJS)

- [ ] Sign up for Resend (or reuse live WP's email service)
- [ ] Verify sending domain (add SPF/DKIM/DMARC DNS records)
- [ ] Set OJS SMTP credentials in `.env`
- [ ] Test email delivery (send to yourself first, verify DKIM passes)

### 3. Deploy wpojs-sync plugin to Krystal WP

This is the non-Docker deployment described in `docs/non-docker-setup.md`.

- [ ] Upload `plugins/wpojs-sync/` to Krystal's `wp-content/plugins/`
- [ ] Add `define('WPOJS_API_KEY', '...')` to `wp-config.php`
- [ ] Activate plugin in WP Admin
- [ ] Configure settings: OJS Base URL, product mappings for all 6 WC products:
  | WC Product ID | Product name | OJS Type ID |
  |---|---|---|
  | 1892 | | |
  | 1924 | | |
  | 1927 | | |
  | 23040 | | |
  | 23041 | | |
  | 23042 | | |
- [ ] Configure manual role mappings (if applicable)
- [ ] Check Wordfence isn't blocking outbound calls to OJS

### 4. Verify and launch sync

- [ ] `wp ojs-sync test-connection` — verify connectivity, auth, IP allowlist
- [ ] `wp ojs-sync sync --dry-run --yes` — preview bulk sync
- [ ] Review output — member count matches expectations
- [ ] `wp ojs-sync sync --yes` — run bulk sync
- [ ] `wp ojs-sync status` — verify counts
- [ ] `wp ojs-sync reconcile` — check for drift
- [ ] Test new member flow (create subscription → verify OJS access)
- [ ] Test cancellation flow (cancel → verify OJS access removed)
- [ ] Test on-hold / failed payment scenario
- [ ] `wp ojs-sync send-welcome-emails --dry-run` — preview count
- [ ] Send test email to yourself
- [ ] `wp ojs-sync send-welcome-emails` — send to all members

### 5. Post-launch monitoring

- [ ] Check WP Admin → OJS Sync → Sync Log for failures
- [ ] Verify Action Scheduler processing jobs (WP Admin → Tools → Scheduled Actions)
- [ ] Monitor email delivery (Resend dashboard — bounces, complaints)
- [ ] Verify non-member purchase flow still works (OJS paywall → buy article)

---

## Phase 2: Prepare Hetzner WP (runs in parallel)

Second Hetzner VPS running WP + OJS. No domain yet — runs on IP, tested in parallel while Krystal stays live.

### 0. Staging: match live site

Before building production, get staging to mirror live WP as closely as possible.

- [ ] Add SEAcomm + Helium themes to repo (`wordpress/web/app/themes/`)
- [ ] Add Gantry 5 to `composer.json`: `"wpackagist-plugin/gantry5": "^5.5"`
- [ ] Add all live plugins we're keeping to `composer.json` (see plugin audit)
- [ ] Add Wordfence + Enhancer for WCS to staging
- [ ] Set SEAcomm as active theme in setup script
- [ ] Deploy to staging, run smoke tests, verify sync + widget rendering
- [ ] Test with Stripe in test mode

### 1. Decide which live plugins to keep

From the plugin audit (`data export/live-wp-plugin-audit.md`):

**Must keep (required for functionality):**
- WooCommerce, WooCommerce Subscriptions, WooCommerce Memberships
- Ultimate Member, UM WooCommerce, UM Notifications
- WooCommerce Stripe Gateway
- Gantry 5 Framework
- Wordfence Security
- Enhancer for WooCommerce Subscriptions
- wpojs-sync (our plugin)

**Probably keep:**
- Yoast SEO
- The Events Calendar + Pro + Event Tickets + Event Tickets Plus
- Ninja Forms
- 301 Redirects (has configured redirects)
- Ivory Search
- PDF Embedder + PDF Embedder Secure
- Donation for WooCommerce
- MailChimp for WooCommerce Memberships
- WP Mail Logging
- Disable Comments
- Swift SMTP (or replace with Resend config)

**Candidates for removal:**
- Classic Editor, Classic Widgets (test without)
- View Admin As (dev tool, not for production)
- Maintenance (if not actively used)
- Export and Import Users and Customers (one-time migration tool)
- Promoter Site Health (leftover)
- WooCommerce.com Update Manager (check if needed)
- WooCommerce Legacy REST API (deprecated, check if anything uses it)
- miniOrange OAuth (check if anything depends on it)

### 2. Set up production Hetzner WP

- [ ] `scripts/init-vps.sh --name=sea-prod-wp --ssl` (or co-locate with OJS on same VPS)
- [ ] All plugins in `composer.json`
- [ ] SEAcomm + Helium themes deployed
- [ ] Stripe test mode configured
- [ ] Email (Swift SMTP or Resend) configured

### 3. Migrate WP data from Krystal

Requires SSH access to Krystal. Add an SSH config entry:

```
Host krystal
  HostName <krystal-ip-or-hostname>
  User <your-krystal-user>
  IdentityFile ~/.ssh/<key>
```

#### What to pull

| Data | Where on Krystal | Size estimate | How |
|---|---|---|---|
| **Database** | MySQL/MariaDB | ~100-500 MB | `wp db export` via WP-CLI |
| **Uploads** (media library) | `wp-content/uploads/` | Could be 1-10 GB+ | `rsync` |
| **SEAcomm theme** | `wp-content/themes/seacomm/` | Small | `rsync` |
| **Helium parent theme** | `wp-content/themes/helium/` | Small | `rsync` |
| **wp-config.php** | WP root | Reference only — note custom constants | `scp` |
| **Paid plugin configs** | DB (options table) | Included in DB dump | — |
| **.htaccess** | WP root | Reference for redirect rules | `scp` |

#### Export commands (run via SSH to Krystal)

```bash
KRYSTAL="krystal"
KRYSTAL_WP_PATH="/path/to/wordpress"  # Find via: ssh krystal "find /home -name wp-config.php 2>/dev/null"

# 1. Database dump (via WP-CLI if available, otherwise mysqldump)
ssh $KRYSTAL "cd $KRYSTAL_WP_PATH && wp db export /tmp/wp-export.sql --allow-root"
scp $KRYSTAL:/tmp/wp-export.sql "data export/krystal-wp-export.sql"

# Or if WP-CLI not available — get credentials from wp-config.php first:
# ssh $KRYSTAL "mysqldump -u <user> -p <dbname> > /tmp/wp-export.sql"

# 2. Uploads (media library) — can be large, rsync handles resume
rsync -az --progress -e ssh \
  $KRYSTAL:$KRYSTAL_WP_PATH/wp-content/uploads/ \
  "data export/krystal-uploads/"

# 3. Themes
rsync -az -e ssh $KRYSTAL:$KRYSTAL_WP_PATH/wp-content/themes/seacomm/ \
  wordpress/web/app/themes/seacomm/
rsync -az -e ssh $KRYSTAL:$KRYSTAL_WP_PATH/wp-content/themes/helium/ \
  wordpress/web/app/themes/helium/

# 4. Reference files
scp $KRYSTAL:$KRYSTAL_WP_PATH/wp-config.php "data export/krystal-wp-config.php"
scp $KRYSTAL:$KRYSTAL_WP_PATH/.htaccess "data export/krystal-htaccess" 2>/dev/null || true
```

#### Import into Hetzner WP

```bash
PROD="sea-prod"
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.staging.yml"

# 1. Copy DB dump to VPS
scp "data export/krystal-wp-export.sql" $PROD:/tmp/

# 2. Import database
ssh $PROD "cd /opt/wp-ojs-sync && $COMPOSE exec -T wp wp db import /tmp/krystal-wp-export.sql --allow-root"

# 3. Search-replace old domain → new domain
ssh $PROD "cd /opt/wp-ojs-sync && $COMPOSE exec -T wp wp search-replace \
  'https://old-domain.org' 'https://new-domain.org' --all-tables --allow-root"

# 4. Sync uploads into the WP uploads volume
rsync -az --progress -e ssh \
  "data export/krystal-uploads/" \
  $PROD:/opt/wp-ojs-sync/wordpress/web/app/uploads/

# 5. Fix permissions
ssh $PROD "cd /opt/wp-ojs-sync && $COMPOSE exec -T wp chown -R www-data:www-data /var/www/html/web/app/uploads"

# 6. Flush caches and permalinks
ssh $PROD "cd /opt/wp-ojs-sync && $COMPOSE exec -T wp wp cache flush --allow-root"
ssh $PROD "cd /opt/wp-ojs-sync && $COMPOSE exec -T wp wp rewrite flush --allow-root"
```

#### Verify after import

- [ ] Pages load (homepage, My Account, shop)
- [ ] Products exist with correct prices
- [ ] User accounts work (login as a real member)
- [ ] WooCommerce Subscriptions intact (check subscription list in WP admin)
- [ ] Media library images display correctly
- [ ] Yoast SEO data preserved (check a few posts)
- [ ] 301 redirects still work
- [ ] Events Calendar events present

### 4. Configure payments

- [ ] Stripe live API keys in WP settings
- [ ] Stripe webhook pointed at Hetzner WP URL
- [ ] Test a payment (subscription renewal or new purchase)
- [ ] Verify WCS subscription payment methods still work after migration

### 5. Verify everything works

- [ ] `scripts/smoke-test.sh --host=sea-prod-wp`
- [ ] `scripts/load-test.sh --host=sea-prod-wp`
- [ ] Full sync round-trip test
- [ ] Login as a real member, verify My Account widget, OJS access
- [ ] Non-member purchase flow
- [ ] Email delivery (password reset, order confirmation)

---

## Phase 3: Domain switchover

Cut over from Krystal to Hetzner. This is the point of no return.

### Pre-switchover

- [ ] Both systems running in parallel, Hetzner verified working on IP
- [ ] DNS TTL lowered to 300s (5 min) at least 24h before switchover
- [ ] Backup of both Krystal and Hetzner databases
- [ ] Maintenance mode on Krystal WP (prevent data changes during cutover)

### Switchover

```bash
# 1. Final data sync (while Krystal is in maintenance mode)
ssh krystal "cd $KRYSTAL_WP_PATH && wp db export /tmp/wp-final.sql --allow-root"
scp krystal:/tmp/wp-final.sql /tmp/wp-final.sql
scp /tmp/wp-final.sql sea-prod:/tmp/

# 2. Import + search-replace
ssh sea-prod "cd /opt/wp-ojs-sync && $COMPOSE exec -T wp wp db import /tmp/wp-final.sql --allow-root"
ssh sea-prod "cd /opt/wp-ojs-sync && $COMPOSE exec -T wp wp search-replace \
  'https://old-domain.org' 'https://new-domain.org' --all-tables --allow-root"

# 3. Final uploads sync (delta only — fast if you've synced before)
rsync -az --progress -e ssh krystal:$KRYSTAL_WP_PATH/wp-content/uploads/ sea-prod:/opt/wp-ojs-sync/wordpress/web/app/uploads/
ssh sea-prod "cd /opt/wp-ojs-sync && $COMPOSE exec -T wp chown -R www-data:www-data /var/www/html/web/app/uploads"
```

- [ ] Final database export from Krystal → import to Hetzner
- [ ] Final `wp-content/uploads/` sync
- [ ] `wp search-replace` for new domain
- [ ] Update DNS A records: WP domain → Hetzner IP
- [ ] Caddy auto-provisions SSL certificate
- [ ] Update Stripe webhook URL to new domain
- [ ] Update OJS `allowed_ips` if WP IP changed (now localhost/Docker network)
- [ ] Update WP `WPOJS_BASE_URL` if OJS is now on same server (use Docker network)

### Post-switchover

- [ ] Verify DNS propagation (`dig` / `nslookup`)
- [ ] Full smoke test
- [ ] Test payment flow with real Stripe
- [ ] Test sync (new subscription → OJS access)
- [ ] Monitor for 24-48h
- [ ] Cancel Krystal hosting (once confident)

---

## Live WP plugin audit reference [DONE]

Full audit saved in `data export/live-wp-plugin-audit.md`. 35 plugins on live, captured 2026-03-07.

**Theme:** SEAcomm v2022.1 — Gantry5/Helium child theme. Requires Gantry 5 Framework plugin + Helium parent theme.
