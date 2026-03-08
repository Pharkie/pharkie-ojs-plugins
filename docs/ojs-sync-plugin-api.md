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
| `GET` | `/users?email={email}` | Yes | Find user by email. Returns `{"found":true, "userId":N, "email":"...", "username":"...", "disabled":bool}` or `{"found":false}`. Also accepts `?userId={id}`. |
| `POST` | `/users/find-or-create` | Yes | Idempotent. Finds existing user by email or creates a new one. Assigns Reader role. Body: `{email, firstName, lastName, passwordHash?}`. Returns `{"userId":N, "created":bool}`. |
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
2. **Bearer token** — `Authorization: Bearer {secret}` header. Compared via `hash_equals()` against `api_key_secret` in `[wpojs]` config section (falls back to `[security]`).

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

## Request logging

All API requests are logged to `wpojs_api_log` with endpoint, method, source IP, HTTP status, and response time (ms). Logs are auto-cleaned after 30 days.
