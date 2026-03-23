# Discovery: What Was Investigated and Why

Last updated: 2026-03-08

> **What is this?** Before building anything, several approaches were evaluated for syncing membership data between WordPress and OJS. This document records what was tried, what was found, and why most options were eliminated. The winning approach — **push-sync** — is what this project implements.

This document records the research phase: what approaches were evaluated, what was found, and why options were eliminated. For the forward-looking implementation plan, see [`plan.md`](./private/plan.md).

---

## Approaches evaluated

| Name | What it is | Status |
|---|---|---|
| **OIDC SSO** | OpenID Connect single sign-on (existing OJS plugin) | Eliminated |
| **Custom OAuth** | Custom signed-redirect flow, WP as OAuth provider | Comparable alternative (not pursued) |
| **Pull-verify** | OJS asks WP "is this person a member?" at access time (Subscription SSO plugin) | Eliminated |
| **Push-sync** | WP pushes subscription changes to OJS via plugins on each side | **Chosen** |
| **Push-sync (direct DB)** | Same as Push-sync but writes to OJS database directly instead of via plugin | Fallback |
| **XML user import** | Bulk-import WP members into OJS via built-in XML import | Eliminated |
| **Janeway migration** | Replace OJS with Janeway + custom paywall | Genuine backup |

---

## Step 1: OIDC SSO (eliminated)

The OJS OIDC client plugin has multiple unresolved bugs (mid-teens), reports of fatal errors on multi-journal setups, and no production-ready release for OJS 3.5 months after 3.5's release. Even if it worked, the maintenance burden would be huge — potentially breaking OJS login entirely after every major update.

Additionally, OIDC only solves authentication (who is this person?), not authorization (do they have a subscription?). You'd still need a separate mechanism to sync subscriptions.

**Result: Eliminated.** The existing plugin is broken. But see "Custom OAuth" below for a reassessment of the underlying architecture.

## Step 1b: Custom OAuth (reassessed, not pursued)

**Reassessed 2026-03-08** after push-sync was built and working. The original OIDC elimination was based on the broken OJS plugin, but the concept of "members authenticate via WP, OJS provisions access on login" was never properly evaluated as a custom build.

A custom OAuth flow — WP signs a JWT with membership claims, OJS validates and provisions a subscription on login — has better member UX (no second password). But it's not simpler overall: it trades push-sync's explicit sync machinery for auth-flow complexity (JWT handling, browser redirects, session management, subscription expiry windows), and an OAuth consumer plugin hooks into OJS's authentication flow — equally vulnerable to OJS upgrade breakage as push-sync's REST endpoints.

The two approaches are roughly equivalent in complexity and effort. Push-sync's failure modes are visible and well-understood. OAuth introduces browser-based edge cases that push-sync avoids by being server-to-server.

See [`oauth-revisited.md`](./oauth-revisited.md) for the full reassessment.

## Step 2: Pull-verify (eliminated)

