# Umami Analytics Plugin

Adds [Umami](https://umami.is) — privacy-friendly, cookieless web analytics — to the reader-facing pages of an OJS journal, plus custom events for the actions worth measuring on a paywalled journal.

Plugin slug: `umamiAnalytics`. Repo folder: `plugins/umami-analytics/` → mounted at `/var/www/html/plugins/generic/umamiAnalytics` (see `docker-compose.yml`).

## Why Umami (vs OJS native stats)

OJS ships its own COUNTER-style usage statistics (article views / galley downloads, in the editorial dashboards). That answers "how many times was article X read". Umami answers the *audience* questions OJS can't: where readers come from, which landing pages convert, how many paywall impressions turn into membership/purchase clicks, device/referrer breakdowns, real-time visitors. The two are complementary — keep both.

## What it tracks

Page views are automatic. On top of that, with **Track custom events** enabled, the plugin records:

| Event | Fires when | Data |
| --- | --- | --- |
| `download` | A galley file link is clicked | `type` (`pdf`/`html`/`xml`), `label`, `path` |
| `paywall-view` | The non-subscriber CTA (Inline HTML Galley) is rendered | `path` |
| `membership-click` | A link inside the paywall CTA is clicked | `href`, `path` |
| `purchase-click` | An article-purchase / subscription payment link is clicked | `href`, `path` |
| `doi-click` | A DOI / reference resolver link is clicked | `doi`, `path` |
| `search` | A search form is submitted | `path` (query text deliberately **not** captured) |
| `login-click` / `register-click` | The login / register link is clicked | `path` |

The membership funnel — `paywall-view` → `membership-click` / `purchase-click` — is the headline thing to watch: it tells you how well the paywall converts.

Only `frontend/` (reader-facing) templates are instrumented; the editorial back office is never tracked. With **Exclude staff** on (default), the tracker is not loaded for logged-in managers, sub-editors, or site admins.

## Getting a Website ID

Umami works with Umami Cloud or a self-hosted instance.

1. In your Umami dashboard: **Settings → Websites → Add website** — name it and set the domain to the journal hostname.
2. **Edit** the website → copy the **Website ID** (a UUID). This is *not* a secret — it appears in page HTML by design, so it's fine in the OJS settings UI.
3. Note your tracking-script URL: `https://cloud.umami.is/script.js` for Umami Cloud, or `https://<your-umami-host>/script.js` for self-hosted.

## Enabling in OJS

1. **Settings → Website → Plugins → Generic Plugins**, tick **Umami Analytics**.
2. Click the arrow → **Settings**, paste the **Website ID**, set the **Tracking Script URL**, **Save**.
3. Load the public site in a private window and confirm in Umami's **Realtime** view that the visit registers. Click a PDF/galley link and confirm a `download` event appears under **Events**.

Nothing is loaded until a Website ID is set, so enabling the plugin without configuring it is harmless.

### Keeping dev/staging out of your stats

Plugin settings are stored per-OJS-database, so dev and live are configured independently. Simplest approach: **only configure the Website ID on live**, leave it blank on dev. If you want to reuse one Website ID across environments, set **Restrict to Domains** to the live hostname so localhost/staging traffic isn't counted.

## Self-hosting Umami

A self-hosted instance (unlimited websites/events, data on your own infra) needs the Umami app plus a MySQL 8 or PostgreSQL database, fronted by a reverse proxy with TLS. This repo includes a `docker-compose.umami.yml` overlay (Umami app + `mysql:8.0`) and a Caddy vhost for exactly that.

> **Note:** Umami requires **MySQL 8 / PostgreSQL** — it fails on MariaDB (the `05_add_visit_id` Prisma migration errors with P3009), so the overlay uses `mysql:8.0` even where the rest of the stack is MariaDB.

Required env vars (see `.env.example`): `CADDY_UMAMI_DOMAIN`, `UMAMI_DB_PASSWORD`, `UMAMI_DB_ROOT_PASSWORD`, `UMAMI_APP_SECRET` (`APP_SECRET` must not change after first boot). On first boot Umami creates its schema; log in with the default `admin` / `umami` and **change the password immediately**.

The self-hosted `@mikusnuz/umami-mcp` MCP works against a self-hosted instance (it speaks the `/api` dialect with username/password auth); it does **not** work with Umami Cloud's `/v1` API.

*(Deployment specifics for this project's server live in the private repo.)*

## Deploying the OJS plugin

The plugin itself is a code-only change (new plugin, no article data). Per `CLAUDE.md` → "Code-only changes":

1. Pull the repo on the server.
2. `docker compose up -d ojs` — the new bind mount in `docker-compose.yml` requires the container to be recreated.
3. Enable + configure the plugin in the live OJS admin (Website ID + script URL above).
4. Run the content-check smoke test to verify the site still works.

## Privacy

Umami is cookieless and does not collect personal data, so it does not require a cookie-consent banner under GDPR/PECR in most interpretations. The `search` event intentionally omits the query string; event `data` values are limited to paths, galley types, DOIs, and link hrefs. Review against your own privacy policy before go-live.

## Uninstall

Disable the plugin in the OJS Plugins UI (stops all injection immediately). To remove entirely, delete the `./plugins/umami-analytics` bind-mount line from `docker-compose.yml`, `docker compose up -d ojs`, and delete the `plugins/umami-analytics/` folder.
