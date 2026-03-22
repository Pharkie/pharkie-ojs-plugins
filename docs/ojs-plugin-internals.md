# OJS Plugin Internals: `wpojs-subscription-api`

Version 1.3.0 | Requires OJS 3.5+

## Overview

The `wpojs-subscription-api` plugin is a generic OJS plugin that exposes REST endpoints for managing user accounts and subscriptions, called by the WP plugin (`wpojs-sync`). It also replaces the OJS password hasher so members can log in with their WordPress credentials, and injects UI messages (login hint, paywall hint, site footer) into OJS templates.

For the full endpoint table, see [ojs-sync-plugin-api.md](ojs-sync-plugin-api.md). This document covers the internals and behaviour that the endpoint table does not.

## Authentication

Every protected endpoint calls `checkAuth()`, which runs three checks in order:

1. **IP allowlist** (`checkIp`) -- Client IP must match an entry in `allowed_ips` (config.inc.php). Supports exact IPv4/IPv6 addresses and IPv4 CIDR notation (e.g. `172.16.0.0/12`). Uses `REMOTE_ADDR` directly, not `$request->ip()`, to avoid X-Forwarded-For spoofing.
2. **Bearer token** (`checkApiKey`) -- The `Authorization: Bearer <token>` header must match the `WPOJS_API_KEY_SECRET` environment variable. The comparison uses `hash_equals()` (timing-safe).
3. **Load protection** (`checkLoad`) -- See next section.

If any check fails, the request is rejected and logged before reaching the endpoint logic. The `/ping` endpoint is the only exception -- it bypasses all auth for reachability probing.

The API secret is read from the `WPOJS_API_KEY_SECRET` environment variable (not `config.inc.php`). This avoids exposure on the OJS System Info page and keeps it separate from OJS's own `api_key_secret` (used for JWT signing).

**Auth failures:**

| Condition | HTTP status |
|---|---|
| IP allowlist not configured | 403 |
| Client IP not in allowlist | 403 |
| API key secret not configured | 500 |
| Missing or wrong Bearer token | 401 |

## Load protection

The plugin implements adaptive throttling based on its own response times, not arbitrary request counts.

**How it works:** On each authenticated request, `checkLoad()` queries the `wpojs_api_log` table for the average duration of the last 20 successful requests within a 60-second window.

| Average response time | Action |
|---|---|
| < 500ms | Healthy. Request proceeds. |
| 500ms -- 2000ms | Stressed. Returns `429` with `Retry-After: 2`. |
| > 2000ms | Overloaded. Returns `429` with `Retry-After: 5`. |
| < 5 samples in window | Cold start. Request proceeds (not enough data to judge). |

The 429 response includes `avg_ms` in the body so the caller can log it:

```json
{"error": "Server under load. Please retry later.", "avg_ms": 1250.3}
```

The WP plugin respects `Retry-After` and backs off automatically via Action Scheduler retry logic.

## Password hash compatibility

`WpCompatibleHasher` implements Laravel's `Hasher` interface and is registered as a replacement for OJS's default hasher when the plugin is enabled.

**Two WP hash formats are supported:**

| Format | Source | Description |
|---|---|---|
| `$wp$2y$10$...` | Stock WordPress 6.8+ | SHA-384-prehashed bcrypt. WP computes `base64(SHA-384(plaintext))`, then bcrypt-hashes that. The `$wp$` prefix distinguishes it from standard bcrypt. |
| `$2y$10$...` | Bedrock / `roots/wp-password-bcrypt` | Standard bcrypt (no prehash). Bedrock overrides `wp_hash_password()` with plain `password_hash()`. |

**Verification flow at OJS login:**

```
User enters password
    |
    v
WpCompatibleHasher::check()
    |
    +-- Hash starts with "$wp$"?
    |       Yes: strip "$wp$" prefix, SHA-384 the plaintext,
    |            base64-encode, password_verify() against inner bcrypt
    |       No:  standard password_verify()
    |
    v
WpCompatibleHasher::needsRehash()
    |
    +-- "$wp$" prefix? -> Always true (rehash needed)
    |   Standard bcrypt? -> Only if cost < 12
    |
    v
Laravel auto-rehashes to native bcrypt ($2y$12$...)
```