This was reconsidered because it looked simpler than originally understood — but then the actual source code was reviewed of the [Subscription SSO plugin](https://github.com/asmecher/subscriptionSSO).

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

Non-members who want to buy individual articles or issues get bounced to WordPress instead of seeing OJS's purchase page. The plugin assumes WordPress IS the only subscription system. There is no fallback to "show OJS purchase options if SSO also says no."

This breaks a hard requirement: non-members must be able to buy content through OJS's native purchase flow.

## Step 3: Native REST API (partially wrong)

The initial idea behind Push-sync was simple: WP calls OJS REST API to create subscriptions.

**Result: Partially wrong.** The OJS REST API has no subscription endpoints. Not in 3.4, not in 3.5, not on main. Confirmed via the [swagger spec](https://github.com/pkp/ojs/blob/main/docs/dev/swagger-source.json), [PKP forums](https://forum.pkp.sfu.ca/t/are-there-api-or-other-options-for-subscription-management-available-in-ojs-3-3/86106), and GitHub issues. PKP has said this is "not a development priority." The user creation API is also questionable — swagger spec shows read-only endpoints.

However: OJS has complete internal PHP classes for subscription CRUD (`IndividualSubscriptionDAO`). They just have no HTTP interface. **The fix is a small OJS plugin that exposes these classes as REST endpoints.** This keeps the spirit of Push-sync intact — the WP side is the same, the fix requires building the OJS receiving end as a custom plugin.

See `docs/private/phase0-findings.md` for full API research (private repo).

---

## Step 4: XML user import (eliminated)

Developer proposed using OJS's built-in Users XML Plugin (`Tools > Import/Export > Users XML Plugin`) as a stopgap — export WP members to XML, import into OJS periodically until the full sync is built.

**What it does:** Creates user accounts and assigns journal roles (e.g. Reader). Works on OJS 3.4, no upgrade needed. Skips existing users on re-import.

**What it doesn't do:** Create subscription records. The XML schema has no subscription-related fields. There is no separate subscription XML import in any OJS version — PKP has confirmed they have [no plans to build one](https://forum.pkp.sfu.ca/t/ojs3-bulk-import-subscriptions/62294).

**What about role-based access?** Some OJS roles (Journal Manager, Subscription Manager, etc.) do bypass the paywall without a subscription record, and the XML import can assign these roles. But they are editorial/admin roles — assigning them to ~500 members would give them inappropriate capabilities (e.g. managing other users' subscriptions), with no expiry control and no granularity. There is no "subscriber" role in OJS. The `Reader` role does not bypass the paywall.

The correct mechanism for member access is the `subscriptions` table. A working stopgap would still require a separate mechanism to create ~500 subscription records (direct SQL, or a PHP script using `IndividualSubscriptionDAO` — essentially writing a throwaway version of part of the OJS plugin). It would also have no mechanism for expiring lapsed members or handling email changes.

**Result: Eliminated.** The XML import handles the easy part (user accounts) but not the hard part (subscriptions, expiry, ongoing sync). See [`xml-import-evaluation.md`](./xml-import-evaluation.md) for the full write-up.

---

## Eliminated options summary

| Approach | Why eliminated |
|---|---|
| **OIDC SSO** (existing plugin) | OJS plugin has unresolved bugs, no 3.5 release, breaks multi-journal. A custom OAuth build avoids these plugin issues but introduces its own complexity — see [`oauth-revisited.md`](./oauth-revisited.md). |
| **Pull-verify** (Subscription SSO plugin) | Source code audit confirmed it hijacks OJS purchase flow. Non-members can't buy articles/issues. See `phase0-sso-plugin-audit.md`. |
| **Native REST API sync** | OJS has no subscription API endpoints in any version. Push-sync works around this with a custom OJS plugin. |
| **XML user import** | Creates user accounts only, not subscriptions. Roles that bypass paywall are editorial/admin — inappropriate for members. No expiry control. See [`xml-import-evaluation.md`](./xml-import-evaluation.md). |

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

Consider switching if the OJS 3.5 upgrade takes more than 2 weeks or the custom plugin scope grows significantly. See `private/janeway-paywall-investigation.md` for the full concrete plan.

**OJS quality concerns (2026-02-20):** During Docker dev environment setup, 6 separate OJS bugs were encountered — install crashes, broken CLI tooling, XML import/export that can't round-trip its own data, and missing APIs. Documented in [`ojs-issues-log.md`](./ojs-issues-log.md). This strengthens the case for keeping Janeway as a genuine backup.

## Citation classification: anystyle evaluated and dropped

**Status: Dropped.** Evaluated 2026-03-23.

[Anystyle](https://github.com/inukshuk/anystyle) is a Ruby CRF-based bibliographic reference parser. It was evaluated as a validation layer for the backfill citation classification pipeline (`split_citation_tiers.py`), which separates ~16,000 citation items into references (for Crossref DOI matching) and notes (display-only).

**What it did:** Parse each citation and check whether anystyle could extract both author and title fields. If not, demote from reference to note.

**Results:**
- 14,535 (93%) — anystyle found both author+title, confirming existing regex classification
- 127 — author-only (almost all legitimate references: Brooke, Macquarrie, Heidegger, Spinelli...)
- 652 — title-only (mix of legitimate references and prose endnotes — can't distinguish)
- 9 — neither (URLs/legislation)

**Why dropped:** Anystyle's CRF model was trained on modern, well-formatted references. This archive spans 30 years of inconsistent citation styles. Requiring both fields demoted ~780 legitimate references. Relaxing to "at least one field" caught only 9 items. Even as pure validation it added no signal — 93% agreement with the existing regex heuristics, and when it disagreed, anystyle was usually wrong (legitimate refs it couldn't parse). It also couldn't help with the actual problem (prose endnotes with embedded citations), since those contain parseable author+year patterns.

**Decision:** Regex-based `is_reference()` + `is_note()` heuristics are better suited to this archive's diversity. Ruby + anystyle removed from Dockerfile.

---

## Payment gateway: PayPal → Stripe

**Status: Stripe chosen.** PayPal eliminated 2026-03-22.

OJS ships with a PayPal payment plugin for non-member article/issue purchases. The PayPal sandbox was tested but found to be **completely broken for UK accounts** — couldn't create working sandbox credentials. Multiple support tickets were filed; PayPal support was unable to diagnose or resolve the issue.

Built a custom Stripe Checkout plugin instead (`plugins/stripe-payment/`). Redirect flow: buyer clicks purchase → OJS creates Stripe Checkout Session → redirect to Stripe → payment → redirect back → access granted. Webhook endpoint as backup confirmation path.

**Security:** Uses a restricted Stripe API key scoped to Checkout Sessions only (write), rather than an unrestricted secret key. No OAuth available for custom integrations (unlike WooCommerce's Stripe plugin which uses Stripe Connect).

PayPal has been removed as a payment option. Manual Payment remains as a fallback for edge cases.

---

<details>
<summary>Appendix: Test data seeding (dev environment only)</summary>

## Test data seeding: role assignment approach

**Decision (2026-02-21):** Use direct SQL (`wp eval-file`) for assigning UM/WCS roles to test users.

### Context

The `--with-sample-data` flag on `setup-wp.sh` imports ~1,400 anonymised test users for dev/staging environments. This is **not** the production sync path — production members already exist in WP and are pushed to OJS via `wp ojs-sync sync`.

### Problem

`wp user import-csv` validates roles early in bootstrap, before Ultimate Member registers its custom roles (`um_custom_role_*`). Importing users with UM roles produces "invalid role" warnings and assigns no role. Activating UM before import doesn't help — the validation runs before plugins fully load.

### Approaches evaluated

| Approach | Speed (1,400 users) | Correctness | Notes |
|---|---|---|---|
| `wp user import-csv` with original roles | — | Fails | UM roles not registered at validation time |
| `wp user set-role` in a loop | ~10 min | Correct | Fires all hooks per user — correct but extremely slow |
| Direct SQL via `wp db query` | ~2s | Correct | Shell quoting issues with serialized PHP values |
| Direct SQL via `wp eval-file` | ~2s | Correct | **Chosen** — PHP handles serialization natively |

### Solution

Two-step process:
1. **Import** users via `wp user import-csv` with role `subscriber` (always valid) — handles password hashing, user creation, and WP hooks correctly
2. **Reassign roles** via `wp eval-file scripts/apply-roles.php` — reads the CSV `original_role` column and updates `wp_capabilities` in `wp_usermeta` directly with the serialized capabilities value

The anonymise script (`scripts/anonymize-users.py`) maps all non-builtin roles to `subscriber` for import and preserves the original role in a separate `original_role` column.

### Why this is safe

- **Test data only.** Production members already exist in WP with correct roles assigned by UM and WCS during normal operation.
- **Direct SQL is appropriate** because we're seeding known-good test data, not modifying live user state. The roles are read from the original export.
- **UM roles don't have special capabilities** beyond what WP stores in `wp_capabilities`. The serialized format `a:1:{s:16:"um_custom_role_1";b:1;}` is all WP needs.
- **No hooks needed.** Test users don't need UM profile metadata, WCS subscription records, or other side effects that `wp user set-role` would trigger. Those are irrelevant for testing the OJS sync.

</details>
