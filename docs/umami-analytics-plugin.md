# Umami Analytics Plugin

Adds [Umami](https://umami.is) — privacy-friendly, cookieless web analytics — to the reader-facing pages of the OJS journal, plus custom events for the actions worth measuring on a paywalled journal.

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

## Where Umami runs: self-hosted on the SEA Hetzner box

We self-host Umami at **`https://analytics.existentialanalysis.org.uk`** (on the same Hetzner VPS as OJS), rather than Umami Cloud — Cloud's free tier caps the number of websites, and self-hosting keeps SEA analytics on SEA infrastructure, separate from any personal Cloud properties. Deployment is the `docker-compose.umami.yml` overlay; see [Self-hosted deployment](#self-hosted-deployment) below.

Get the **Website ID**:

1. Log in to `https://analytics.existentialanalysis.org.uk` (admin account created on first boot).
2. **Settings → Websites → Add website** — name it (e.g. "SEA Journal"), domain `journal.existentialanalysis.org.uk`.
3. **Edit** the website → copy the **Website ID** (a UUID). Not a secret — it appears in page HTML by design, so it's fine in the OJS settings UI.
4. In the OJS plugin settings, set **Tracking Script URL** to `https://analytics.existentialanalysis.org.uk/script.js` (the Cloud default won't apply here).

## Enabling in OJS

1. **Settings → Website → Plugins → Generic Plugins**, tick **Umami Analytics**.
2. Click the arrow → **Settings**, paste the **Website ID**, leave the script URL at default, **Save**.
3. Load the public site in a private window and confirm in Umami's **Realtime** view that the visit registers. Click a PDF/galley link and confirm a `download` event appears under **Events**.

Nothing is loaded until a Website ID is set, so enabling the plugin without configuring it is harmless.

### Keeping dev/staging out of your stats

Plugin settings are stored per-OJS-database, so dev and live are configured independently. Simplest approach: **only configure the Website ID on live**, leave it blank on dev. If you do want to reuse one Website ID across environments, set **Restrict to Domains** to the live hostname so localhost/staging traffic isn't counted.

## Self-hosted deployment

The Umami service (app + its own MariaDB) is defined in `docker-compose.umami.yml`, fronted by Caddy at `analytics.existentialanalysis.org.uk`.

**One-time, on the box:**

1. **DNS** — `analytics.existentialanalysis.org.uk` A → `46.225.173.209` (already added; see `private/dns-management.md`).
2. **Secrets** — add to `.env.live` (SOPS): `CADDY_UMAMI_DOMAIN=analytics.existentialanalysis.org.uk`, and `UMAMI_DB_PASSWORD`, `UMAMI_DB_ROOT_PASSWORD`, `UMAMI_APP_SECRET` (generate with `openssl rand -hex 24/24/32`). `APP_SECRET` must not change after first boot.
3. **Deploy** — `ssh sea-live 'cd /opt/pharkie-ojs-plugins && git pull'`, then bring up the stack **with the umami overlay appended** to the existing chain:
   ```
   docker compose -f docker-compose.yml -f docker-compose.staging.yml \
     -f docker-compose.caddy.yml -f docker-compose.umami.yml up -d
   ```
   (Add `docker-compose.umami.yml` to the pinned overlay list / deploy script so future `up`s include it.)
4. **First boot** — Umami auto-creates its schema. Log in at the URL with `admin` / `umami`, **change the password immediately**, create the "SEA Journal" website, copy the Website ID.
5. **Ops** — add a Better Stack monitor + heartbeat for the analytics vhost, and add `umami-db` to the backup routine (its volume is `umami_db_data`; tiny).

**MCP:** point `@mikusnuz/umami-mcp` at the self-hosted instance — set `UMAMI_URL=https://analytics.existentialanalysis.org.uk`, `UMAMI_USERNAME`, `UMAMI_PASSWORD` (the admin creds) in `~/.claude.json`, and drop the stale `UMAMI_API_KEY`. This MCP speaks the self-hosted `/api` dialect, so it works as designed against your own instance.

## Deploying the OJS plugin

The plugin itself is a code-only change (new plugin, no article data). Per `CLAUDE.md` → "Code-only changes":

1. `ssh sea-live 'cd /opt/pharkie-ojs-plugins && git pull'`
2. `docker compose up -d ojs` — the new bind mount in `docker-compose.yml` requires the container to be recreated.
3. Enable + configure the plugin in the live OJS admin (Website ID + script URL above).
4. `scripts/monitoring/content-check.sh --host=sea-live` — verify the site still works.

## Privacy

Umami is cookieless and does not collect personal data, so it does not require a cookie-consent banner under GDPR/PECR in most interpretations. The `search` event intentionally omits the query string; event `data` values are limited to paths, galley types, DOIs, and link hrefs. Review against your own privacy policy before go-live.

## Uninstall

Disable the plugin in the OJS Plugins UI (stops all injection immediately). To remove entirely, delete the `./plugins/umami-analytics` bind-mount line from `docker-compose.yml`, `docker compose up -d ojs`, and delete the `plugins/umami-analytics/` folder.
