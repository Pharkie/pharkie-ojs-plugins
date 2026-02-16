# Architecture Decision

Last updated: 2026-02-16

## Plan naming

| Name | What it is | Status |
|---|---|---|
| **OIDC SSO** | OpenID Connect single sign-on | Eliminated |
| **Pull-verify** | OJS asks WP "is this person a member?" at access time (Subscription SSO plugin) | Eliminated |
| **Push-sync** | WP pushes subscription changes to OJS via plugins on each side | **Recommended** |
| **Push-sync (direct DB)** | Same as Push-sync but writes to OJS database directly instead of via plugin | Fallback |
| **Janeway migration** | Replace OJS with Janeway + custom paywall | Genuine backup |

Previous developer's naming: Plan A = OIDC SSO, Plan B = Pull-verify, Plan C = Push-sync, Plan D = Janeway migration.

---

## Decision trail

This document records what we investigated, what we found, and why options were eliminated.

### Step 1: OIDC SSO (eliminated)

The previous developer evaluated this thoroughly. The OJS OIDC client plugin has multiple unresolved bugs (mid-teens), reports of fatal errors on multi-journal setups, and no production-ready release for OJS 3.5 months after 3.5's release. Even if it worked, the maintenance burden would be huge — potentially breaking OJS login entirely after every major update.

Additionally, OIDC only solves authentication (who is this person?), not authorization (do they have a subscription?). You'd still need a separate mechanism to sync subscriptions.

**Result: Eliminated.** Previous developer confirmed independently. We concur.

### Step 2: Pull-verify (eliminated)

