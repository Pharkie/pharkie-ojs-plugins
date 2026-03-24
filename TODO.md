# Roadmap

Deployment-specific roadmap (milestones, server details, sync status) lives in `private/TODO.md`.

## Getting started (new deployment)

1. Set up a dev environment — see [`docs/docker-setup.md`](docs/docker-setup.md)
2. Read the architecture — see [`ARCHITECTURE.md`](ARCHITECTURE.md)
3. Install plugins on your OJS + WP servers — see [`docs/non-docker-setup.md`](docs/non-docker-setup.md)
4. Configure API keys, subscription type mapping, and IP allowlisting
5. Run a dry-run bulk sync: `wp ojs-sync sync --bulk --dry-run`
6. Deploy to a VPS — see [`docs/vps-deployment.md`](docs/vps-deployment.md)

## Done

- OJS plugin (`wpojs-subscription-api`) — REST endpoints for user + subscription CRUD
- WP plugin (`wpojs-sync`) — hooks into WCS lifecycle events, async queue, CLI bulk sync
- Inline HTML galley plugin — inlines HTML content on article pages
- Stripe Payment plugin — Checkout redirect flow for non-member purchases
- Docker dev environment with sample data seeding
- Member dashboard widget (WooCommerce My Account)
- UI messages (OJS login hint, paywall hint, footer)
- Password hash sync (bulk + ongoing) — WP hashes sent to OJS, lazy rehash on first login
- VPS deployment automation (`init-vps.sh`, `deploy.sh`, `smoke-test.sh`, `load-test.sh`)
- Backfill pipeline for importing journal back-issues into OJS
- DOI assignment + Crossref deposit
- Automated database backups (encrypted, rotated, off-server)
- Citation extraction + classification (15,182 references, 2,031 notes, 57 author bios)
- JATS 1.3 XML generation — 1,398 article files, single source of truth for article content

### JATS quality improvements

- [ ] Extract journal page numbers from split PDFs (first page of each article PDF has printed page number) → populate `<fpage>`/`<lpage>` in JATS
- [ ] AI-generate abstracts for 372 research articles that lack them (flag as `abstract-type="AI-generated"`)
- [ ] Parse `<mixed-citation>` into `<element-citation>` (structured author/year/title/publisher fields) — improves Crossref reference linking accuracy (Crossref can fuzzy-match raw strings but structured data gets better results)

### JATS → OJS sync

- [ ] Map JATS to OJS: generate OJS Native XML from JATS files (for new issue imports)
- [ ] Backfill citations into live OJS: insert references from JATS `<ref-list>` into OJS `citations` table (direct DB, no reimport — would break 1,470 DOIs)
- [ ] Re-embed notes/bios/provenance into HTML galleys on live (stripped during citation extraction, stored in JATS `<fn-group>`/`<bio>`/`<notes>`)

### Typical post-launch tasks

- [ ] Test new member flow (create subscription → verify OJS access)
- [ ] Test cancellation flow (cancel → verify OJS access removed)
- [ ] Test on-hold / failed payment scenario
- [ ] Mobile testing
- [ ] Security audit (file uploads, rate limiting, CSP headers, pen test)
- [ ] Monitoring (uptime checks, sync health, SSL expiry)
- [ ] SEO and discoverability (citation meta tags, sitemap, Google Scholar)
- [ ] Analytics

### Playwright E2E browser tests (`e2e/`)

- Sync lifecycle — WCS activate/expire → OJS subscription status
- OJS login — synced user logs in with WP password
- WP dashboard — My Account journal access widget
- OJS UI messages — login hint, footer, paywall hint
- Admin monitoring — Sync Log page
- Email change sync, user deletion / GDPR
- Error recovery, manual roles, settings, WP-CLI, API auth
- Inline HTML galley, article purchase flow
