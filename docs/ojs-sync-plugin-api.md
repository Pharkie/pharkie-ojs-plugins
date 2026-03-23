# OJS Plugin API Reference

This is the REST API exposed by the custom OJS plugin (`wpojs-subscription-api`). The WP plugin calls these endpoints to sync membership data — you don't need to call them manually unless you're debugging or building custom integrations.

> **Not the native OJS API.** OJS has its own REST API for submissions, issues, and users — but it has no subscription endpoints. This custom plugin fills that gap. See [OJS internals](ojs-internals.md) for the native API.

All endpoints are under `/api/v1/wpojs/`.

Base URL: `{OJS_BASE_URL}/index.php/{journal_path}/api/v1/wpojs`

> **Trying to connect?** Run `wp ojs-sync test-connection` from the WP server to verify connectivity. See the [support runbook](support-runbook.md) if something fails.

## Endpoints

### System

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `GET` | `/ping` | No | Reachability check. Returns `{"status":"ok"}`. |
| `GET` | `/preflight` | Yes | Compatibility check. Verifies OJS internals (Repo methods, DAOs, tables, subscription types, load protection). Returns `{"compatible":true/false, "checks":[...]}`. |
| `GET` | `/subscription-types` | Yes | Lists subscription types for the journal. Returns `{"types":[{"id":1,"name":"..."},...]}`.|

### Users

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `GET` | `/users?email={email}` | Yes | Find user by email. Returns `{"found":true, "userId":N, "email":"...", "username":"...", "disabled":bool}` or `{"found":false}`. |
| `POST` | `/users/find-or-create` | Yes | Idempotent. Finds existing user by email or creates a new one. Assigns Reader role. Body: `{email, firstName, lastName, passwordHash?, username?}`. Returns `{"userId":N, "created":bool}`. |
| `PUT` | `/users/{userId}/email` | Yes | Update user email. Body: `{newEmail}`. Returns 409 if email already in use. |
| `PUT` | `/users/{userId}/password` | Yes | Update user password hash. Body: `{passwordHash}`. |
| `DELETE` | `/users/{userId}` | Yes | GDPR erasure. Anonymises all PII, disables account, expires subscription, removes settings and access keys. Does not delete the user record. |

### Subscriptions

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `POST` | `/subscriptions` | Yes | Idempotent upsert. Creates or updates subscription. Body: `{userId, typeId, dateStart, dateEnd?}`. `dateEnd: null` = non-expiring. Returns `{"subscriptionId":N, "created":bool}`. |
| `GET` | `/subscriptions?userId={userId}` | Yes | Get subscription for user. Returns subscription details including `status` (1=active, 16=expired/other). |
| `PUT` | `/subscriptions/{subscriptionId}/expire` | Yes | Expire by subscription ID. Sets status to 16 (Other). |
| `PUT` | `/subscriptions/expire-by-user/{userId}` | Yes | Expire by user ID. Sets status to 16 (Other). |
| `POST` | `/subscriptions/status-batch` | Yes | Batch status lookup. Body: `{emails:["a@b.com",...]}`. Returns status for multiple users in one call. |

## Authentication

All protected endpoints (marked "Auth: Yes" above) enforce dual-layer auth:

1. **IP allowlist** — Client IP must be in `allowed_ips` (OJS `config.inc.php` `[wpojs]` section). Supports exact match (IPv4/IPv6) and CIDR notation (IPv4 only).
2. **Bearer token** — `Authorization: Bearer {secret}` header. Compared via `hash_equals()` against the `WPOJS_API_KEY_SECRET` environment variable.

## Load protection

The OJS plugin self-monitors response times and returns `429 Too Many Requests` with a `Retry-After` header when under pressure. The WP plugin respects this automatically.

| Avg response time | Action |
|---|---|
| < 500ms | Healthy, request proceeds |
| 500–2000ms | `429`, `Retry-After: 2` |
| > 2000ms | `429`, `Retry-After: 5` |
| < 5 recent samples | Cold start, request proceeds |

## Error responses

All errors return JSON: `{"error": "description"}` with appropriate HTTP status:

| Status | Meaning |
|---|---|
| `400` | Invalid/missing parameters |
| `401` | Invalid or missing API key |
| `403` | IP not in allowlist |
| `404` | User/subscription not found |
| `409` | Email conflict (update email) |
| `429` | Server under load (retry after delay) |
| `500` | Internal error |

## Username sync

The WP plugin sends `$user->user_login` as an optional `username` field in the `find-or-create` call. The OJS plugin sanitizes it to OJS's lowercase-alphanumeric constraint (`preg_replace('/[^a-z0-9]/', '', strtolower($wpUsername))`) and uses it as the base for username generation. If not provided or if it sanitizes to empty, falls back to the existing auto-gen from firstName+lastName. The collision loop (`base`, `base1`, `base2`...) handles duplicates regardless of source.

**Why this matters:** OJS admin screens show usernames, so `bscanlan` (from WP login `BScanlan`) is more recognisable than `benscanlan` (auto-generated from first+last name).

**Login impact:** 536 of 1419 live WP usernames (38%) contain characters stripped by sanitization (dots, hyphens, underscores, spaces, `@` — mostly email-as-username accounts). If those users typed their WP login into OJS, the unsanitized input wouldn't match the sanitized stored username and login would fail. This is mitigated by two things:

1. The login page relabels the "Username or Email" field to just **"Email"** and sets `autocomplete="email"`, steering users toward email login.
2. OJS's `PKPUserProvider::retrieveByCredentials()` checks if the input looks like an email — if so, it does an email lookup (which always works). Only non-email-looking input goes through the username path.

Case-only differences (e.g. `BScanlan` → `bscanlan`) are fine — OJS username lookup is case-insensitive.

## Request logging

All API requests are logged to `wpojs_api_log` with endpoint, method, source IP, HTTP status, and response time (ms). Logs are auto-cleaned after 30 days.
