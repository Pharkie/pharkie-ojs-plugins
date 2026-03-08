# Roadmap

## Done

- OJS plugin (`wpojs-subscription-api`) — code complete, reviewed
- WP plugin (`wpojs-sync`) — code complete, reviewed
- Docker dev environment
- Member dashboard widget (WooCommerce My Account)
- UI messages (OJS login hint, paywall hint, footer)
- Non-Docker setup guide — `docs/non-docker-setup.md`
- Dev environment clean rebuild verified — all 56 e2e tests passing
- Staging VPS on Hetzner (personal account) — fully scripted, smoke tests (17/17) + load tests passing
- Deployment automation — `init-vps.sh`, `deploy.sh`, `provision-vps.sh`, `smoke-test.sh`, `load-test.sh`
- Deployment docs — `docs/deployment.md` (public), `docs/private/staging-prod-setup.md` (private)
- Security hardening — no default passwords (fail-loud), env var validation in deploy.sh, .env permissions handling
- Smoke tests cover admin pages (WP + OJS) to catch .env/permission issues that WP-CLI misses
- HPOS fix for sample data seeding (disable → seed → sync → re-enable)
- Three-phase rollout plan — `docs/private/pre-production-checklist.md`
- Live WP plugin audit — `data export/live-wp-plugin-audit.md` (35 plugins, theme identified)

## Blocked — waiting on access

- [ ] **Hetzner Cloud account for SEA** — ID verification + payment card. Needed for production VPS.
- [ ] **Krystal WP SSH access** — pull SEAcomm/Helium theme files, check config
- [ ] **Krystal OJS staging SSH access** — pull OJS theme

## Phase 1: OJS on Hetzner + WP on Krystal

Deploy sync plugin to existing Krystal WP. OJS runs on new SEA-owned Hetzner VPS.

- [ ] Set up SEA Hetzner VPS (staging + production) — `scripts/init-vps.sh`
- [ ] Set up transactional email (Resend) — domain verification, SPF/DKIM/DMARC, OJS SMTP config
- [ ] Integrate SEAcomm/Helium themes into staging (requires Krystal access)
- [ ] Integrate OJS theme from Krystal staging (requires Krystal access)
- [ ] Add live plugins to `composer.json` (Gantry 5, Wordfence, Enhancer for WCS, others TBD)
- [ ] Test sync on staging with live-matching plugin set
- [ ] Deploy wpojs-sync plugin to Krystal WP (non-Docker install per `docs/non-docker-setup.md`)
- [ ] Configure WP settings: type mapping for all 6 WC products, manual roles, OJS URL
- [ ] Configure OJS: API key, allowed IPs (Krystal's outbound IP), subscription types
- [ ] `wp ojs-sync test-connection` → `sync --dry-run` → `sync` → `send-welcome-emails`
- [ ] Verify: new member flow, cancellation/expiry, on-hold/failed payment, welcome email delivery

## Phase 2: Hetzner WP (parallel, no domain)

Second Hetzner VPS running WP + OJS. Runs quietly on IP while Krystal stays live.

- [ ] All live plugins in `composer.json`
- [ ] SEAcomm theme active
- [ ] Stripe test mode configured
- [ ] Database + uploads migrated from Krystal
- [ ] `wp search-replace` for domain
- [ ] Smoke tests + load tests passing
- [ ] Full sync verified

## Phase 3: Domain switchover

- [ ] Lower DNS TTL 24h before
- [ ] Final database + uploads sync from Krystal
- [ ] DNS cutover to Hetzner
- [ ] Update Stripe webhook URL
- [ ] Update OJS allowed_ips (now Docker network)
- [ ] Monitor 24-48h
- [ ] Cancel Krystal hosting

## Staging test results (2026-03-02, external tester)

- [x] Connection test passes
- [x] Bulk backfill — existing members synced to OJS
- [x] OJS API endpoint mounted and responding
- [x] WP mapping configured: WC Product 23042 → OJS Type 1
- [ ] **New member flow** — create WCS subscription, verify OJS user + access created automatically
- [ ] **Cancellation/expiry** — cancel subscription, verify OJS access removed
- [ ] **On-hold / failed payment** — test payment failure scenarios
- [ ] **All products mapped** — configure all 6 WC products
- [ ] **Password flow** — verify welcome email delivery and password set process

## Playwright E2E browser tests (`e2e/`)

All passing (56/56):

- [x] Sync lifecycle — WCS activate/expire → OJS subscription status
- [x] OJS login — synced user sets password + logs in
- [x] WP dashboard — My Account journal access widget (active + inactive)
- [x] OJS UI messages — login hint, footer, paywall hint
- [x] Admin monitoring — Sync Log page stats, nonce, retry actions
- [x] OJS API request logging — log table + `wpojs_created_by_sync` flag
- [x] Email change sync — WP email change → OJS email updated
- [x] User deletion / GDPR — WP user delete → OJS user anonymised + disabled
- [x] Test connection — settings page AJAX test reports success
- [x] Error recovery — sync fails when OJS unreachable, succeeds on retry
- [x] Manual roles, settings behaviour, WP-CLI commands, API auth

## Future improvements

- [ ] Admin per-member sync status — Sync Log page shows global stats but no per-user view. Data exists in `wp_wpojs_sync_log` + `_wpojs_user_id` usermeta; just needs a UI.
- [ ] Follow-up email for members who haven't set OJS password — requires new OJS endpoint or direct DB query. Post-launch.
- [ ] Password sync — copy WP password hashes to OJS so members keep their existing password. Lazy rehash (phpass→bcrypt) on first OJS login for bulk-synced users; ongoing changes sent as bcrypt via hooks. Eliminates mandatory "set your password" email.

Dropped (not worth the complexity):
- ~~Batch bulk sync endpoint~~ — would reduce 1400 HTTP calls to ~14 but adds OJS-side complexity (transactions, partial failure). Adaptive throttling makes sequential sync fast enough (~3 min on Hetzner for 700 users).
- ~~Differential reconciliation~~ — full scan batches (chunks of 100). Fast enough.
- ~~API key rotation~~ — ops procedure, not a code feature.
- ~~On-hold grace period~~ — WCS retries failed payments automatically. Revisit if it generates support tickets.
