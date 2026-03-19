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
- [ ] DNS cutover — point `journal.existentialanalysis.org.uk` to Hetzner (via cPanel UAPI over SSH, see below)
- [ ] Set up transactional email (Resend) — domain verification, SPF/DKIM/DMARC, OJS SMTP config
- [ ] Monitor 24-48h
- [ ] Decommission PKP hosting

## Next after OJS: WP sync (Phase 2)

Connect Krystal WP (`community.existentialanalysis.org.uk`) to Hetzner OJS via the sync plugins. WP stays on Krystal for now. The membership site is on the `community` subdomain, not `public_html` — see "Live WP environment" below for details.

### OJS-side prep

- [ ] Configure `config.inc.php` `[wpojs]` section: `api_key_secret`, `allowed_ips` (Krystal's outbound IP), `wp_member_url`, `support_email`
- [ ] Create OJS subscription type(s) — at minimum one "Individual Membership" type
- [ ] Verify OJS ping endpoint reachable from Krystal: `curl https://<ojs-domain>/index.php/ea/api/v1/wpojs/ping`

### WP-side deployment

- [ ] Upload plugin: `scp -r plugins/wpojs-sync/ sea-wp-live:community.existentialanalysis.org.uk/wp-content/plugins/wpojs-sync/`
- [ ] Add `define('WPOJS_API_KEY', '...')` to `wp-config.php` (must match OJS `api_key_secret`)
- [ ] Activate: `ssh sea-wp-live "cd community.existentialanalysis.org.uk && wp plugin activate wpojs-sync"`
- [ ] Configure settings (WP Admin → OJS Sync):
  - OJS Base URL (HTTPS, includes journal path)
  - Product mappings — all 6 WC subscription products:
    | WC Product ID | Product name | OJS Type ID |
    |---|---|---|
    | 1892 | UK Membership (no directory listing) | TBD |
    | 1924 | International Membership (no directory listing) | TBD |
    | 1927 | Student Membership (no directory listing) | TBD |
    | 23040 | Student Membership (with directory listing) | TBD |
    | 23041 | International Membership (with directory listing) | TBD |
    | 23042 | UK Membership (with directory listing) | TBD |
  - Manual roles: `um_custom_role_7` (Exco/life UK), `um_custom_role_8` (Exco/life international), `um_custom_role_9` (Exco/life student) — currently 1 user total
- [ ] Check Wordfence isn't blocking outbound HTTPS to OJS domain

### Verify and launch

- [ ] `ssh sea-wp-live "cd community.existentialanalysis.org.uk && wp ojs-sync test-connection"`
- [ ] `ssh sea-wp-live "cd community.existentialanalysis.org.uk && wp ojs-sync sync --bulk --dry-run"` — expect ~699 members
- [ ] Review dry run output, confirm member count matches expectations
- [ ] `ssh sea-wp-live "cd community.existentialanalysis.org.uk && wp ojs-sync sync --bulk --yes"` — run real bulk sync
- [ ] `ssh sea-wp-live "cd community.existentialanalysis.org.uk && wp ojs-sync status"` — verify counts
- [ ] Spot-check 2-3 members can log into OJS with WP password
- [ ] Test new member flow (create WCS subscription → verify OJS access created)
- [ ] Test cancellation flow (cancel → verify OJS access removed)
- [ ] Test on-hold / failed payment scenario
- [ ] Verify non-member OJS purchase flow still works (paywall → buy article)

### Post-launch monitoring

- [ ] Check WP Admin → OJS Sync → Sync Log for failures
- [ ] Verify Action Scheduler processing jobs (WP Admin → Tools → Scheduled Actions)
- [ ] Monitor daily digest emails for sync failures
- [ ] Run `wp ojs-sync reconcile` manually after 24h to check for drift

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

## Live WP environment (community.existentialanalysis.org.uk)

Investigated 2026-03-19. The membership site is on Krystal at `~/community.existentialanalysis.org.uk/` (NOT `~/public_html/`, which is a separate brochure site). SSH: `sea-wp-live` (port 722, user `existent`).

- **WP 6.9.4**, **PHP 8.3.30** — exceeds plugin requirements
- **WooCommerce** 10.6.1, **WCS** 8.5.0, **Ultimate Member** 2.11.2, **Stripe** 10.5.2, **Action Scheduler** (bundled, running)
- **1,419 users**, 12 admins, **699 active subscriptions** with 6 membership products
- **6 subscription products** (all annual): UK £50, International £60, Student £35 — each in "with listing" and "no listing" variants
- **9 UM roles** including 3 manual Exco/life member roles (1 user total)
- **Wordfence** active — check outbound rules before connecting to OJS
- **`proc_open` disabled** — `wp db query` won't work, use `wp eval` with `$wpdb` instead. Does not affect sync plugin.
- **WP cron** via page loads (no system cron for WP). Action Scheduler runs every minute.
- Plugin dir writable, no existing `WPOJS_API_KEY` in wp-config.php
- **HPOS disabled** — subscriptions in `wp_posts`, not `wc_orders`. Sync plugin handles both. Don't enable on Krystal; enable at Phase 3 (WP→Hetzner).

## DNS management

Nameservers are Krystal's (`ns1.krystal.uk`, `ns2.krystal.uk`). All DNS for `existentialanalysis.org.uk` is managed via cPanel on the Krystal account — no registrar or third-party access needed.

**Backup:** `docs/private/dns-backup-2026-03-19.txt` — full zone snapshot before changes.

**Read zone:** `ssh sea-wp-live "uapi DNS parse_zone zone=existentialanalysis.org.uk"`

**Update A record** (two-step: get serial, then edit):
```bash
# 1. Get current serial from parse_zone SOA record
ssh sea-wp-live "uapi DNS parse_zone zone=existentialanalysis.org.uk --output=json" | python3 -c "..."
# 2. Edit with matching serial and line_index
ssh sea-wp-live 'uapi DNS mass_edit_zone zone=existentialanalysis.org.uk serial=<SERIAL> edit="{\"line_index\":<LINE>,\"dname\":\"journal\",\"record_type\":\"A\",\"data\":[\"<IP>\"],\"ttl\":300}"'
```
Get `<LINE>` from `parse_zone` output (`line_index` field). Get `<SERIAL>` from SOA record.

**Current records of interest:**
| Subdomain | Type | Value | What |
|---|---|---|---|
| `journal` | A | `46.225.173.209` | Hetzner staging OJS (changed 2026-03-19) |
| `community` | A | `77.72.2.79` | Krystal WP (membership site) |
| (root) | A | `77.72.2.79` | Krystal WP (brochure site) |

## Dev environment rebuild + verify

Full dev rebuild is a two-step process:

1. **Rebuild + sample data** (~3–5 min): `scripts/rebuild-dev.sh --with-sample-data --skip-tests`
   Seeds ~1400 test WP users + subscriptions and 2 sample OJS issues. Includes branding, plugin config, subscription types.
2. **Full backfill import** (~10 min): `backfill/import.sh backfill/output/*`
   Imports all 68 issues (1398 articles, HTML + PDF galleys, 469MB XML). Overwrites sample issues, keeps WP test users.
3. **Run e2e tests**: `npx playwright test` — 66 tests, all should pass.
4. **Verify banner links**: sidebar "BOOK NOW" banners should link to `WPOJS_WP_MEMBER_URL` (not localhost).

Last verified 2026-03-19:
- [x] Dev containers running, test-connection passes
- [x] 66/66 Playwright e2e tests pass (inline HTML galley TOC test fixed — was incorrectly checking paywalled articles' Full Text links)

## Staging rebuild

After dev is verified, grave-and-repave staging to confirm the same setup works on Hetzner:

1. `hcloud server delete sea-staging && hcloud firewall delete sea-staging-fw`
2. `scripts/init-vps.sh --name=sea-staging`
3. `scripts/deploy.sh --host=sea-staging --provision --clean --env-file=.env.staging`
4. `scripts/backfill-remote.sh --host=sea-staging` — syncs + imports all 68 issues
5. `scripts/smoke-test.sh --host=sea-staging`
6. Verify banner links point to `WPOJS_WP_MEMBER_URL` from `.env.staging`

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

All passing (66/66):

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
- [x] Inline HTML galley — editorial inline content, hidden "Full Text" links, PDF visible, paywall excluded

## Backfill: journal archive (30 years of back-issues)

All 68 issue PDFs (Vol 1–37.1) collected, verified, and in `backfill/input/`. See `docs/backfill-issue-inventory.md` for full inventory with page counts, TOC status, and article counts.

### Done

- [x] Audit all source PDFs (`backfill/audit.py`)
- [x] OCR 6 scanned PDFs (6.1, 13.1, 13.2, 14.1, 14.2, 29.2)
- [x] Manual TOC sidecars for 5 unparseable issues (2, 6.1, 6.2, 16.1, 16.2)
- [x] Download live WP archive (`securepdfs/`), compare all three sources
- [x] Fix mislabelled PDFs: 34.1 and 35.1 (archive had wrong issue content)
- [x] Replace truncated 6.2 (22pp) with full 204-page version from live server
- [x] Obtain missing PDFs: 34.1, 35.1, 36.2 (from live WP), 37.1 (provided manually)
- [x] Rebuild 6.2.toc.json for full 204-page issue
- [x] Human review of all prepared PDFs
- [x] Pivot: delete automated TOC parser, adopt Claude-reviewed toc.json as primary
- [x] All 68 toc.json files created and verified (1398 articles/reviews)
- [x] Phase 1: Add ~60 missing book review entries across 16 early issues (vols 2–17.1)
- [x] Phase 2: Fix PAGE_OFF_BY_1 (~120 entries), page range bugs, honorifics, gaps, missing entries
- [x] Full verification pass (7 agents across all 68 volumes) — all findings resolved
- [x] Data quality clean: zero honorifics, zero malformed metadata, all book_years filled
- [x] Google Sheet published (1398 rows): `backfill/sheets_export.py`
- [x] Enrichment data imported from spreadsheet review (`backfill/import_review.py`)
- [x] Automated toc.json audit tool (`backfill/audit_toc.py`) — checks page boundaries, back matter, reviewer attribution, book review metadata against source PDFs
- [x] Back matter removal — 60+ pages of ISSN, Publications Received, ads, membership forms, contributor guidelines removed from last articles across 40+ volumes
- [x] 105 articles extended to include missing last page (author bios, references, final paragraphs)
- [x] 91 book reviews extended to include reviewer byline page
- [x] 19 wrong reviewer attributions corrected (article authors misassigned as reviewers)
- [x] 7 combined multi-book reviews fixed (identical page ranges for all entries)
- [x] 3 missing articles/reviews added (Vol 7.1 Jennifer Hay, Vol 11.2 Milton letter, Vol 31.2 Living Your Own Life)
- [x] Vol 6.1 all 7 book review page ranges corrected (shifted ~3 pages)
- [x] Vol 18.2 Zone of Interior split into 3 separate reviews (Rosemary Moore, Joseph Berke, M. Guy Thompson)
- [x] Vol 30.2 Happy City split into 3 entries (Happy City review, A Ghost Story film review, Cotterrell exhibition report)
- [x] Systematic back matter scan — 20+ entries trimmed across 17 volumes (Publications Received, Advertising Rates, Back Issues pages incorrectly included)
- [x] `audit_toc.py` enhanced: checks ALL articles for back matter flags (not just last), auto-fix mode for both last-article and mid-issue back matter
- [x] HTML bleed detection + trimming tool (`backfill/fix_html_bleed.py`) with running header stripping
- [x] Separate Haiku prompts per section type (article, shared-page article, book review) with book_title disambiguation

### Next

- [x] **HTML galley regeneration** — all 1398 articles have HTML galleys. 42 PyMuPDF fallback (content-filtered). Report: `backfill/output/htmlgen-report.json`.
- [x] **HTML galley QA & consistency pass** — `audit_html.py` + `fix_html_bleed.py` enhancements. Fixed 168 issues (running headers, reviewer mismatches, missing bylines, page boundaries). Broader spot-check (109 reviews across 41 volumes): 89/109 pass (82%), then fixed all 20 failures (truncations, merged reviews, wrong reviewers). Final audit: 1398 files, 0 issues.
- [x] **Archive quality notice** — subtle footer box on all OJS pages: "Our archive has been digitally restored from 30 years of print issues. If you spot any errors or formatting issues, please let us know at [email]." Contact email configurable via `OJS_CONTACT_EMAIL`.
- [ ] **Broader shared-page book review check** — spot-checks found shared-page bleed in ~10% of sampled reviews. Run a systematic check of ALL shared-page book reviews (where pdf_page_start == prev article's pdf_page_end) to catch remaining cases.
- [ ] Run all 68 PDFs through backfill pipeline (`split-issue.sh` → `import.sh`) — HTML galleys will be picked up automatically by `generate_xml.py`
- toc.json files are auto-discovered by `split-issue.sh` — no flags needed

## Future improvements

- [x] **Inline HTML for editorials** — standalone OJS plugin (`inlineHtmlGalley`) renders editorial HTML galley content inline on article pages for open-access articles, hides redundant "Full Text" galley links. Extracted from `wpojsSubscriptionApi` into independently publishable plugin. 5 Playwright tests. HTML galley generation in `generate_xml.py` already handles this during backfill import.
- [ ] **Cross-issue browsing** — let readers browse articles by type (e.g., Book Reviews) across issue boundaries. Two things to investigate:
  - [ ] **Browse by Section plugin** (`pkp/browseBySection`) — install from Plugin Gallery, enable per-section. Works with existing section assignments, no extra metadata. Easiest win.
  - [ ] **OJS categories** — decide taxonomy/topology (hierarchical, one level of nesting supported). Categories are independent of sections and require assigning articles (could be done during backfill import). Decide what categories to create and whether they add value beyond section browsing.
- [ ] **Non-member payment UX flow** — OJS redirects anonymous users to login when they click a paywalled article, with no "this is paywalled / subscribe / purchase" interstitial. Just a bare login form. This is how the OJS code works (`Validation::redirectLogin()` in `ArticleHandler.php` fires for anonymous users on restricted galleys; purchase flow requires `$user->getId()`). Need to work out the best achievable flow for non-members who want to buy individual articles/issues — may need a custom interstitial or template override. Related: registration is currently disabled (`disableUserReg=1` in `setup-ojs.sh`) because members are created via sync — but non-member purchasers need to register. May need to re-enable registration with appropriate guardrails.
- [ ] Admin per-member sync status — Sync Log page shows global stats but no per-user view. Data exists in `wp_wpojs_sync_log` + `_wpojs_user_id` usermeta; just needs a UI.

Dropped (not worth the complexity):
- ~~Batch bulk sync endpoint~~ — would reduce 1400 HTTP calls to ~14 but adds OJS-side complexity (transactions, partial failure). Load-based backpressure + adaptive throttling makes sequential sync fast enough (~40s on Hetzner for 684 users).
- ~~Differential reconciliation~~ — full scan batches (chunks of 100). Fast enough.
- ~~API key rotation~~ — ops procedure, not a code feature.
- ~~On-hold grace period~~ — WCS retries failed payments automatically. Revisit if it generates support tickets.
