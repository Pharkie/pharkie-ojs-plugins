# Roadmap

## Done

- OJS plugin (`wpojs-subscription-api`) — code complete, reviewed
- WP plugin (`wpojs-sync`) — code complete, reviewed
- Docker dev environment
- Member dashboard widget (WooCommerce My Account)
- UI messages (OJS login hint, paywall hint, footer)
- Non-Docker setup guide — `docs/non-docker-setup.md`
- Dev environment clean rebuild verified — all 61 e2e tests passing
- Staging VPS on Hetzner (Michal's org account) — fully scripted, smoke tests (22/22) + load tests passing, bulk sync 684/684 clean
- Deployment automation — `init-vps.sh`, `deploy.sh`, `provision-vps.sh`, `smoke-test.sh`, `load-test.sh`
- Deployment docs — `docs/vps-deployment.md` (public), `docs/private/staging-prod-setup.md` (private)
- Security hardening — no default passwords (fail-loud), env var validation in deploy.sh, .env permissions handling
- Smoke tests cover admin pages (WP + OJS) to catch .env/permission issues that WP-CLI misses
- HPOS fix for sample data seeding (disable → seed → sync → re-enable)
- Three-phase rollout plan — `docs/private/pre-production-checklist.md`
- Live WP plugin audit — `data export/live-wp-plugin-audit.md` (35 plugins, theme identified)
- Password hash sync (bulk, new members, ongoing password changes) — WP hashes sent to OJS, members log in with existing WP password, lazy rehash to cost 12 on first OJS login
- Dev-vs-live branding parity — journal metadata, theme, nav menu, editorial team, sidebar blocks (Information + event banners), "For Advertisers" static page + sidebar link

## Blocked — waiting on access

- [x] ~~**Hetzner Cloud account for SEA**~~ — Done. Using Michal's org account (`sea-michal`).
- [x] ~~**Krystal WP SSH access**~~ — Working (`sea-wp-live`, port 722).
- [x] ~~**OJS branding/theme**~~ — Fully replicated in `setup-ojs.sh` (config-as-code). No SSH to PKP-hosted live OJS needed.

## Next: OJS live on Hetzner

OJS is ready to go live. Branding matches the PKP-hosted live site. Deploy to Hetzner, point `journal.existentialanalysis.org.uk` at it.

- [x] ~~Set up SEA Hetzner VPS (staging)~~ — `scripts/init-vps.sh`, deployed and verified 2026-03-10
- [ ] Deploy OJS to Hetzner staging — `deploy.sh --host=sea-staging --env-file=.env.staging`
- [ ] SSL certificate (Let's Encrypt) for `journal.existentialanalysis.org.uk`
- [ ] Smoke tests + manual review of staging OJS
- [ ] DNS cutover — point `journal.existentialanalysis.org.uk` to Hetzner
- [ ] Set up transactional email (Resend) — domain verification, SPF/DKIM/DMARC, OJS SMTP config
- [ ] Monitor 24-48h
- [ ] Decommission PKP hosting

## Later: WP sync (Phase 2)

Connect Krystal WP to the new Hetzner OJS via the sync plugins. WP stays on Krystal for now.

- [ ] Deploy wpojs-sync plugin to Krystal WP (non-Docker install per `docs/non-docker-setup.md`)
- [ ] Configure WP settings: type mapping for all 6 WC products, manual roles, OJS URL
- [ ] Configure OJS: API key, allowed IPs (Krystal's outbound IP), subscription types
- [ ] `wp ojs-sync test-connection` → `sync --dry-run` → `sync`
- [ ] Verify: new member flow, cancellation/expiry, on-hold/failed payment, OJS login with WP password

## Later: WP migration to Hetzner (Phase 3)

Move WP from Krystal to Hetzner. Both WP + OJS on same VPS.

- [ ] Integrate SEAcomm/Helium theme (pull from Krystal via SSH)
- [ ] Add live plugins to `composer.json` (Gantry 5, Wordfence, Enhancer for WCS, others TBD)
- [ ] Stripe test mode configured
- [ ] Database + uploads migrated from Krystal
- [ ] `wp search-replace` for domain
- [ ] DNS cutover for `community.existentialanalysis.org.uk`
- [ ] Update OJS allowed_ips (now Docker network)
- [ ] Cancel Krystal hosting

## Staging test results (2026-03-10, sea-michal account)

- [x] Connection test passes
- [x] Bulk sync — 684/684 members synced to OJS (50s, 0 failures)
- [x] OJS API endpoint mounted and responding
- [x] All 6 products mapped to OJS subscription types
- [x] Smoke tests 22/22 — includes full sync round-trip (create, subscribe, expire, anonymise)
- [x] OJS login with WP password works after bulk sync
- [ ] **New member flow** — create WCS subscription, verify OJS user + access created automatically (needs live testing)
- [ ] **Cancellation/expiry** — cancel subscription, verify OJS access removed (needs live testing)
- [ ] **On-hold / failed payment** — test payment failure scenarios (needs live testing)

## Playwright E2E browser tests (`e2e/`)

All passing (61/61):

- [x] Sync lifecycle — WCS activate/expire → OJS subscription status
- [x] OJS login — synced user logs in with WP password (no password setup needed)
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

Dropped (not worth the complexity):
- ~~Batch bulk sync endpoint~~ — would reduce 1400 HTTP calls to ~14 but adds OJS-side complexity (transactions, partial failure). Load-based backpressure + adaptive throttling makes sequential sync fast enough (~40s on Hetzner for 684 users).
- ~~Differential reconciliation~~ — full scan batches (chunks of 100). Fast enough.
- ~~API key rotation~~ — ops procedure, not a code feature.
- ~~On-hold grace period~~ — WCS retries failed payments automatically. Revisit if it generates support tickets.
