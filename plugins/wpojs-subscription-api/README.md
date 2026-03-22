# WP-OJS Subscription API Plugin for OJS

Adds REST API endpoints to OJS for user and subscription management — something OJS doesn't provide natively. Designed for push-sync integrations where an external system (e.g. WordPress with WooCommerce Subscriptions) manages memberships and pushes changes to OJS.

Also handles WordPress password hash verification at login (so members can use their WP password on OJS without resetting) and adds configurable UI messages (login hint, paywall hint, site footer).

## Requirements

- OJS 3.5+
- At least one subscription type configured in OJS

## Installation

1. Copy the plugin folder to `plugins/generic/wpojsSubscriptionApi/` in your OJS installation.

   **Important:** The folder name must be exactly `wpojsSubscriptionApi` (camelCase). Hyphens or underscores break OJS autoloading and the plugin won't appear in the admin UI.

2. Mount the API route handler into OJS's API directory:
   ```
   plugins/wpojs-subscription-api/api/v1/wpojs  →  api/v1/wpojs
   ```
   (In Docker, add a read-only bind mount. On bare metal, symlink or copy.)

3. Register the plugin in the OJS database (if not auto-detected):
   ```sql
   INSERT INTO versions (major, minor, revision, build, date_installed, current, product_type, product, product_class_name, lazy_load, sitewide)
   VALUES (1, 3, 0, 0, NOW(), 1, 'plugins.generic', 'wpojsSubscriptionApi', 'WpojsSubscriptionApiPlugin', 1, 0);
   ```
   Check `version.xml` for the current version numbers before running this.

4. Enable the plugin in **Website Settings → Plugins → Generic Plugins**.
5. Clear the OJS cache: `rm -rf cache/fc-* cache/wc-* cache/opcache`

## Configuration

Settings are split across two locations:

### Environment variable

The API shared secret is read from the `WPOJS_API_KEY_SECRET` environment variable (not `config.inc.php`). Set it in your Docker Compose `.env` file, or for non-Docker installs, in your Apache/nginx config (`SetEnv WPOJS_API_KEY_SECRET ...`).

### `config.inc.php` (server config)

Add a `[wpojs]` section for non-secret settings:

```ini
[wpojs]
allowed_ips = "1.2.3.4,10.0.0.0/8"
wp_member_url = "https://your-wordpress-site.example.org"
support_email = "support@example.org"
```

| Setting | Source | Description |
|---|---|---|
| `WPOJS_API_KEY_SECRET` | env var | Shared secret for API authentication. Requests must send `Authorization: Bearer {secret}`. Compared using timing-safe `hash_equals()`. Read from environment to avoid exposure on OJS System Info page. |
| `allowed_ips` | `[wpojs]` | Comma-separated IPs or CIDR ranges. Requests from other IPs are rejected (403). |
| `wp_member_url` | `[wpojs]` | WordPress membership site URL. Used in UI messages (`{wpUrl}` placeholder). |
| `support_email` | `[wpojs]` | Support email. Used in paywall hint (`{supportEmail}` placeholder). |

### `plugin_settings` table (UI messages)

These settings contain HTML and special characters that break PHP INI parsing, so they're stored in the database. Set them via the OJS admin UI (**Plugins → WP-OJS Subscription API → Settings**) or via a deployment script:

| Setting | Description |
|---|---|
| `loginHint` | Message shown on the login page (e.g. "Log in with your membership email"). Supports `{supportEmail}` placeholder. |
| `footerMessage` | Message added to the site footer. Supports `{wpUrl}` placeholder. |
| `passwordResetHint` | Message on the password change page. Supports `{wpResetUrl}` placeholder (auto-generated from `wp_member_url`). |

## API endpoints

All endpoints are under `/api/v1/wpojs/`. Authentication is via `Authorization: Bearer {API_KEY}` header except where noted.

### System

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `GET` | `/ping` | No | Health check. Returns `{"status":"ok"}`. |
| `GET` | `/preflight` | Yes | Compatibility check. Verifies OJS internals. |
| `GET` | `/subscription-types` | Yes | Lists subscription types for the journal. |

### Users

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `GET` | `/users?email={email}` | Yes | Find user by email. |
| `POST` | `/users/find-or-create` | Yes | Idempotent. Find or create user, assign Reader role. |
| `PUT` | `/users/{userId}/email` | Yes | Update user email (409 if already in use). |
| `PUT` | `/users/{userId}/password` | Yes | Update user password hash. |
| `DELETE` | `/users/{userId}` | Yes | GDPR erasure (anonymise, disable, expire subscription). |

### Subscriptions

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `POST` | `/subscriptions` | Yes | Idempotent upsert. Create or update subscription. |
| `GET` | `/subscriptions?userId={userId}` | Yes | Get subscription for user. |
| `PUT` | `/subscriptions/{id}/expire` | Yes | Expire subscription by ID. |
| `PUT` | `/subscriptions/expire-by-user/{userId}` | Yes | Expire subscription by user ID. |
| `POST` | `/subscriptions/status-batch` | Yes | Batch status lookup for multiple users. |

For full endpoint details including request/response schemas, see the [API reference](../../docs/ojs-sync-plugin-api.md).

## WordPress password hashes

The plugin includes `WpCompatibleHasher.php` which verifies WordPress `$P$` phpass hashes at OJS login time. When a member logs into OJS with their WP password:

1. OJS tries its native bcrypt hash — fails (password was synced as a WP hash).
2. The plugin intercepts the auth check and tries the WP phpass algorithm.
3. If it matches, login succeeds and OJS lazy-rehashes the password to native bcrypt.
4. Next login uses bcrypt directly — no WP hash check needed.

This means members can log into OJS with their existing WP password immediately after bulk sync, with no "reset your password" step.

## Authentication

API requests must include:
- `Authorization: Bearer {secret}` header — the secret is compared against the `WPOJS_API_KEY_SECRET` environment variable using timing-safe `hash_equals()`.
- Request must originate from an IP in the `allowed_ips` allowlist.

**Important:** If using Apache with PHP-FPM, add `CGIPassAuth on` to `.htaccess` — Apache strips `Authorization` headers by default. Do not use `?apiToken=` query parameters (they leak into access logs).

## Docker deployment

Bind-mount the plugin directory and its API routes:

```yaml
# docker-compose.yml
services:
  ojs:
    volumes:
      - ./plugins/wpojs-subscription-api:/var/www/html/plugins/generic/wpojsSubscriptionApi
      - ./plugins/wpojs-subscription-api/api/v1/wpojs:/var/www/html/api/v1/wpojs:ro
```

The `[wpojs]` settings must also be present in your OJS `config.inc.php`. If using a template (e.g. `config.inc.php.tmpl` with `envsubst`), add the section there.

## Uninstallation

1. Disable the plugin in **Website Settings → Plugins**.
2. Remove the plugin directory and API route mount.
3. Optionally clean up: `DELETE FROM versions WHERE product = 'wpojsSubscriptionApi';` and `DELETE FROM plugin_settings WHERE plugin_name = 'wpojssubscriptionapiplugin';`

## LLM Generated, Human Reviewed

This code was generated with Claude Code (Anthropic, Claude Opus 4.6). Development was overseen by the human author with attention to reliability and security. Architectural decisions, configuration choices, and development sessions were closely planned, directed and verified by the human author throughout. The code and test results were reviewed and tested by the human author beyond the LLM. Still, the code has had limited manual review, I encourage you to make your own checks and use this code at your own risk.

## License

PolyForm Noncommercial 1.0.0 — see [LICENSE.md](../../LICENSE.md).
