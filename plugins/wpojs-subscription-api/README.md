# WP-OJS Subscription API Plugin for OJS

Adds REST API endpoints to OJS for user and subscription management — something OJS doesn't provide natively. Designed for push-sync integrations where an external system (e.g. WordPress with WooCommerce Subscriptions) manages memberships and pushes changes to OJS.

Also handles WordPress password hash verification at login (so members can use their WP password on OJS without resetting) and adds configurable UI messages (login hint, paywall hint, site footer).

## Requirements

- OJS 3.5+
- At least one subscription type configured in OJS

## Installation

1. Copy the plugin folder to `plugins/generic/wpojsSubscriptionApi/` in your OJS installation.

   **Important:** The folder name must be exactly `wpojsSubscriptionApi` (camelCase). Hyphens or underscores break OJS autoloading and the plugin won't appear in the admin UI.

2. Register the plugin in the OJS database (if not auto-detected):
   ```sql
   INSERT INTO versions (major, minor, revision, build, date_installed, current, product_type, product, product_class_name, lazy_load, sitewide)
   VALUES (1, 3, 0, 0, NOW(), 1, 'plugins.generic', 'wpojsSubscriptionApi', 'WpojsSubscriptionApiPlugin', 1, 0);
   ```
3. Enable the plugin in **Website Settings → Plugins → Generic Plugins**.
4. Clear the OJS cache: `rm -rf cache/fc-* cache/wc-* cache/opcache`

## Configuration

The plugin is configured via `plugin_settings` database entries (typically set by a deployment script, not through the UI):

| Setting | Description |
|---|---|
| `apiKeyHash` | SHA-256 hash of the API key. Requests must send `Authorization: Bearer {key}`. |
| `allowedIps` | Comma-separated CIDR ranges for IP allowlisting (e.g. `172.0.0.0/8`). |
| `wpMemberUrl` | URL of the WordPress membership site (used in UI messages). |
| `supportEmail` | Support email address (used in paywall hint message). |
| `loginHint` | Message shown on the login page (e.g. "Log in with your membership email"). |
| `paywallHint` | Message shown on paywalled articles for logged-in non-subscribers. |
| `footerMessage` | Message added to the site footer. |
| `passwordResetHint` | Message on the password change page directing members to WP. |

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
- `Authorization: Bearer {API_KEY}` header (key is validated against the stored SHA-256 hash)
- Request must originate from an allowed IP range

The API key is stored as a hash, never in plaintext. Do not use `?apiToken=` query parameters — they leak into access logs. If using Apache with PHP-FPM, add `CGIPassAuth on` to `.htaccess` (Apache strips `Authorization` headers by default).

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

The second mount exposes the API route handler to OJS's API router.

## License

GNU General Public License v3.0. See [LICENSE](https://www.gnu.org/licenses/gpl-3.0.html).
