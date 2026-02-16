# SEA WordPress ↔ OJS Integration

Integration between the Society for Existential Analysis (SEA) WordPress membership system and Open Journal Systems (OJS) hosting the journal *Existential Analysis*.

## Problem

- **OJS** hosts the journal with built-in paywall and access control
- **WordPress** is the SEA membership system (source of truth for members)
- SEA members should get journal access without buying separately
- Non-members must still be able to buy individual articles/issues via OJS

## Architecture Decision

**Push-sync:** custom OJS plugin + WP plugin. WP pushes subscription changes to OJS on membership events. A small OJS plugin exposes subscription CRUD as REST endpoints (using OJS's own internal classes). A WP plugin calls those endpoints when membership status changes.

This was reached after systematically eliminating alternatives — see [docs/architecture.md](./docs/architecture.md) for the full decision trail, including an honest evaluation of Janeway as a genuine backup.

### What was eliminated and why

| Approach | Why it's dead |
|---|---|
| Native REST API sync | API has no subscription endpoints. Not in 3.4, 3.5, or main. |
| OIDC SSO | Only solves login, not access. Plugin has unresolved bugs, no 3.5 release, breaks multi-journal. |
| Pull-verify (Subscription SSO plugin) | Source code audit confirmed it hijacks OJS purchase flow. Non-members can't buy content. |

### How it works

```
Member signs up / renews on WordPress
  → WP plugin calls OJS custom endpoint → subscription created
  → OJS paywall sees subscription → access granted

Member lapses
  → WP plugin calls OJS custom endpoint → subscription expired
  → OJS paywall denies access → shows purchase options

Non-member visits paywalled content
  → No subscription → normal OJS purchase flow (£3 / £25 / £18)
```

## Access Model

| User type | How they access journal content |
|---|---|
| SEA member (current) | Subscription created in OJS via sync; logs into OJS separately |
| Non-member, single article | Buys via OJS paywall (£3) |
| Non-member, current issue | Buys via OJS paywall (£25) |
| Non-member, back issue | Buys via OJS paywall (£18) |
| Lapsed member | Subscription expired in OJS; treated as non-member |

## Key Constraints

- **Over budget, time-sensitive** — ship the simplest thing that works
- **WP is source of truth** — OJS subscriptions are derived, not authoritative
- **OJS paywall must keep working** — non-member purchases are revenue
- **No OJS core modifications** — plugins only
- **No OIDC/OpenID SSO** — only solves login, not access; OJS plugin poorly maintained against 3.5 breaking changes; if it fails, nobody can log into OJS at all

## Live Sites

- **WordPress (SEA membership):** https://community.existentialanalysis.org.uk/
- **OJS (journal):** https://journal.existentialanalysis.org.uk/index.php/t1/index

## Tech Stack

- WordPress (PHP) — existing SEA site, using Ultimate Member + WooCommerce + WooCommerce Subscriptions
- OJS (PHP) — journal hosting (currently 3.4.0-9, upgrade to 3.5 required)
- Custom OJS plugin (`sea-subscription-api`) — exposes subscription CRUD endpoints
- Custom WP plugin (`sea-ojs-sync`) — hooks into WooCommerce Subscriptions events, syncs to OJS

## Repository Structure

```
WP OJS/
├── README.md                          # This file
├── CLAUDE.md                          # AI assistant instructions and gotchas
├── TODO.md                            # Task list and phased plan
├── docs/
│   ├── architecture.md                # Decision trail and architecture options
│   ├── ojs-api.md                     # OJS REST API reference, DB schema, PHP internals
│   ├── wp-integration.md              # WP membership stack, hooks, code patterns
│   ├── phase0-findings.md             # Raw research from API audit
│   ├── phase0-sso-plugin-audit.md     # Source code audit of Subscription SSO plugin
│   └── janeway-paywall-investigation.md  # Janeway backup: concrete Stripe paywall plan
└── plugins/                           # Plugin source (Phase 1)
    ├── sea-subscription-api/          # OJS plugin (TBD)
    └── sea-ojs-sync/                  # WP plugin (TBD)
```

## Quick Links

- [Architecture decision trail](./docs/architecture.md)
- [WP integration details](./docs/wp-integration.md)
- [Janeway backup plan](./docs/janeway-paywall-investigation.md)
- [TODO list](./TODO.md)
- [OJS REST API swagger spec](https://github.com/pkp/ojs/blob/main/docs/dev/swagger-source.json)
- [OJS subscription classes (GitHub)](https://github.com/pkp/ojs/tree/main/classes/subscription)
- [OJS 3.5 plugin API example](https://github.com/touhidurabir/apiExample)