After the first successful login, the user's hash is silently upgraded to standard bcrypt. Subsequent logins skip the WP hash path entirely.

**Key properties:**
- No plaintext passwords are stored or transmitted -- only hashes.
- `make()` always produces standard bcrypt (cost 12). The plugin never writes WP-format hashes.
- The hasher is registered via `PKPUserProvider::setHasher()` during plugin `register()`.

## Request logging

All API requests are logged to the `wpojs_api_log` table (created by the plugin's install migration).

**Table schema:**

| Column | Type | Description |
|---|---|---|
| `log_id` | BIGINT PK | Auto-increment |
| `endpoint` | VARCHAR(255) | Request path |
| `method` | VARCHAR(10) | HTTP method |
| `source_ip` | VARCHAR(45) | Client IP (REMOTE_ADDR) |
| `http_status` | INT | Response status code |
| `duration_ms` | INT, nullable | Request duration in milliseconds |
| `created_at` | DATETIME | Timestamp |

**Logging behaviour:**
- Auth failures (401, 403, 429) are logged immediately by `checkAuth()`.
- Successful requests are logged by `jsonResponse()` after the endpoint completes, so the actual status code and duration are recorded (not a premature 200).
- Duration is measured from `WPOJS_REQUEST_START` (set at the start of `checkAuth()`).
- Logging failures are caught silently -- a broken log table never breaks the API.

**Auto-cleanup (30-day retention):**
- Triggered two ways: (1) on every API request via `maybeCleanupLogs()`, which runs at most once per hour (tracked via a `plugin_settings` timestamp); (2) when an admin opens the Status page.
- Deletes all rows where `created_at` is older than 30 days.

## Admin status page

Accessible from the OJS plugin settings (Website > Plugins > Generic > WP-OJS Subscription API > Status). Requires Journal Manager or Site Admin role.

The status page shows three sections:

**1. Configuration health checks:**

| Check | What it verifies |
|---|---|
| API key defined | `WPOJS_API_KEY_SECRET` env var is set |
| Allowed IPs configured | `allowed_ips` is non-empty |
| WP member URL set | `wp_member_url` is non-empty |
| Support email set | `support_email` is non-empty |
| Load protection | Shows current average response time and sample count |

All-green indicator when every check passes.

**2. Sync stats:**
- Active individual subscriptions (count)
- Users created by sync (count, based on `wpojs_created_by_sync` user setting)
- Subscription types in use (breakdown by type with counts)

**3. Recent API activity:**
- Last 50 log entries with timestamp, method, endpoint, source IP, HTTP status
- Status codes colour-coded: green for 2xx, red for 4xx+

There is also a **Settings** page (separate from Status) where admins can edit the three UI messages: login hint, paywall hint, and footer message. Messages support `<a>` tags and are capped at 1000 characters.

## GDPR erasure

`DELETE /users/{userId}` does not delete the OJS user record (which would break referential integrity with reviews, submissions, etc.). Instead it anonymises all PII:

**Fields overwritten:**

| Field | New value |
|---|---|
| email | `deleted_{userId}@anonymised.invalid` |
| username | `deleted_{userId}` |
| givenName | `Deleted` |
| familyName | `User` |
| affiliation | (cleared) |
| biography | (cleared) |
| orcid | (cleared) |
| url | (cleared) |
| phone | (cleared) |
| mailingAddress | (cleared) |
| disabled | `true` |

**Additional cleanup:**
- Active subscription in the journal is set to status `OTHER` (16).
- User settings deleted: `wpojs_created_by_sync`, `wpojs_welcome_email_sent`, `preferredPublicName`, `signature`, `mailingAddress`, `phone`, `orcid`.
- All `access_keys` rows for the user are deleted (password reset tokens).

**What is preserved:** The user record itself, any editorial assignments, review history, and submission metadata. This maintains journal integrity while removing all personally identifiable information.

## Subscription management

### Upsert behaviour (`POST /subscriptions`)

The subscription endpoint is idempotent. OJS allows only one individual subscription per user per journal.

**Decision tree:**

```
Does user already have a subscription for this journal?
    |
    No  -> INSERT new subscription (status=ACTIVE)
    |
    Yes -> Is it currently active?
        |
        No  -> REACTIVATION: set status=ACTIVE, apply all
        |      incoming values (typeId, dateStart, dateEnd).
        |      WP is source of truth for reactivations.
        |
        Yes -> EXTENSION (idempotent, prevents shortening):
               - dateEnd null (incoming) + existing has date -> switch to non-expiring
               - incoming dateEnd > existing dateEnd        -> extend
               - incoming dateEnd <= existing dateEnd       -> no-op
               - existing is non-expiring                   -> keep non-expiring (no-op)
               - typeId changed                             -> update (tier upgrade/downgrade)
```

**Race condition handling:** If a concurrent request inserts a duplicate subscription between the existence check and the INSERT, the `QueryException` is caught. The plugin re-reads the existing subscription and updates it instead.

### Status values

| Constant | Value | Meaning |
|---|---|---|
| `STATUS_ACTIVE` | 1 | Active subscription (has paywall access) |
| `STATUS_OTHER` | 16 | Used for expired-by-sync and GDPR erasure |

The plugin uses status 16 ("Other") rather than OJS's built-in expired/cancelled statuses. This keeps sync-managed expirations distinct from OJS-native ones.

### Expiration

Two convenience endpoints:
- `PUT /subscriptions/{subscriptionId}/expire` -- expire by subscription ID
- `PUT /subscriptions/expire-by-user/{userId}` -- expire by user ID (saves WP an extra lookup)

Both are idempotent: setting status to OTHER when already OTHER is a no-op.

## User creation

### Find-or-create pattern (`POST /users/find-or-create`)

1. Look up existing user by email (includes disabled accounts).
2. If found, return existing `userId` with `created: false`. No fields are updated.
3. If not found, create a new user:

**Username generation:**
- Base: lowercase `firstName + lastName`, stripped of non-alphanumeric characters.
- If base is empty, falls back to `user`.
- Appends incrementing suffix (`1`, `2`, `3`...) until unique.
- On username collision (QueryException), retries once with a random 6-character hex suffix (e.g. `janedoe_a3f2b1`).

**New user properties:**
- Password: WP hash stored directly if `passwordHash` is provided (custom hasher verifies at login). Otherwise, random password with `mustChangePassword = true`.
- `dateRegistered` and `dateValidated` set to current time (email marked as verified -- no confirmation email sent).
- `disabled = false`.
- Assigned the **Reader** role for the current journal.
- Tagged with `wpojs_created_by_sync` user setting (for status page stats).

### Race condition handling

If two concurrent requests try to create the same user (same email), the second request catches the `QueryException`, re-reads the user by email, and returns the existing record with `created: false`. The Reader role assignment is re-applied (idempotent).

## OJS configuration

The API secret is read from the `WPOJS_API_KEY_SECRET` environment variable. Set it in Docker Compose `.env`, or for non-Docker installs in Apache/nginx config (`SetEnv WPOJS_API_KEY_SECRET ...`).

Non-secret settings go in `config.inc.php` under the `[wpojs]` section:

```ini
[wpojs]
; Comma-separated IP allowlist. Supports exact IPs and IPv4 CIDR notation.
allowed_ips = "203.0.113.10,172.16.0.0/12"

; WP membership management URL (used in footer message).
wp_member_url = "https://your-wp-site.example.org/membership"

; Support contact email (used in paywall hint message).
support_email = "support@example.org"
```

| Setting | Source | Required | Used by |
|---|---|---|---|
| `WPOJS_API_KEY_SECRET` | env var | Yes | Bearer token auth. Must match `WPOJS_API_KEY` on the WP side. |
| `allowed_ips` | `[wpojs]` | Yes | IP allowlist. Empty = all requests blocked (403). |
| `wp_member_url` | `[wpojs]` | No | Footer message template (`{wpUrl}` placeholder). Footer hidden if empty. |
| `support_email` | `[wpojs]` | No | Paywall hint template (`{supportEmail}` placeholder). Hint hidden if empty. |

**UI messages** (login hint, paywall hint, footer) are stored in `plugin_settings` (database), not in config.inc.php. PHP INI files corrupt values containing double quotes and curly braces (needed for HTML links and placeholders). Instance defaults are written by `setup-ojs.sh` during environment setup. Admins can edit them via the plugin Settings page in OJS.
