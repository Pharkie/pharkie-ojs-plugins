# Roadmap

## Done

- OJS plugin (`wpojs-subscription-api`) — code complete, reviewed
- WP plugin (`wpojs-sync`) — code complete, reviewed
- Docker dev environment
- Member dashboard widget (WooCommerce My Account)
- UI messages (OJS login hint, paywall hint, footer)
- Non-Docker setup guide — `docs/non-docker-setup.md`
- Dev environment clean rebuild verified — all 81 e2e tests passing
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

Server: `sea-live` (was `sea-staging`, promoted 2026-03-19). SSH: `ssh sea-live`. Deploy: `scripts/deploy.sh --host=sea-live --ssl --env-file=.env.live`.

- [x] ~~Set up SEA Hetzner VPS~~ — `scripts/init-vps.sh`, deployed and verified 2026-03-10
- [x] ~~Deploy OJS + WP~~ — 68 issues imported, 22/22 smoke tests pass
- [x] ~~DNS cutover~~ — `journal.existentialanalysis.org.uk` → `46.225.173.209` (2026-03-19, via cPanel UAPI)
- [x] ~~Caddy + firewall~~ — ports 80/443 open, Caddy deployed with `--ssl`, auto-provisioning Let's Encrypt cert
- [x] ~~SSL cert provisioned~~ — HTTPS working, Caddy auto-provisioned Let's Encrypt cert (verified 2026-03-19)
- [x] ~~Resend email~~ — SMTP + SPF/DKIM working (confirmed 2026-03-19)
- [ ] Monitor 24-48h
- [x] ~~Decommission PKP hosting~~ — cancelled, runs until ~September 2026

## WP sync (Phase 2) — LIVE

WP (`community.existentialanalysis.org.uk` on Krystal) ↔ OJS (`journal.existentialanalysis.org.uk` on Hetzner).

### OJS-side prep — DONE

- [x] ~~`config.inc.php` `[wpojs]` section~~ — `api_key_secret`, `allowed_ips` (77.72.2.79 Krystal + 172.0.0.0/8 Docker), `wp_member_url`, `support_email`
- [x] ~~Subscription type created~~ — "Membership (all tiers)" (type_id=1, £0)
- [x] ~~OJS ping reachable from Krystal~~ — verified

### WP-side deployment — DONE

- [x] ~~Plugin uploaded and activated~~ — `wpojs-sync` active on Krystal WP
- [x] ~~API key in wp-config.php~~ — `WPOJS_API_KEY=QDGwNVJpIQDjvQ8U3dCIMSLrCef57YLA`
- [x] ~~OJS Base URL configured~~ — `https://journal.existentialanalysis.org.uk/index.php/ea`
- [x] ~~Product mappings~~ — all 6 products → OJS type 1:
    | WC Product ID | Product name | OJS Type ID |
    |---|---|---|
    | 1892 | UK Membership (no directory listing) | 1 |
    | 1924 | International Membership (no directory listing) | 1 |
    | 1927 | Student Membership (no directory listing) | 1 |
    | 23040 | Student Membership (with directory listing) | 1 |
    | 23041 | International Membership (with directory listing) | 1 |
    | 23042 | UK Membership (with directory listing) | 1 |

### Bulk sync — DONE (2026-03-20)

- [x] ~~Dry run~~ — 677 members, 0 failures
- [x] ~~Bulk sync~~ — 677 synced, 0 skipped, 0 failed (226s, adaptive throttle)
- [x] ~~3 pre-sync failures retried~~ — events that fired before type mappings were configured
- [x] ~~Post-sync smoke test~~ — 22/22 passed
- OJS: 1370 users, 1366 subscriptions (includes 692 from March 19 test sync + 678 from March 20 production sync)

### Post-launch — IN PROGRESS

