# Architecture Options

Last updated: 2026-02-16

## Context

The OJS REST API has no subscription endpoints (see `phase0-findings.md`). This rules out the original plan of "WP calls OJS API to create subscriptions" and leaves four viable approaches.

## Decision status: PENDING

Blocked on answers from SEA — see "blocking questions" at bottom.

---

## Option A: Subscription SSO Plugin (FASTEST — days not weeks)

Previously rejected as a category ("SSO plugins are fragile"), but this specific plugin is simpler than originally understood and is maintained by PKP's lead developer.

### How it works

```
Member hits paywall
  → OJS Subscription SSO plugin fires
  → Calls WP verification endpoint with user identifier
  → WP checks membership status
  → Returns match/no-match
  → OJS grants or denies access
  → Result cached for N hours

Non-member hits paywall
  → SSO plugin fires, WP says "not a member"
  → OJS falls through to normal purchase flow (£3 article / £25 issue / £18 back issue)
```

### Components

1. **OJS side:** Install + configure [Subscription SSO plugin](https://github.com/asmecher/subscriptionSSO)
   - Set verification URL → WP endpoint
   - Set incoming parameter name
   - Set verification regex (pattern for "yes, this is a member")
   - Set cache duration (hours)
2. **WP side:** Small plugin or REST endpoint (`/wp-json/sea-ojs/v1/verify`)
   - Receives user identifier
   - Looks up membership status
   - Returns response matching the regex

### Pros
- Minimal custom code (~100 lines on WP side, zero on OJS side)
- No subscription records to manage in OJS
- No user sync needed
- No password sync needed
- WP stays sole source of truth
- Ships in 3-5 days

### Cons
- **Must verify:** does the plugin coexist with OJS purchases for non-members?
- No subscription records visible in OJS admin (members are invisible to OJS)
- Single point of failure: WP down = no member access (mitigated by cache)
- Members don't have OJS accounts (no reading history, preferences — likely fine for SEA)

### Critical blocker
> Does the Subscription SSO plugin allow non-members to still purchase content through OJS's normal paywall?

If yes → ship this. If no → Option B.

---

## Option B: Custom OJS Plugin + WP Plugin (CLEANEST — 2-4 weeks)

Build a small OJS plugin that exposes subscription CRUD as REST endpoints. WP plugin calls those endpoints on membership changes.

### How it works

```
Member signs up / renews in WP
  → WP plugin fires on membership status change
  → Calls OJS custom API: find-or-create user by email
  → Calls OJS custom API: create/renew subscription
  → OJS paywall sees valid subscription, grants access normally

Member lapses
  → WP plugin fires
  → Calls OJS custom API: expire/delete subscription
  → OJS paywall denies access, shows purchase options
```

### Components

1. **OJS plugin** (`sea-subscription-api`):
   - Registers REST endpoints under `/api/v1/plugin/sea/subscriptions`
   - Uses `IndividualSubscriptionDAO` internally for all CRUD
   - Authenticated via OJS API key
   - OJS 3.5+ required for clean plugin API ([pkp-lib #9434](https://github.com/pkp/pkp-lib/issues/9434))

2. **WP plugin** (`sea-ojs-sync`):
   - Settings page: OJS URL, API key, subscription type ID
   - Hooks into WP membership status changes
   - Calls OJS plugin endpoints
   - Bulk sync command (WP-CLI or admin button)
   - Error logging

### Pros
- Clean architecture, proper separation of concerns
- Subscriptions exist in OJS properly — visible in admin UI
- OJS paywall works completely normally
- Non-member purchases unaffected
- Uses OJS's own validated DAO layer

### Cons
- Two plugins to build and maintain
- Requires OJS 3.5+ (3.4 plugin API is much harder)
- Need OJS development + deployment capability
- OJS upgrades could break the plugin
- Members need separate OJS accounts (two logins)

---

## Option C: Direct DB Writes (FAST but fragile — 1-2 weeks)

WP plugin writes directly to OJS database tables. No OJS plugin needed.

### How it works

```
WP membership change
  → WP plugin connects to OJS database
  → INSERT/UPDATE/DELETE on `subscriptions` table
  → OJS reads subscriptions normally
```

### Pros
- No OJS plugin needed
- Works on any OJS version
- Fast to implement

### Cons
- Bypasses OJS validation, hooks, event system
- Schema changes between OJS versions silently break it
- No audit trail
- Requires same DB server or cross-database access
- Hard to debug

### When to use
Only if: same server, can't install OJS plugins, need it immediately. Treat as a stopgap.

---

## Option D: Janeway Migration (NUCLEAR — weeks to months)

Abandon OJS. Migrate to Janeway (Python/Django, proper OAuth, better APIs). Build a custom paywall since Janeway refuses to support them.

### When to consider
Only if all OJS options prove unworkable or more expensive than a platform migration.

---

## Recommendation

1. **First:** Answer the SSO coexistence question (test the plugin)
2. **If SSO works with purchases:** Ship Option A. Done in days.
3. **If not, and OJS is 3.5+:** Ship Option B. Done in 2-4 weeks.
4. **If OJS is 3.4 and can't upgrade:** Ship Option C as stopgap, plan Option B for after OJS upgrade.
5. **If everything fails:** Consider Option D, but this is a last resort.

---

## Blocking questions (need from SEA)

| Question | Why it matters |
|---|---|
| OJS version (3.4.x vs 3.5.x)? | Determines if Option B is viable |
| Can we install OJS plugins? | Required for Option A and B |
| Which WP membership plugin? | Determines WP hook points |
| Which membership tiers grant journal access? | Scope of the verification logic |
| Same server / network? | Determines if Option C is even possible |
| OJS admin access available? | Needed for any option |
