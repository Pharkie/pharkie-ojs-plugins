# Phase 0 Findings: OJS API Audit

Research conducted 2026-02-16 via OJS source code, swagger specs, PKP forums, and GitHub issues.

> **Note:** The architectural conclusions from this research are recorded in `architecture.md`. This file preserves the raw findings for reference.

## Key finding: No subscription REST API

**The OJS REST API has NO subscription endpoints.** Not in 3.4, not in 3.5. Confirmed by:
- The [OJS swagger spec](https://github.com/pkp/ojs/blob/main/docs/dev/swagger-source.json) — 37 endpoint categories, no `subscriptions`
- [PKP forum confirmation](https://forum.pkp.sfu.ca/t/are-there-api-or-other-options-for-subscription-management-available-in-ojs-3-3/86106) — PKP devs acknowledge the gap
- [Bulk subscription import request](https://forum.pkp.sfu.ca/t/ojs3-bulk-import-subscriptions/62294) was told it's "not a development priority"

This means subscription management requires either a custom OJS plugin (recommended) or direct DB writes (fallback). See `architecture.md` for the full analysis.

## User API: exists but with caveats

**Conflicting information about user creation:**
- The OJS swagger spec for `main` branch lists `GET /users`, `GET /users/{id}`, `PUT /users/{id}/endRole/{groupId}` — but **no POST endpoint for creating users**
- Some documentation and older OJS versions suggest `POST /users` exists
- **Must verify against actual OJS version**

**What definitely works:**
- `GET /api/v1/users?searchPhrase=email@example.com` — find users by email
- Reading user details, listing users with filters

**Authentication:**
- Bearer token via `Authorization: Bearer <token>` header
- Token generated per-user at User Profile > API Key
- Requires `api_key_secret` set in `config.inc.php`
- Apache + PHP-FPM: `CGIPassAuth on` required in `.htaccess`
- Fallback: `?apiToken=<token>` query parameter
- Minimum role: Journal Manager, Editor, or Subeditor; full control requires Site Administrator

## OJS subscription internals

Full PHP DAO classes exist internally — see `ojs-api.md` for complete schema and code patterns. The classes have complete CRUD (`IndividualSubscriptionDAO.insertObject()`, `updateObject()`, `deleteById()`, `renewSubscription()`). Our custom OJS plugin wraps these as REST endpoints.

## OJS 3.5 plugin extensibility

OJS 3.5 restored plugin-registered API endpoints via [pkp-lib #9434](https://github.com/pkp/pkp-lib/issues/9434). This is required for the custom OJS plugin. SEA is on 3.4.0-9, so **upgrade to 3.5 is a prerequisite**. See `ojs-api.md` for the 3.5 plugin API pattern and breaking changes.
