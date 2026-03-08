# Non-Docker Setup

How to install and configure the WP-OJS integration on servers where OJS and WordPress are running natively.

For Docker-based setup, see [`docker/README.md`](../docker/README.md).

---

## Prerequisites

- OJS 3.5+ installed and running
- WordPress with WooCommerce Subscriptions installed and running
- Both servers can reach each other over HTTPS
- SSH/admin access to both servers

---

## OJS plugin deployment

### 1. Copy plugin files

Copy the `plugins/wpojs-subscription-api/` directory to the OJS plugins folder. **The folder name must be `wpojsSubscriptionApi`** (camelCase, no hyphens, no underscores). OJS validates plugin folder names against its product name rules — hyphens/underscores cause "Invalid product name" errors and break the Plugins admin page.

```bash
cp -r plugins/wpojs-subscription-api/ /path/to/ojs/plugins/generic/wpojsSubscriptionApi/
chown -R www-data:www-data /path/to/ojs/plugins/generic/wpojsSubscriptionApi/
```

### 2. Mount the API route entry point

OJS 3.5 discovers API endpoints by looking for files at `api/v1/{handler}/index.php` in the OJS root directory. Plugins cannot register API routes programmatically — the physical file must exist at this location.

Create a symlink (preferred) or copy:

```bash
# Symlink (stays in sync with plugin updates)
ln -s /path/to/ojs/plugins/generic/wpojsSubscriptionApi/api/v1/wpojs \
      /path/to/ojs/api/v1/wpojs

# Or copy (must redo after plugin updates)
cp -r /path/to/ojs/plugins/generic/wpojsSubscriptionApi/api/v1/wpojs \
      /path/to/ojs/api/v1/wpojs
```

**If this step is skipped**, all `/api/v1/wpojs/...` requests will return `404 endpointNotFound`. The WP settings page will show "Could not connect to OJS" and the type dropdowns will be disabled.

### 3. Configure `config.inc.php`

Add the `[wpojs]` section to OJS's `config.inc.php`:

```ini
;;;;;;;;;;;;;;;;;;
; WP-OJS Integration
;;;;;;;;;;;;;;;;;;

[wpojs]
; Shared secret for API authentication (Bearer token).
; IMPORTANT: Use a non-numeric value or quote it. PHP's INI parser
; treats unquoted numeric values as integers, which causes a type
; error in hash_equals(). Use something like: "my-secret-key-here"
api_key_secret = "your-shared-secret-here"

; Comma-separated list of IP addresses allowed to call the API.
; Use the WP server's outbound IP. Supports CIDR notation (e.g. 172.16.0.0/12).
allowed_ips = "1.2.3.4"

; URL to your WordPress membership site (shown in OJS footer message).
wp_member_url = "https://www.example.org"

; Support email (shown in OJS paywall hint for logged-in non-subscribers).
support_email = "support@example.org"

```

**Config value quoting:** Always quote string values in `config.inc.php`. PHP's INI parser silently coerces unquoted numeric values to integers, which causes `hash_equals()` to throw a TypeError. This applies to `api_key_secret`, `allowed_ips`, and any other string values.

### 4. Enable the plugin in OJS

Go to OJS Admin Dashboard → Settings → Website → Plugins tab → Installed Plugins → Generic Plugins → find "WP-OJS Subscription API" → Enable.

On first enable, OJS runs the plugin's migration (`WpojsApiLogMigration`) and creates the `wpojs_api_log` table automatically.

**If the table is not created** (e.g. OJS skipped the migration), you can:
- Disable and re-enable the plugin (OJS re-runs migrations on enable)
- Or create it manually:

```sql
CREATE TABLE wpojs_api_log (
    log_id BIGINT AUTO_INCREMENT PRIMARY KEY,
    endpoint VARCHAR(255) NOT NULL,
    method VARCHAR(10) NOT NULL,
    source_ip VARCHAR(45) NOT NULL,
    http_status SMALLINT NOT NULL,
    duration_ms INT UNSIGNED NULL,
    created_at DATETIME NOT NULL,
    INDEX wpojs_api_log_created_at (created_at),
    INDEX wpojs_api_log_load (created_at, duration_ms)
);
```

### 5. Create at least one subscription type

The integration creates OJS subscriptions for members. This requires at least one subscription type to exist in OJS.

Go to OJS Admin → Subscriptions → Subscription Types → Create New Subscription Type:
- **Name:** e.g. "Individual Membership" (descriptive, for admin reference)
- **Type:** Individual
- **Duration/format:** doesn't matter for sync — the WP plugin sets explicit `dateStart`/`dateEnd`

