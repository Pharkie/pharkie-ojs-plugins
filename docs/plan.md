# Implementation Plan: Push-sync

Last updated: 2026-02-17

WP pushes subscription changes to OJS via a custom plugin on each side. For how we arrived at this decision, see [`discovery.md`](./discovery.md).

---

## How it works

```
Member signs up / renews on WordPress
  → WP plugin fires on WooCommerce Subscription status change
  → Calls OJS custom endpoint: find-or-create user by email
  → Calls OJS custom endpoint: create/renew subscription
  → Subscription record now exists in OJS database
  → Member visits OJS, logs in, hits paywalled content:
  → OJS paywall checks its own database → finds valid subscription → access granted

Member lapses / cancels
  → WP plugin fires
  → Calls OJS custom endpoint: expire subscription
  → OJS paywall denies access → shows normal purchase options

Non-member visits paywalled content
  → No subscription exists in OJS
  → OJS shows standard purchase page (£3 / £25 / £18)
  → Completely unaffected by the integration
```

---

## What gets built

### OJS plugin (`sea-subscription-api`)

Installed in `plugins/generic/seaSubscriptionApi/`:

- Registers REST endpoints for subscription CRUD
- Uses OJS's own `IndividualSubscriptionDAO` internally (classes already exist and are well-tested)
- Authenticated via OJS API key (Bearer token)
- No modifications to OJS core code — standard plugin, dropped into a folder
- Requires OJS 3.5+ for the clean plugin API pattern ([pkp-lib #9434](https://github.com/pkp/pkp-lib/issues/9434))
- "Set your password" welcome email: generates reset token, sends branded email on user creation
- Custom login page message: "SEA member? First time here? Set your password"

### WP plugin (`sea-ojs-sync`)

Installed like any WP plugin:

- Settings page: OJS URL, API key, subscription type ID mapping (WooCommerce Product → OJS Subscription Type)
- Hooks into **WooCommerce Subscriptions** lifecycle events (`status_active`, `status_expired`, `status_cancelled`, `status_on-hold`) as primary triggers
- Secondary hooks on Ultimate Member role changes as safety net
- Calls OJS plugin endpoints on each event
- Bulk sync command (WP-CLI or admin button) for initial population and drift correction
- Error logging visible in WP admin
- Retry logic for failed API calls
- See [`wp-integration.md`](./wp-integration.md) for full hook details and code patterns

---

## Key decisions

| Decision | Detail |
|---|---|
| **Email is the key** | Same email required on both systems. No mapping table. Members who want a different email on OJS update their WP email first. |
| **Bulk push creates accounts** | Don't wait for members to self-register. Push user accounts + subscriptions from WP upfront (~500 existing members at launch). |
| **All tiers grant access** | Any active WCS subscription → OJS access. Multiple WooCommerce products exist but all grant journal access. |
| **Password via welcome email** | Bulk sync triggers "set your password" email per member (not "forgot password"). Login page prompt as fallback. |
| **Content loaded gradually** | Launch with ~60 recent articles across 2 journals. Back issues loaded over time. |

---

## Facts established

| Question | Answer |
|---|---|
| OJS version | **3.4.0-9.** Upgrade to 3.5 required. |
| OJS admin access | **Yes.** Site Administrator level. |
| Can install OJS plugins | **Yes.** |
| WP membership plugin | **Ultimate Member + WooCommerce + WooCommerce Subscriptions.** |
| Membership tiers | **All tiers grant journal access.** |
| Hosting | **Different servers.** WP and OJS on separate hosts. HTTP calls over network. |
| OJS state | **Fresh install.** A handful of admin logins, ~60 test articles, no existing member accounts. |

---

## Launch sequence

1. **Upgrade OJS 3.4 → 3.5** — the biggest risk. Significant breaking changes (Slim→Laravel, Vue 2→3). Back up everything first. Test on staging.
2. **Build and deploy OJS plugin** (`sea-subscription-api`)
3. **Build and deploy WP plugin** (`sea-ojs-sync`)
4. **Bulk sync ~500 existing members** — creates OJS user accounts + subscriptions. Run with dry-run first, verify counts.
5. **"Set your password" emails** — bulk sync triggers a welcome email per member with a direct password-set link.
6. **OJS template changes** — "SEA member? Set your password" on login page, "subscribe via SEA" on paywall.

If the OJS 3.5 upgrade hits serious problems, fallback options are documented in [`discovery.md`](./discovery.md).

---

## Why this works

- OJS paywall operates completely normally — subscriptions are real OJS subscription records
- Non-member purchases are completely unaffected
- WP remains source of truth — OJS subscriptions are a downstream projection
- Uses OJS's own validated DAO layer — no raw SQL, no bypassed validation
- Both sides are standard plugins — no core code changes on either platform
- Subscriptions visible in OJS admin UI for troubleshooting

## Trade-offs

- **Two plugins to build and maintain.** Both can be written in 1-2 Claude Code sessions. Real time is in testing, deployment, and the OJS upgrade.
- **Requires OJS 3.5+.** The upgrade has significant breaking changes. This is the biggest risk in the whole plan.
- **Members need separate OJS accounts.** Two logins, matched by email. Clear onboarding via welcome email mitigates confusion.
- **OJS upgrades could break the OJS plugin.** Mitigated by using the DAO layer (more stable than raw SQL), but still needs testing on each OJS upgrade.
