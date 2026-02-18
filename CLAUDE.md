# CLAUDE.md

## Project

WordPress ↔ OJS integration for the Society for Existential Analysis (SEA). WP manages memberships; OJS hosts the journal *Existential Analysis* behind a paywall. Goal: members get access, non-members can still buy content.

## Live sites

- **WP:** https://community.existentialanalysis.org.uk/
- **OJS:** https://journal.existentialanalysis.org.uk/index.php/t1/index (currently 3.4.0-9)

## Key docs (read these first)

- `docs/plan.md` — the implementation plan: what we're building, how it works, endpoint specs, launch sequence, testing approach
- `docs/discovery.md` — decision trail: what was tried, what was eliminated, and why
- `docs/review-findings.md` — six-perspective plan review (senior dev, QA, UX, business, security, ops/SRE) and how findings were resolved
- `docs/ojs-api.md` — OJS REST API capabilities, DB schema, PHP internals
- `docs/wp-integration.md` — WP membership stack (Ultimate Member + WooCommerce Subscriptions), hooks, code patterns
- `docs/janeway-paywall-investigation.md` — concrete technical plan for Janeway backup path
- `TODO.md` — task checklist with phased implementation steps

## Architecture decision

**Push-sync** (custom OJS plugin + WP plugin). WP pushes subscription changes to OJS on membership events. A plugin on each side: WP detects changes, OJS receives them and creates subscription records using its own internal DAO classes. See `docs/plan.md` for full details, `docs/discovery.md` for how we got here.

Previous developer called this "Plan C". Key addition: OJS REST API has no subscription endpoints, so we build a small OJS plugin to expose them.

**Janeway migration** is a genuine backup (not a nuclear option) if the OJS 3.5 upgrade proves too costly. See `docs/discovery.md` for the comparison.

## Plan naming

| Name | What | Status |
|---|---|---|
| **OIDC SSO** | OpenID Connect SSO | Eliminated |
| **Pull-verify** | Subscription SSO plugin (OJS asks WP at access time) | Eliminated |
| **Push-sync** | WP pushes to OJS via plugins on each side | **Chosen** |
| **Push-sync (direct DB)** | Same but writes to OJS DB directly | Fallback |
| **Janeway migration** | Replace OJS with Janeway + custom paywall | Genuine backup |

## Hard constraints

- **WP is source of truth** for membership. OJS is downstream.
- **Email is the matching key.** Same email required on both systems. No separate mapping table. Members who want a different email on OJS must update their WP email first.
- **Bulk sync creates OJS accounts.** Don't wait for members to self-register. Push user accounts + subscriptions from WP upfront (~500 existing members at launch).
- **OJS paywall must keep working** for non-member purchases (£3 article, £25 issue, £18 back issue).
- **No OJS core modifications.** Plugins only.
- **Ship fast.** Project is over budget. Prefer boring, reliable solutions.

## Eliminated approaches (don't revisit)

- **OIDC SSO** — only solves login not access; OJS plugin has unresolved bugs, no 3.5 release, breaks multi-journal. Previous developer confirmed.
- **Pull-verify** (Subscription SSO plugin) — source code audit confirmed it hijacks OJS purchase flow. Non-members can't buy content. See `docs/phase0-sso-plugin-audit.md`.
- **Native REST API sync** — subscription endpoints don't exist in any OJS version. Push-sync works around this with a custom OJS plugin.
- **XML user import as stopgap** — OJS built-in XML import creates user accounts only, not subscription records. Paywall checks subscriptions table, so doesn't grant access. See `docs/xml-import-evaluation.md`.

## Gotchas

- **OJS has NO subscription REST API.** The endpoints don't exist. That's why we need a custom OJS plugin. See `docs/ojs-api.md`.
- **User creation API is unconfirmed.** Swagger spec shows read-only user endpoints. Verify against actual OJS version before relying on it.
- **Apache + PHP-FPM strips Authorization headers.** Need `CGIPassAuth on` in `.htaccess`. Do not use `?apiToken=` query param in production (leaks key into access logs).
- **OJS 3.5 upgrade is the biggest risk.** SEA is on 3.4.0-9. The 3.5 upgrade has significant breaking changes (Slim→Laravel, Vue 2→3). If this goes badly, re-evaluate Janeway migration.

## WP membership stack

**Ultimate Member + WooCommerce + WooCommerce Subscriptions.** UM handles registration/profiles/roles. WCS handles billing. Membership = WP role.

Primary integration: hook into **WooCommerce Subscriptions** status events (`woocommerce_subscription_status_active`, `_expired`, `_cancelled`, `_on-hold`). All sync calls are async (queued via WP Cron, not inline). Daily reconciliation catches any drift. See `docs/wp-integration.md` for WCS hook details and `docs/plan.md` for the full WP plugin spec.

## Code conventions

- WordPress plugin standards (PHP)
- Prefix everything `sea_ojs_`
- Use WP HTTP API (`wp_remote_post` etc.) — not raw cURL
- Use WP Cron for scheduled tasks, not system cron
- Log all sync operations — failures must be visible in WP admin
- API key stored as `wp-config.php` constant (`SEA_OJS_API_KEY`), not in the database
- Settings page for OJS URL, subscription type mapping (WooCommerce Product → OJS Subscription Type), journal ID(s)

## Don't

- Modify OJS source code
- Sync passwords between systems
- Build message queues, webhook servers, or microservices
- Add features beyond the core sync requirement
- Assume any OJS API endpoint exists without checking `docs/ojs-api.md`
- Revisit OIDC SSO or Pull-verify — both eliminated with documented reasons
