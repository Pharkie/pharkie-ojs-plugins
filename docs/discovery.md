# Discovery: What We Investigated and Why

Last updated: 2026-02-18

This document records the research phase: what approaches were evaluated, what was found, and why options were eliminated. For the forward-looking implementation plan, see [`plan.md`](./plan.md).

---

## Approaches evaluated

| Name | What it is | Status |
|---|---|---|
| **OIDC SSO** | OpenID Connect single sign-on | Eliminated |
| **Pull-verify** | OJS asks WP "is this person a member?" at access time (Subscription SSO plugin) | Eliminated |
| **Push-sync** | WP pushes subscription changes to OJS via plugins on each side | **Chosen** |
| **Push-sync (direct DB)** | Same as Push-sync but writes to OJS database directly instead of via plugin | Fallback |
| **XML user import** | Bulk-import WP members into OJS via built-in XML import | Eliminated |
| **Janeway migration** | Replace OJS with Janeway + custom paywall | Genuine backup |

Previous developer's naming: Plan A = OIDC SSO, Plan B = Pull-verify, Plan C = Push-sync, Plan D = Janeway migration.

---

## Step 1: OIDC SSO (eliminated)

The previous developer evaluated this thoroughly. The OJS OIDC client plugin has multiple unresolved bugs (mid-teens), reports of fatal errors on multi-journal setups, and no production-ready release for OJS 3.5 months after 3.5's release. Even if it worked, the maintenance burden would be huge — potentially breaking OJS login entirely after every major update.

Additionally, OIDC only solves authentication (who is this person?), not authorization (do they have a subscription?). You'd still need a separate mechanism to sync subscriptions.

**Result: Eliminated.** Previous developer confirmed independently. We concur.

## Step 2: Pull-verify (eliminated)

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

## Step 3: Native REST API (partially wrong)

The initial idea behind Push-sync was simple: WP calls OJS REST API to create subscriptions. The previous developer believed this was feasible using "core API endpoints."

**Result: Partially wrong.** The OJS REST API has no subscription endpoints. Not in 3.4, not in 3.5, not on main. Confirmed via the [swagger spec](https://github.com/pkp/ojs/blob/main/docs/dev/swagger-source.json), [PKP forums](https://forum.pkp.sfu.ca/t/are-there-api-or-other-options-for-subscription-management-available-in-ojs-3-3/86106), and GitHub issues. PKP has said this is "not a development priority." The user creation API is also questionable — swagger spec shows read-only endpoints.

However: OJS has complete internal PHP classes for subscription CRUD (`IndividualSubscriptionDAO`). They just have no HTTP interface. **The fix is a small OJS plugin that exposes these classes as REST endpoints.** This keeps the spirit of Push-sync intact — the WP side is the same, we just need to build the OJS receiving end ourselves.

See `phase0-findings.md` for full API research.

---

## Step 4: XML user import (doesn't solve the problem)

Developer proposed using OJS's built-in Users XML Plugin (`Tools > Import/Export > Users XML Plugin`) as a stopgap — export WP members to XML, import into OJS periodically until the full sync is built.

**What it does:** Creates user accounts and assigns journal roles (e.g. Reader). Works on OJS 3.4, no upgrade needed. Skips existing users on re-import.

**What it doesn't do:** Create subscription records. The XML schema has no subscription-related fields. There is no separate subscription XML import in any OJS version — PKP has confirmed they have [no plans to build one](https://forum.pkp.sfu.ca/t/ojs3-bulk-import-subscriptions/62294).

**Why this matters:** The OJS paywall checks the `subscriptions` table, not user roles. A Reader account with no subscription record is still blocked from paywalled content. So XML import alone doesn't grant members access.

A working stopgap would still require a separate mechanism to create ~500 subscription records (direct SQL, or a PHP script using `IndividualSubscriptionDAO` — essentially writing a throwaway version of part of the OJS plugin). It would also have no mechanism for expiring lapsed members or handling email changes.

**Result: Doesn't solve the problem.** The XML import handles the easy part (user accounts) but not the hard part (subscriptions, expiry, ongoing sync). See [`xml-import-evaluation.md`](./xml-import-evaluation.md) for the full write-up.

---

## Eliminated options summary

| Approach | Why eliminated |
|---|---|
| **OIDC SSO** | Only solves login, not access. OJS plugin has unresolved bugs, no 3.5 release, breaks multi-journal. Previous developer confirmed. |
| **Pull-verify** (Subscription SSO plugin) | Source code audit confirmed it hijacks OJS purchase flow. Non-members can't buy articles/issues. See `phase0-sso-plugin-audit.md`. |
| **Native REST API sync** | OJS has no subscription API endpoints in any version. Push-sync works around this with a custom OJS plugin. |
| **XML user import** | OJS built-in import creates user accounts only, not subscriptions. Paywall checks subscriptions table, so doesn't grant access. See [`xml-import-evaluation.md`](./xml-import-evaluation.md). |

---

## Fallback: Push-sync (direct DB)

**Status: Fallback only.** Same push model, but the WP plugin writes directly to the OJS database instead of calling the OJS plugin's API.

- Bypasses OJS validation, hooks, and event system
- Schema changes between OJS versions break it silently
- No audit trail in OJS
- WP and OJS are on different servers, so needs cross-server database access

Only use if OJS plugins can't be installed or as a stopgap.

## Backup: Janeway migration

**Status: Genuine alternative, not just a last resort.** Evaluated 2026-02-16.

[Janeway](https://janeway.systems/) is a Python/Django journal platform by the Open Library of Humanities. It has a real REST API (DRF) and a clean plugin system, but an ideological opposition to paywalls — you'd fork and maintain paywall code permanently.

| Factor | Push-sync (OJS) | Janeway migration |
|---|---|---|
| **Total effort** | OJS 3.5 upgrade (days to weeks?) + plugins (1-2 sessions with Claude Code) + testing/deployment | Plugin code (1-2 sessions with Claude Code) + content migration (days to weeks) + testing/deployment |
| **Biggest risk** | OJS 3.5 upgrade (Slim→Laravel, Vue 2→3, breaking changes) | Building the payment system from scratch |
| **Paywall** | OJS native — already works | Must build (Django + Stripe) |
| **Non-member purchases** | OJS handles natively | Must build |
| **SSO/OIDC potential** | Broken in OJS (confirmed) | OIDC shipped v1.4.2, built on `mozilla-django-oidc`. But WP as OIDC provider needs a plugin with only 90 installs — untested. |
| **Two logins problem** | Yes (WP + OJS, no fix possible) | Theoretically solvable via OIDC but untested |
| **Content migration** | Not needed | Required — [tooling exists](https://github.com/openlibhums/janeway/wiki/Importing-from-OJS) but unverified at scale |
| **Tech stack** | PHP + PHP | PHP (WP) + Python/Django — two languages |
| **Staff retraining** | Minimal (OJS stays) | Required (new admin interface) |

Consider switching if the OJS 3.5 upgrade takes more than 2 weeks or the custom plugin scope grows significantly. See `janeway-paywall-investigation.md` for the full concrete plan.