**Finding Subscription Type IDs:** In OJS Admin, go to Subscriptions → Subscription Types. Click Edit on each type — the `typeId` is in the URL. Alternatively, run this query on the OJS database:

```sql
SELECT st.type_id, sts.setting_value AS type_name
FROM subscription_types st
JOIN subscription_type_settings sts ON st.type_id = sts.type_id
  AND sts.setting_name = 'name'
  AND sts.locale = 'en'
WHERE st.journal_id = 1;
```

### 6. Apache configuration

If running Apache with PHP-FPM (common), the `Authorization` header is stripped by default. Add to `.htaccess` or Apache config:

```apache
CGIPassAuth on
```

Without this, all authenticated API requests will fail with 401 (missing API key).

### 7. Verify with ping

Test reachability (no auth required):

```bash
curl https://your-ojs-site.example.org/index.php/journalpath/api/v1/wpojs/ping
# Expected: {"status":"ok"}
```

---

## WP plugin deployment

### 1. Copy plugin files

```bash
cp -r plugins/wpojs-sync/ /path/to/wordpress/wp-content/plugins/wpojs-sync/
```

### 2. Set the API key

Add to `wp-config.php` (before the "That's all" line):

```php
define('WPOJS_API_KEY', 'your-shared-secret-here');
```

This must match the `api_key_secret` value in OJS's `config.inc.php`.

### 3. Activate and configure

1. Activate the plugin in WP Admin → Plugins.
2. Go to WP Admin → OJS Sync → Settings.
3. Set **OJS Base URL** — must include the journal path, e.g.: `https://your-ojs-site.example.org/index.php/journalpath`
4. Add **Product Mappings** (WC Product → OJS Type): for each WooCommerce Subscription product that grants journal access, map its Product ID to the OJS Subscription Type ID.
5. If using **WordPress Role-Based Access**, select the roles and set the OJS Type.
6. Verify the **OJS Connection** status shows "Connected to OJS" with the correct number of subscription types.

### 4. Run initial sync

```bash
# Dry run first — see what would happen
wp ojs-sync sync --dry-run

# Full sync — creates OJS users + subscriptions
wp ojs-sync sync

# Check results
wp ojs-sync status
```

Members can now log in to OJS with their WP email and password — no welcome emails or password setup needed.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| WP test returns 404 / "endpoint not found" | API route file not mounted | Symlink/copy `api/v1/wpojs/` to OJS root (step 2) |
| WP test returns 403 "IP not allowed" | WP server IP not in allowlist | Add IP to `allowed_ips` in `config.inc.php` |
| WP test returns 401 "Invalid or missing API key" | Secret mismatch or missing `CGIPassAuth` | Check both secrets match; add `CGIPassAuth on` to Apache |
| `hash_equals()` type error in OJS logs | Numeric-only secret parsed as integer | Quote the value in `config.inc.php` or use a non-numeric secret |
| "Invalid product name" / Plugins page stuck | Wrong plugin folder name | Must be `wpojsSubscriptionApi` (camelCase, no hyphens/underscores) |
| 500 error on API requests | `wpojs_api_log` table missing | Disable + re-enable plugin, or create table manually (step 4) |
| "No subscription types" in preflight | Empty `subscription_types` table | Create one in OJS admin (step 5) |
| WP sync log shows 0 activations | No product mapping configured | Add mappings in WP OJS Sync settings (step 3) |
| OJS autoload error (`WpojsApiLog not found`) | Wrong folder name breaking autoloader | Folder must be `wpojsSubscriptionApi` to match PHP namespace |

---

## What NOT to do

**Don't try to register API routes from the plugin.** OJS 3.5's `APIRouter` discovers routes by scanning for `api/v1/{path}/index.php` files. There is no hook or service provider to register routes programmatically. The symlink/copy approach in step 2 is the correct pattern.

**Don't add autoloader hacks (manual `require_once`, `class_alias`).** The OJS Composer autoloader maps `APP\plugins\generic\wpojsSubscriptionApi\*` → `plugins/generic/wpojsSubscriptionApi/*.php`. This works when the folder name matches the namespace. If autoloading fails, fix the folder name — don't add workarounds.

**Don't auto-create subscription types.** Subscription types have business meaning (name, tier, duration) that only the journal admin can decide. The preflight check warns if none exist.

**Don't use `?apiToken=` query parameter.** This leaks the key into web server access logs. Use `Authorization: Bearer <token>` header instead (requires `CGIPassAuth on` for Apache + PHP-FPM).
