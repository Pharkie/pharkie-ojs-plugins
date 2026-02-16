# CLAUDE.md

## Project

WordPress ↔ OJS integration for the Society for Existential Analysis (SEA). WP manages memberships; OJS hosts the journal *Existential Analysis* behind a paywall. Goal: members get access, non-members can still buy content.

## Live sites

- **WP:** https://community.existentialanalysis.org.uk/
- **OJS:** https://journal.existentialanalysis.org.uk/index.php/t1/index

## Key docs (read these first)

- `docs/architecture.md` — architecture options and current recommendation
- `docs/ojs-api.md` — OJS REST API capabilities, DB schema, PHP internals
- `docs/phase0-findings.md` — raw research findings from API audit
- `TODO.md` — task list with blocking questions and phased implementation plan

## Hard constraints

- **WP is source of truth** for membership. OJS is downstream.
- **OJS paywall must keep working** for non-member purchases (£3 article, £25 issue, £18 back issue).
- **No OJS core modifications.** Plugins only.
- **No OIDC / OpenID SSO.** Evaluated and rejected as too fragile.
- **Ship fast.** Project is over budget. Prefer boring, reliable solutions.

## Gotchas

- **OJS has NO subscription REST API.** Don't assume you can POST/PUT subscriptions. The endpoints don't exist. See `docs/ojs-api.md`.
- **User creation API is unconfirmed.** Swagger spec shows read-only user endpoints. POST /users may not exist on SEA's OJS version. Verify before relying on it.
- **Apache + PHP-FPM strips Authorization headers.** Need `CGIPassAuth on` in `.htaccess` or use `?apiToken=` query param fallback.
- **OJS 3.4 vs 3.5 matters a lot.** 3.5 has clean plugin API extensibility (Laravel routing). 3.4 doesn't. Architecture choice depends on version.
- **Subscription SSO plugin ≠ OIDC SSO.** The Subscription SSO plugin (by PKP lead dev) is a simple access-check delegator, not a login/session system. It was rejected as part of a category but is actually the fastest viable option. Don't conflate it with the rejected OIDC approach.

## Code conventions

- WordPress plugin standards (PHP)
- Prefix everything `sea_ojs_`
- Use WP HTTP API (`wp_remote_post` etc.) — not raw cURL
- Use WP Cron for scheduled tasks, not system cron
- Log all sync operations — failures must be visible in WP admin
- Settings page for OJS URL, API key, subscription type mapping

## Don't

- Modify OJS source code
- Sync passwords between systems
- Build message queues, webhook servers, or microservices
- Add features beyond the core access requirement
- Assume any OJS API endpoint exists without checking `docs/ojs-api.md`
