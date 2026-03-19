# Pre-Production Checklist

Three-phase rollout: OJS first (on Hetzner, WP stays on Krystal), then WP migrates to Hetzner too.

---

## Phase 1: OJS on Hetzner + WP on Krystal

Deploy wpojs-sync plugin to the live Krystal WP. OJS runs on a new Hetzner VPS. Sync works across the internet. No WP migration yet — Krystal handles payments, themes, everything.

### 0. Krystal hosting access [DONE]

- [x] SSH access — `ssh sea-wp-live` (port 722, user `existent`)
- [x] WP-CLI working — `cd community.existentialanalysis.org.uk && wp ...`
- [x] Plugin dir writable — confirmed
- [x] Action Scheduler available — bundled with WooCommerce, running every minute

**Important:** The membership WP site is at `~/community.existentialanalysis.org.uk/`, NOT `~/public_html/` (which is a separate brochure site). Always `cd community.existentialanalysis.org.uk` for WP-CLI commands.

**Note:** `proc_open` is disabled on Krystal (PHP security). `wp db query` won't work — use `wp eval` with `$wpdb` instead. This does not affect the sync plugin or WP-CLI commands.

### With access, gather:

- [x] **Download SEAcomm theme** — `wp-content/themes/seacomm/` + `wp-content/themes/helium/` (parent). Already pulled to `wordpress/themes/`.
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

This is the non-Docker deployment described in `docs/non-docker-setup.md`. Target: `community.existentialanalysis.org.uk` (NOT `public_html`).

- [ ] Upload: `scp -r plugins/wpojs-sync/ sea-wp-live:community.existentialanalysis.org.uk/wp-content/plugins/wpojs-sync/`
- [ ] Add `define('WPOJS_API_KEY', '...')` to `~/community.existentialanalysis.org.uk/wp-config.php`
- [ ] Activate: `ssh sea-wp-live "cd community.existentialanalysis.org.uk && wp plugin activate wpojs-sync"`
- [ ] Configure settings (WP Admin → OJS Sync): OJS Base URL, product mappings for all 6 WC products:
  | WC Product ID | Product name | OJS Type ID |
  |---|---|---|
  | 1892 | UK Membership (no directory listing) | TBD |
  | 1924 | International Membership (no directory listing) | TBD |
  | 1927 | Student Membership (no directory listing) | TBD |
  | 23040 | Student Membership (with directory listing) | TBD |
  | 23041 | International Membership (with directory listing) | TBD |
  | 23042 | UK Membership (with directory listing) | TBD |
- [ ] Configure manual role mappings: `um_custom_role_7` (Exco/life UK, 1 user), `um_custom_role_8` (Exco/life intl, 0), `um_custom_role_9` (Exco/life student, 0)
- [ ] Check Wordfence isn't blocking outbound HTTPS calls to OJS

### 4. Verify and launch sync

- [ ] `wp ojs-sync test-connection` — verify connectivity, auth, IP allowlist
- [ ] `wp ojs-sync sync --bulk --dry-run` — preview bulk sync
- [ ] Review output — member count matches expectations
- [ ] `wp ojs-sync sync --bulk --yes` — run bulk sync
- [ ] `wp ojs-sync status` — verify counts
- [ ] `wp ojs-sync reconcile` — check for drift
- [ ] Test new member flow (create subscription → verify OJS access)
- [ ] Test cancellation flow (cancel → verify OJS access removed)
- [ ] Test on-hold / failed payment scenario
- [ ] Verify members can log into OJS with WP password (spot-check 2-3 accounts)
- [ ] Send member announcement via normal channel (newsletter/email)

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

SSH access is already configured: `ssh sea-wp-live` (port 722, user `existent`, key `~/.ssh/hetzner`).

**Important:** WP root is `~/community.existentialanalysis.org.uk/`, NOT `~/public_html/`. Note: `wp db export` won't work because `proc_open` is disabled on Krystal — use `wp eval` with `$wpdb` for queries, or export via cPanel/phpMyAdmin.

#### What to pull

| Data | Where on Krystal | Size estimate | How |
|---|---|---|---|
| **Database** | MySQL/MariaDB (DB: `existent_2021`) | ~100-500 MB | cPanel export or `mysqldump` via cPanel terminal |
| **Uploads** (media library) | `community.existentialanalysis.org.uk/wp-content/uploads/` | Could be 1-10 GB+ | `rsync` |
| **SEAcomm theme** | `community.existentialanalysis.org.uk/wp-content/themes/seacomm/` | Small | Already pulled to `wordpress/themes/` |
| **Helium parent theme** | `community.existentialanalysis.org.uk/wp-content/themes/helium/` | Small | Already pulled to `wordpress/themes/` |
| **wp-config.php** | `community.existentialanalysis.org.uk/` | Reference only — note custom constants | `scp` |
| **Paid plugin configs** | DB (options table) | Included in DB dump | — |
| **.htaccess** | `community.existentialanalysis.org.uk/` | Reference for redirect rules | `scp` |

#### Export commands

```bash
WP_ROOT="community.existentialanalysis.org.uk"

# 1. Database dump — proc_open is disabled, so wp db export won't work.
#    Option A: Export via cPanel phpMyAdmin (database: existent_2021)
#    Option B: Use cPanel Terminal (not SSH) which may have proc_open enabled
#    Option C: mysqldump if credentials available
scp sea-wp-live:/tmp/wp-export.sql "data export/krystal-wp-export.sql"

# 2. Uploads (media library) — can be large, rsync handles resume
rsync -az --progress -e ssh \
  sea-wp-live:$WP_ROOT/wp-content/uploads/ \
  "data export/krystal-uploads/"

# 3. Themes (already done)
# rsync -az -e ssh sea-wp-live:$WP_ROOT/wp-content/themes/seacomm/ wordpress/themes/seacomm/
# rsync -az -e ssh sea-wp-live:$WP_ROOT/wp-content/themes/helium/ wordpress/themes/helium/

# 4. Reference files
scp sea-wp-live:$WP_ROOT/wp-config.php "data export/krystal-wp-config.php"
scp sea-wp-live:$WP_ROOT/.htaccess "data export/krystal-htaccess" 2>/dev/null || true
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