- [x] ~~Spot-check 2-3 members can log into OJS with WP password~~ — 2 members tested successfully (2026-03-21)
- [ ] Test new member flow (create WCS subscription → verify OJS access created)
- [ ] Test cancellation flow (cancel → verify OJS access removed)
- [ ] Test on-hold / failed payment scenario
- [ ] **Mobile testing** — check OJS article pages, archive, login, inline HTML galley, paywall on mobile browsers (iOS Safari, Android Chrome). Check responsive layout, readability, touch targets, galley content overflow.
- [x] ~~**Non-member purchase flow**~~ — Stripe Payment plugin built and tested on dev (2026-03-22). Full Checkout redirect flow with webhook handler. PayPal sandbox was broken for UK accounts — multiple support tickets filed, PayPal support was unable to resolve. Switched to Stripe. Live Stripe account created (2026-03-22), restricted API key (Checkout Sessions only). PayPal removed as payment option.
- [x] ~~Monitor 24-48h — check sync log for failures~~ — 0 sync failures, 678/678 reconciliation clean (2026-03-21)
- [x] ~~Run `wp ojs-sync reconcile` manually after 24h to check for drift~~ — 678 OK, 0 drift (2026-03-21)
- [x] ~~Investigate "Active WP members: 0" in `wp ojs-sync status`~~ — fixed, now shows 678 correctly

### Hardening — TODO

- [x] ~~**ggshield**~~ — wired into pre-commit as Check 7, auth persisted via bind-mounted config dir, tested (2026-03-22)
- [x] ~~**DOI deposits to Crossref**~~ — 1,470 DOIs registered in production (2026-03-21). Includes 44 pre-existing DOIs (36.2 + 37.1) re-deposited with updated URLs. 6 articles with leading `"` in titles were missed by auto-assignment — assigned and deposited via API. One book review (9771) had empty surname author ("ChatGPT") fixed. `blast-queue.sh` rewritten: `jobs.php run --once` loop, worker script injection, timeout, stale worker kill, `--delay` flag, ETA monitor. See `docs/blast-queue.md`.
  - [x] ~~**Verify DOIs resolve**~~ — confirmed, e.g. `https://doi.org/10.65828/km6aqq62` → `journal.existentialanalysis.org.uk` (2026-03-22)
- [x] ~~**SMTP credentials audit**~~ — false positive, no real keys in repo.
- [x] ~~**Env var hardening deployed**~~ — deployed to live (2026-03-22)
- [ ] **Author emails** — backfilled articles use `firstname.lastname@placeholder.invalid` as dummy emails. Cross-reference known authors (editorial board, regular contributors) with real emails from WP/UM user list or SEA membership records.

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
2. **Full backfill import** (~15 min): `backfill/import.sh backfill/output/* --clean`
   Imports all 68 issues (1398 articles, HTML + PDF galleys, 469MB XML). `--clean` wipes DB first. For single-issue updates: `import.sh backfill/output/37.1 --force` (overwrites that issue only).
3. **Run e2e tests**: `npx playwright test` — 81 tests, all should pass.
4. **Verify banner links**: sidebar "BOOK NOW" banners should link to `WPOJS_WP_MEMBER_URL` (not localhost).