The previous developer rejected this for good reasons (complexity, fights with paywall, janky user flow). We initially reconsidered it because it looked simpler than originally understood — but then we read the actual source code of the [Subscription SSO plugin](https://github.com/asmecher/subscriptionSSO).

**What it actually does (from [source code audit](./phase0-sso-plugin-audit.md)):**

1. User hits a paywalled article on OJS
2. OJS checks its own subscriptions first — if the user already has one, the plugin does nothing
3. If not subscribed AND no valid SSO session → plugin **redirects the user's browser** to WordPress
4. WordPress authenticates them, confirms membership, generates a token
5. WordPress redirects them back to OJS with that token in the URL
6. OJS makes a server-side cURL call to a WP verification endpoint with the token
7. If WP's response matches a configured regex → OJS stores a timestamp in the PHP session
8. User can browse paywalled content for N hours (configurable) without re-verification

**Result: Eliminated.** The plugin hijacks the purchase flow:

```
User hits paywalled content
  → OJS checks own subscriptions → not subscribed
  → Plugin checks SSO session → no valid session
  → Plugin IMMEDIATELY REDIRECTS to WordPress
  → OJS never shows its purchase/paywall page
```

Non-members who want to buy a £3 article get bounced to WordPress instead of seeing OJS's purchase page. The plugin assumes WordPress IS the only subscription system. There is no fallback to "show OJS purchase options if SSO also says no."

This breaks a hard requirement: non-members must be able to buy individual articles (£3), current issues (£25), and back issues (£18) through OJS.

### Step 3: Native REST API — WP pushes subscriptions via OJS API

The initial idea behind Push-sync was simple: WP calls OJS REST API to create subscriptions. The previous developer believed this was feasible using "core API endpoints."

**Result: Partially wrong.** The OJS REST API has no subscription endpoints. Not in 3.4, not in 3.5, not on main. Confirmed via the [swagger spec](https://github.com/pkp/ojs/blob/main/docs/dev/swagger-source.json), [PKP forums](https://forum.pkp.sfu.ca/t/are-there-api-or-other-options-for-subscription-management-available-in-ojs-3-3/86106), and GitHub issues. PKP has said this is "not a development priority." The user creation API is also questionable — swagger spec shows read-only endpoints.

However: OJS has complete internal PHP classes for subscription CRUD (`IndividualSubscriptionDAO`). They just have no HTTP interface. **The fix is a small OJS plugin that exposes these classes as REST endpoints.** This keeps the spirit of Push-sync intact — the WP side is the same, we just need to build the OJS receiving end ourselves.

See `phase0-findings.md` for full API research.

---

## Push-sync: Custom OJS Plugin + WP Plugin (RECOMMENDED)

**Status: Recommended.** This is the previous developer's Plan C, with one addition: a small OJS plugin to expose the subscription API that OJS doesn't ship natively.

### How it works (push model)

```
Member signs up / renews on WordPress
  → WP plugin fires on membership status change
  → Calls OJS custom endpoint: find-or-create user by email
  → Calls OJS custom endpoint: create/renew subscription
  → Subscription record now exists in OJS database
  → Later, when member visits OJS and hits paywalled content:
  → OJS paywall checks its own database → finds valid subscription → grants access

Member lapses / cancels
  → WP plugin fires
  → Calls OJS custom endpoint: expire/delete subscription
  → OJS paywall denies access → shows normal purchase options

Non-member visits paywalled content
  → No subscription exists in OJS
  → OJS shows standard purchase page (£3 / £25 / £18)
  → Completely unaffected by the integration
```

### What gets built

**OJS plugin** (`sea-subscription-api`) — installed in `plugins/generic/seaSubscriptionApi/`:
- Registers REST endpoints for subscription CRUD
- Uses OJS's own `IndividualSubscriptionDAO` internally (the classes already exist and are well-tested)
- Authenticated via OJS API key (Bearer token)
- No modifications to OJS core code — standard plugin, dropped into a folder
- Requires OJS 3.5+ for the clean plugin API pattern ([pkp-lib #9434](https://github.com/pkp/pkp-lib/issues/9434))

**WP plugin** (`sea-ojs-sync`) — installed like any WP plugin:
- Settings page: OJS URL, API key, subscription type ID mapping
- Hooks into WP membership plugin events (signup, renewal, lapse, cancellation)
- Calls OJS plugin endpoints on each event
- Bulk sync command (WP-CLI or admin button) for initial population and drift correction
- Error logging visible in WP admin
- Retry logic for failed API calls

### Why this works

- OJS paywall operates completely normally — subscriptions are real OJS subscription records
- Non-member purchases are completely unaffected
- WP remains source of truth — OJS subscriptions are a downstream projection
- Uses OJS's own validated DAO layer — no raw SQL, no bypassed validation
- Both sides are standard plugins — no core code changes on either platform
- Subscriptions visible in OJS admin UI for troubleshooting
- Scales to multiple journals easily

### Trade-offs

- **Two plugins to build and maintain.** More initial work than a simpler approach.
- **Requires OJS 3.5+.** SEA is currently on 3.4.0-9, so upgrade is required first. The 3.5 upgrade has significant breaking changes (Slim→Laravel, Vue 2→3, new config requirements). This is the biggest risk in the whole plan.
- **Members need separate OJS accounts.** Two logins. Matched by email address. Need clear onboarding to explain this.
- **OJS upgrades could break the OJS plugin.** Mitigated by using the DAO layer (more stable than raw SQL), but still needs testing on each OJS upgrade.
- **Effort:** OJS 3.5 upgrade (unknown, potentially 1-2 weeks) + 2-4 weeks for both plugins.

### Blocking questions

| Question | Status |
|---|---|
| OJS version? | **3.4.0-9.** Upgrade to 3.5 required. |
| Can we install OJS plugins? | Need to confirm |
| Which WP membership plugin? | Need from SEA |
| Which membership tiers grant journal access? | Need from SEA |
| OJS admin access available? | Need to confirm |

---

## Push-sync (direct DB): Fallback

**Status: Fallback only.** Use if OJS plugins can't be installed, or as a stopgap.

Same push model as Push-sync, but instead of the OJS plugin receiving API calls, the WP plugin writes directly to the OJS database.

### How it works

```
WP membership change
  → WP plugin connects to OJS database
  → Finds or creates user in `users` table
  → INSERT/UPDATE/DELETE on `subscriptions` table
  → OJS reads subscriptions normally — doesn't know they were created externally
```

### What gets built

**WP plugin only** — nothing on OJS side:
- Same membership hooks as Push-sync
- Direct database connection to OJS (PDO or wpdb with external connection)
- Writes to `users`, `subscriptions`, `subscription_types` tables
- Must match exact OJS schema expectations

### Why it's risky

- **Bypasses OJS validation, hooks, and event system.** Notifications, cache invalidation, etc. won't fire.
- **Schema changes between OJS versions break it silently.** An OJS upgrade could change the tables without warning.
- **No audit trail.** OJS won't log subscription creation events.
- **Requires database access.** WP server must connect to OJS database.
- **Hard to debug.** Neither system has the full picture when things go wrong.

### When to use

- Can't install OJS plugins for some reason
- Need something shipping immediately while Push-sync proper is being built
- WP and OJS share a database server

---

## Janeway migration: Genuine backup (not nuclear)

**Status: Genuine alternative, not just a last resort.** Evaluated 2026-02-16.

The initial assumption was that Janeway is a "nuclear option" — too expensive to be realistic. After proper evaluation, it's actually a different set of trade-offs with comparable total effort.

### What Janeway is

[Janeway](https://janeway.systems/) is a journal publishing platform built on Python/Django by the Open Library of Humanities. It has proper OAuth/OIDC support, a real REST API (Django REST Framework), and a clean plugin system. It's actively maintained (7,200+ commits, 48 releases, latest v1.7.5 June 2025).

### The paywall problem

Janeway has an **ideological opposition to paywalls**. This is not a missing feature — it is a hard policy. The maintainers will never accept paywall code upstream. However, the code is AGPL-3.0, so you can fork and add paywalls. You'd maintain the paywall code yourself permanently.

### What you'd actually build

A Django app (Janeway plugin) with:
- Subscription models (user, type, dates, status)
- Access control decorator wrapping the article download views
- Paywall UI via Janeway's template hook system
- Stripe integration for non-member purchases (£3/£25/£18)
- REST API endpoint for WP to push subscription changes (identical concept to Push-sync's WP plugin)

### Honest comparison: Push-sync vs Janeway migration

| Factor | Push-sync (OJS) | Janeway migration |
|---|---|---|
| **Total effort** | OJS 3.5 upgrade (1-2 weeks?) + 2-4 weeks plugins | 4-6 weeks (including content migration) |
| **Biggest risk** | OJS 3.5 upgrade (Slim→Laravel, Vue 2→3, breaking changes) | Building the payment system for non-members from scratch |
| **Paywall** | OJS native — already works | Must build (Django decorator + Stripe) |
| **Non-member purchases** | OJS handles natively | Must build |
| **API quality** | Poor (no subscription endpoints, workarounds needed) | Excellent (DRF, Swagger, proper permissions) |
| **SSO/OIDC potential** | Broken in OJS | Works natively in Janeway |
| **Two logins problem** | Yes (WP + OJS, no fix possible) | Solvable via native OIDC |
| **Content migration** | Not needed | Required — [tooling exists](https://github.com/openlibhums/janeway/wiki/Importing-from-OJS) but unverified for this scale |
| **Tech stack** | PHP + PHP | PHP (WP) + Python/Django (Janeway) — two languages |
| **Upstream support** | OJS subscription features deprioritized by PKP | Janeway team will never help with paywall code |
| **Ongoing maintenance** | OJS plugin may break on OJS upgrades | Paywall fork may conflict with Janeway upgrades |
| **Staff retraining** | Minimal (OJS stays) | Required (new admin interface) |
| **Future scalability** | Plugin pattern extends to new journals | Plugin pattern extends to new journals |

### When to switch to Janeway migration

Set a time-box on Push-sync. Consider Janeway seriously if:
- **The OJS 3.5 upgrade takes more than 2 weeks** including testing and paywall verification
- **OJS plugin installation is blocked** (hosting restrictions, admin access)
- **The OJS user creation API genuinely doesn't work** and the custom plugin scope grows
- **SEA wants single sign-on in future** (OIDC works in Janeway, broken in OJS)

### When NOT to switch

- If the OJS 3.5 upgrade goes smoothly
- If keeping OJS means less retraining and less migration risk
- If adding a second language (Python) to the stack is unacceptable
- If the 35+ years of back issues make content migration impractical

---

## Eliminated options

| Approach | Why eliminated |
|---|---|
| **OIDC SSO** | Only solves login, not access. OJS plugin has unresolved bugs, no 3.5 release, breaks multi-journal. Previous developer confirmed. |
| **Pull-verify** (Subscription SSO plugin) | Source code audit confirmed it hijacks OJS purchase flow. Non-members can't buy articles/issues. See `phase0-sso-plugin-audit.md`. |
| **Native REST API sync** | OJS has no subscription API endpoints in any version. Push-sync works around this with a custom OJS plugin. |

---

## Recommendation

**Ship Push-sync** (custom OJS plugin + WP plugin), preceded by an OJS 3.4 → 3.5 upgrade.

Sequence:
1. Upgrade OJS to 3.5
2. Build and deploy OJS plugin (`sea-subscription-api`)
3. Build and deploy WP plugin (`sea-ojs-sync`)
4. Bulk sync existing members
5. Add cosmetic OJS template changes ("set your password" prompts, "subscribe via SEA" links)

**If the OJS 3.5 upgrade hits serious problems**, re-evaluate Janeway migration as a genuine alternative rather than a last resort. The total effort is comparable — the risks are just different.
