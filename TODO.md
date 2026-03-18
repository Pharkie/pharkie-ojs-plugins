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
- [ ] **Archive quality notice** — add a footer notice on OJS for recently digitized back-issue content. Something like "This archive has been digitally restored from print. If you spot any issues, please email [editor] to help us get the archive right." Not a banner — a subtle footer box.
- [ ] **Broader shared-page book review check** — spot-checks found shared-page bleed in ~10% of sampled reviews. Run a systematic check of ALL shared-page book reviews (where pdf_page_start == prev article's pdf_page_end) to catch remaining cases.
- [ ] Run all 68 PDFs through backfill pipeline (`split-issue.sh` → `import.sh`) — HTML galleys will be picked up automatically by `generate_xml.py`
- toc.json files are auto-discovered by `split-issue.sh` — no flags needed

## Future improvements

- [x] **Inline HTML for editorials** — standalone OJS plugin (`inlineHtmlGalley`) renders editorial HTML galley content inline on article pages for open-access articles, hides redundant "Full Text" galley links. Extracted from `wpojsSubscriptionApi` into independently publishable plugin. 5 Playwright tests. HTML galley generation in `generate_xml.py` already handles this during backfill import.
- [ ] **Cross-issue browsing** — let readers browse articles by type (e.g., Book Reviews) across issue boundaries. Two things to investigate:
  - [ ] **Browse by Section plugin** (`pkp/browseBySection`) — install from Plugin Gallery, enable per-section. Works with existing section assignments, no extra metadata. Easiest win.
  - [ ] **OJS categories** — decide taxonomy/topology (hierarchical, one level of nesting supported). Categories are independent of sections and require assigning articles (could be done during backfill import). Decide what categories to create and whether they add value beyond section browsing.
- [ ] Admin per-member sync status — Sync Log page shows global stats but no per-user view. Data exists in `wp_wpojs_sync_log` + `_wpojs_user_id` usermeta; just needs a UI.

Dropped (not worth the complexity):
- ~~Batch bulk sync endpoint~~ — would reduce 1400 HTTP calls to ~14 but adds OJS-side complexity (transactions, partial failure). Load-based backpressure + adaptive throttling makes sequential sync fast enough (~40s on Hetzner for 684 users).
- ~~Differential reconciliation~~ — full scan batches (chunks of 100). Fast enough.
- ~~API key rotation~~ — ops procedure, not a code feature.
- ~~On-hold grace period~~ — WCS retries failed payments automatically. Revisit if it generates support tickets.
