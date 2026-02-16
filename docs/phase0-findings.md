# Phase 0 Findings: OJS API Audit

Research conducted 2026-02-16 via OJS source code, swagger specs, PKP forums, and GitHub issues.

## The headline

**The OJS REST API has NO subscription endpoints.** None. Zero. Not in 3.4, not in 3.5. This is confirmed by:
- The [OJS swagger spec](https://github.com/pkp/ojs/blob/main/docs/dev/swagger-source.json) — 37 endpoint categories, no `subscriptions`
- [PKP forum confirmation](https://forum.pkp.sfu.ca/t/are-there-api-or-other-options-for-subscription-management-available-in-ojs-3-3/86106) — PKP devs acknowledge the gap
- PKP's only official suggestion is the Subscription SSO plugin (which we already rejected)
- [Bulk subscription import request](https://forum.pkp.sfu.ca/t/ojs3-bulk-import-subscriptions/62294) was told it's "not a development priority"

This means the original plan ("WP calls OJS REST API to create subscriptions") **cannot work as described.** We need to choose a different mechanism for the subscription-write side.

## User API: exists but with caveats

**Conflicting information about user creation:**
- The OJS swagger spec for `main` branch lists `GET /users`, `GET /users/{id}`, `PUT /users/{id}/endRole/{groupId}` — but **no POST endpoint for creating users**
- Some documentation and older OJS versions suggest `POST /users` exists
- This needs to be verified against the actual OJS version SEA is running

**What definitely works:**
- `GET /api/v1/users?searchPhrase=email@example.com` — find users by email
- Reading user details
- Listing users with filters

**What may not work:**
- Creating users via API (POST /users) — contradictory sources
- Updating user details via API (PUT /users/{id}) — not in current swagger spec

**Authentication:**
- Bearer token via `Authorization: Bearer <token>` header
- Token generated per-user at User Profile > API Key
- Requires `api_key_secret` set in `config.inc.php`
- Apache + PHP-FPM setups need `CGIPassAuth on` in `.htaccess` or the header gets stripped
- Fallback: `?apiToken=<token>` query parameter
- Minimum role for user endpoints: Journal Manager, Editor, or Subeditor
- Full control requires Site Administrator

## OJS subscription internals (what exists inside OJS)

OJS has a **complete internal PHP layer** for subscriptions — it just has no HTTP interface.

### Database schema

| Table | Purpose |
|---|---|
| `subscriptions` | Core: `subscription_id`, `journal_id`, `user_id`, `type_id`, `date_start`, `date_end`, `status`, `membership`, `reference_number`, `notes` |
| `subscription_types` | Type definitions: cost, currency, duration, format (online/print/both), institutional flag |
| `subscription_type_settings` | Localized names/descriptions |
| `institutional_subscriptions` | Extra fields for institutional subs: institution name, domain |
| `institutional_subscription_ip` | IP ranges for institutional access |

### Status constants

```
SUBSCRIPTION_STATUS_ACTIVE = 1
SUBSCRIPTION_STATUS_NEEDS_INFORMATION = 2
SUBSCRIPTION_STATUS_NEEDS_APPROVAL = 3
SUBSCRIPTION_STATUS_AWAITING_MANUAL_PAYMENT = 4
SUBSCRIPTION_STATUS_AWAITING_ONLINE_PAYMENT = 5
SUBSCRIPTION_STATUS_OTHER = 16
```

### DAO classes (full CRUD exists internally)

- `IndividualSubscriptionDAO` — `insertObject()`, `updateObject()`, `getById()`, `getByUserIdForJournal()`, `deleteById()`, `renewSubscription()`
- `InstitutionalSubscriptionDAO` — same pattern + domain/IP validation
- `SubscriptionTypeDAO` — type management

### Creating a subscription in PHP (internal pattern)

```php
use APP\subscription\IndividualSubscription;
use PKP\db\DAORegistry;

$dao = DAORegistry::getDAO('IndividualSubscriptionDAO');
$sub = new IndividualSubscription();
$sub->setJournalId($journalId);
$sub->setUserId($userId);
$sub->setTypeId($typeId);
$sub->setStatus(1); // SUBSCRIPTION_STATUS_ACTIVE
$sub->setDateStart('2026-01-01');
$sub->setDateEnd('2026-12-31');
$sub->setMembership('SEA-12345');
$dao->insertObject($sub);
```

## OJS 3.5 plugin API extensibility

**Good news:** OJS 3.5 restored the ability for plugins to register custom REST API endpoints via [pkp-lib #9434](https://github.com/pkp/pkp-lib/issues/9434). This means a custom OJS plugin can expose subscription CRUD as proper REST endpoints using the internal DAOs.

The pattern (3.5+):
```php
Hook::add('APIHandler::endpoints::plugin', function($hookName, $apiRouter) {
    $apiRouter->registerPluginApiControllers([
        new SubscriptionApiController(),
    ]);
    return Hook::CONTINUE;
});
```

An [example plugin](https://github.com/touhidurabir/apiExample) demonstrates the full pattern.

**Caveat:** OJS 3.5 migrated from Slim to Laravel routing — any plugin must use Laravel patterns, not Slim.

**For OJS 3.4:** Plugin API endpoint registration is harder/less supported. Would need older `LoadHandler` hook pattern.

## Revised options

Given these findings, here are the viable architectures, re-ranked:

### Option A: Subscription SSO Plugin (FASTEST — reconsidered)

We rejected this earlier, but the findings change the calculus. Here's what it actually does:

- When a user hits paywalled content, OJS calls a **verification URL** (on your WordPress site)
- WP checks if the user has an active membership and responds yes/no
- OJS grants/denies access based on the response
- **No subscription records created in OJS** — access is delegated in real-time

**How it works mechanically:**
1. Install + configure the [Subscription SSO plugin](https://github.com/asmecher/subscriptionSSO) in OJS
2. Set verification URL to a WP endpoint (e.g., `https://sea.org.uk/wp-json/sea-ojs/v1/verify`)
3. Plugin sends a session/user identifier to WP
4. WP returns success/failure based on membership status
5. Plugin caches the result for configurable hours

**Pros:**
- No subscription management needed in OJS at all
- No user sync needed
- WordPress stays fully in control
- Very little custom code (WP endpoint + OJS plugin config)
- Ships in days, not weeks

**Cons (why we rejected it before):**
- "Conflicts with OJS paywall logic" — **need to verify if this is still true**
- Non-members buying individual articles: does the SSO plugin interfere with normal OJS purchases?
- If the SSO plugin overrides ALL access checks, non-member purchases might break
- Single point of failure: if WP is down, no member access
- "Fragile dependencies" — but the plugin is maintained by PKP's lead dev (Alec Smecher)
- Members don't have OJS accounts — can they save preferences, track reading history? Probably not needed for SEA.

**Critical question to resolve:** Does the Subscription SSO plugin coexist with OJS's normal paywall for non-members? If yes, this is the fastest path. If no, we're back to the harder options.

### Option B: Custom OJS Plugin + WP Plugin (CLEANEST — medium effort)

Build a small OJS plugin that exposes subscription CRUD endpoints. WP plugin calls those endpoints.

**Components:**
1. **OJS plugin** (`sea-subscription-api`): Registers REST endpoints for subscription CRUD using internal DAOs
2. **WP plugin** (`sea-ojs-sync`): Calls OJS user API + custom subscription endpoints on membership changes

**Pros:**
- Clean architecture, proper separation
- Uses OJS's own validated DAO layer
- Subscriptions visible in OJS admin UI
- OJS paywall works completely normally
- Non-member purchases unaffected

**Cons:**
- Requires OJS 3.5+ for clean plugin API (3.4 is harder)
- Two plugins to build and maintain
- Need OJS development/deployment capability (not just WP)
- OJS upgrades could break the plugin (though using DAOs is relatively stable)

**Effort:** 2-4 weeks for both plugins, depending on complexity

### Option C: Direct Database Writes (FASTEST after SSO — but fragile)

WP plugin writes directly to OJS database tables.

**Pros:**
- No OJS plugin needed
- Fastest to implement after SSO approach
- Works on any OJS version

**Cons:**
- Bypasses OJS validation, hooks, and event system
- Schema changes between OJS versions will silently break it
- No audit trail
- If WP and OJS are on different servers, need cross-database access
- Hard to debug when things go wrong

**Effort:** 1-2 weeks, but ongoing maintenance risk

### Option D: Janeway + Custom Paywall (NUCLEAR OPTION)

Abandon OJS entirely. Use Janeway (which has proper OAuth and better APIs) and build a simple paywall.

**Only consider if:**
- OJS integration proves more expensive than switching platforms
- The Subscription SSO plugin doesn't coexist with OJS purchases
- The custom OJS plugin approach is blocked by OJS version constraints

**Effort:** Weeks to months. Not recommended unless everything else fails.

## Recommendation

**Investigate Option A (Subscription SSO) first.** Specifically, answer one question:

> Does the Subscription SSO plugin coexist with OJS's normal purchase flow for non-members?

If **yes**: Ship Option A. It's the fastest, simplest, least code to maintain. WP stays source of truth. OJS paywall works for non-members. Build one small WP REST endpoint. Done.

If **no**: Ship Option B (custom OJS plugin + WP plugin). More work but architecturally clean. Requires OJS 3.5+.

If **OJS is 3.4 and can't upgrade**: Option C (direct DB writes) as a stopgap, with a plan to migrate to Option B when OJS is upgraded.

## Still need to verify (requires SEA input)

- [ ] Which OJS version is SEA running? (3.4.x vs 3.5.x — determines plugin approach)
- [ ] Which WP membership plugin? (determines WP hook points)
- [ ] What membership tiers exist and which grant journal access?
- [ ] Are WP and OJS on the same server?
- [ ] Can we test the Subscription SSO plugin's interaction with OJS purchase flow?
- [ ] Does SEA have OJS admin access for plugin installation and API key setup?