Last verified 2026-03-19:
- [x] Dev containers running, test-connection passes
- [x] 81/81 Playwright e2e tests pass (inline HTML galley TOC test fixed — was incorrectly checking paywalled articles' Full Text links)

## Future staging rebuild

When a staging server is needed for testing changes before live:

1. `scripts/init-vps.sh --name=sea-staging`
2. `scripts/deploy.sh --host=sea-staging --provision --env-file=.env.staging`
3. `scripts/backfill-remote.sh --host=sea-staging` — syncs + imports all 68 issues
4. `scripts/smoke-test.sh --host=sea-staging`

No staging server currently exists. Live is `sea-live`.

## Staging test results (2026-03-10, sea-michal account)

- [x] Connection test passes
- [x] Bulk sync — 684/684 members synced to OJS (50s, 0 failures)
- [x] OJS API endpoint mounted and responding
- [x] All 6 products mapped to OJS subscription types
- [x] Smoke tests 22/22 — includes full sync round-trip (create, subscribe, expire, anonymise)
- [x] OJS login with WP password works after bulk sync
- [ ] **New member flow** — (see post-launch checklist above)
- [ ] **Cancellation/expiry** — (see post-launch checklist above)
- [ ] **On-hold / failed payment** — (see post-launch checklist above)

## Playwright E2E browser tests (`e2e/`)

All passing (81/81):

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
- [x] **Fix duplicate abstracts** — article pages showed abstract twice (once in OJS metadata, once in HTML galley). Phase 1: updated 312 toc.json abstracts + 103 keywords from cleaner HTML galley text, fixed 24 bad keyword arrays. Phase 2: stripped abstract sections from all 520 HTML galley files. Scripts: `backfill/update_abstracts.py`, `backfill/strip_abstract.py`. Deployed to live 2026-03-21.
- [x] ~~**Broader shared-page book review check**~~ — 268 shared-page reviews checked, 3 bleed cases fixed (14.2, 32.2×2), 7 false positives confirmed. 0 remaining (2026-03-21).

## Next priorities

### 1. Stripe live deployment
- [x] ~~Create Stripe account with SEA bank details~~ (2026-03-22)
- [x] ~~Create restricted API key (Checkout Sessions write-only)~~ (2026-03-22)
- [x] ~~Add live keys (`rk_live_`, `pk_live_`) to `.env.live`~~ (2026-03-22)
- [x] ~~Configure Stripe webhook endpoint in Stripe dashboard~~ (2026-03-22)
- [x] ~~Add webhook secret (`whsec_`) to `.env.live`~~ (2026-03-22)
- [x] ~~Configure keys via OJS admin UI~~ (2026-03-22)
- [ ] Deploy code changes (env var auth, multi-stage build, field order) and verify end-to-end purchase on live

### 2. Citation extraction + Crossref reference linking
Crossref membership obligation: include DOIs for cited works when depositing ([reference linking docs](https://www.crossref.org/documentation/reference-linking/)). Current Crossref Reference Linking Plugin (`pkp/crossrefReferenceLinking`) is broken on OJS 3.5. OJS 3.6 will integrate citation linking properly ([pkp/pkp-lib#12104](https://github.com/pkp/pkp-lib/issues/12104)). Author guidelines already updated. 18-month grace period for new members.
- [ ] **Phase 1: Extract archive citations from HTML galleys** — parse reference sections from existing HTML galleys (~1,356 articles), split into individual citations, load into OJS `citations` table.
- [ ] **Phase 2: Look up DOIs for existing citations** — use Crossref REST API to match extracted citations to DOIs (free, no per-lookup cost). When OJS 3.6 lands, the built-in plugin can include matched DOIs in `<citation_list>` deposits automatically.

### 3. Security audit
- [x] **OJS database backups** — daily automated mysqldump at 03:00 UTC via cron on VPS. AES-256-CBC encrypted (key in `/opt/backups/ojs/.backup-key`, back up in password manager). 29MB compressed. Retention: 7 daily + 4 weekly on VPS; 30 daily + 12 weekly off-server. Scripts: `scripts/backup-ojs-db.sh` (runs on VPS), `scripts/pull-ojs-backup.sh` (pull/decrypt/manage). Off-server: GitHub Actions pulls encrypted dumps daily at 04:00 UTC to private repo `Pharkie/sea-ojs-db-backups`. Hardened: restricted SSH key (read-only backup access, no shell), staleness alert (>36h → email), size check (<1MB → fail), deploy.sh auto-installs cron. Full round-trip tested. (2026-03-22)
- [ ] OJS hardening: file upload restrictions, rate limiting, CSP headers, admin access controls, ~~backup strategy~~, update policy
- [ ] Caddy security headers
- [ ] WP plugin: API key handling, input validation, CSRF
- [ ] Stripe plugin: webhook signature verification, amount/currency checks (done in code, needs pen test)
- [ ] Penetration test the sync API endpoint

### 4. Discoverability & indexing
- [ ] **SEO** — citation meta tags (Highwire/Dublin Core), sitemap.xml, robots.txt, Open Graph tags. Submit sitemap to Google Search Console. Consider structured data (schema.org/ScholarlyArticle).
- [ ] **Google Scholar indexing**:
  - [ ] Verify Highwire Press meta tags on article pages
  - [ ] Ensure abstracts visible to crawlers (not behind paywall/JS)
  - [ ] Submit to Google Scholar inclusion request form
  - [ ] Check PDF metadata matches HTML meta tags
  - [ ] Verify `robots.txt` and paywall don't block Googlebot
- [ ] **JATS XML galleys** — standard format for scholarly interchange, required/preferred by DOAJ, PubMed, Crossref enhanced deposits, preservation services. Approach options:
  - **From HTML galleys** — convert existing HTML galleys to JATS XML (~1,356 articles already have HTML)
  - **From source PDFs via API** — re-extract structured content directly to JATS using Claude API
  - **JATS Parser Plugin** — OJS built-in, can render JATS XML inline
- [ ] **ResearchGate journal profile** — create/claim the journal profile
- [ ] **DOAJ indexing** — apply for Directory of Open Access Journals listing (open-access content: editorials, book reviews):
  - [ ] DOAJ application form
  - [ ] JATS XML or OAI-PMH metadata feed
  - [ ] Verify OAI-PMH endpoint works
  - [ ] Clear licensing info on each article
- [ ] **ORCID integration** — configure OJS ORCID plugin, add ORCID iDs to author metadata

### 5. Analytics
- [ ] Decide approach: OJS Usage Statistics (built-in), Google Analytics, Plausible, or Matomo. Consider: privacy (GDPR), metrics needed, cost.

### 6. Everything else
- [ ] **Non-member payment UX flow** — OJS redirects anonymous users to bare login form. Test whether UX is acceptable or needs a custom interstitial.
- [ ] **Cross-issue browsing** — Browse by Section plugin (`pkp/browseBySection`) and/or OJS categories
- [ ] Admin per-member sync status — per-user view of sync log
- [ ] **Author emails** — replace `firstname.lastname@placeholder.invalid` with real emails
- [ ] **Invite article authors to review their contributions** — email authors to check: right number of articles, correct full text, correct PDF pages. Requires real author emails first.
- [ ] Test new member / cancellation / on-hold flows
- [ ] Mobile testing

## Done

- [x] **Inline HTML galley plugin** — standalone, configurable settings UI, 5 Playwright tests
- [x] **DOI assignment** — 1,476 DOIs assigned (1,408 articles + 68 issues)
- [x] **DOI deposit to Crossref** — 1,470 DOIs registered in production (2026-03-21). See `docs/blast-queue.md`.
- [x] **Stripe Payment plugin** — built, tested on dev, 5/5 e2e tests (2026-03-22). Replaced PayPal (sandbox broken for UK accounts, PayPal support unhelpful). Stripe account created with restricted API key (Checkout Sessions only).

Dropped (not worth the complexity):
- ~~Batch bulk sync endpoint~~ — would reduce 1400 HTTP calls to ~14 but adds OJS-side complexity (transactions, partial failure). Load-based backpressure + adaptive throttling makes sequential sync fast enough (~40s on Hetzner for 684 users).
- ~~Differential reconciliation~~ — full scan batches (chunks of 100). Fast enough.
- ~~API key rotation~~ — ops procedure, not a code feature.
- ~~On-hold grace period~~ — WCS retries failed payments automatically. Revisit if it generates support tickets.
