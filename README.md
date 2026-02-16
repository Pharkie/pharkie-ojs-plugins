# SEA WordPress ↔ OJS Integration

Integration layer between the Society for Existential Analysis (SEA) WordPress membership system and Open Journal Systems (OJS) hosting the journal *Existential Analysis*.

## Problem

- **OJS** hosts the journal with built-in paywall and access control
- **WordPress** is the SEA membership system (source of truth for members)
- SEA members should get journal access without buying separately
- Non-members should still be able to buy individual articles/issues via OJS
- SSO approaches have been evaluated and rejected as too fragile

## Architecture Decision

**Pending.** The original plan (WP → OJS REST API sync) was invalidated by Phase 0 research: the OJS REST API has no subscription endpoints.

Four options are documented in `docs/architecture.md`, ranked by delivery speed. The current recommendation is to test Option A (Subscription SSO plugin) first — see that doc for details.
- No real-time "click and you're in" from WP to OJS
- No modifications to OJS core or its paywall logic

## Access Model

| User type | How they access journal content |
|---|---|
| SEA member (current) | Subscription auto-created in OJS via sync; logs into OJS separately |
| Non-member, single article | Buys via OJS paywall (£3) |
| Non-member, current issue | Buys via OJS paywall (£25) |
| Non-member, back issue | Buys via OJS paywall (£18) |
| Lapsed member | Subscription expired/removed in OJS; treated as non-member |

## Key Constraints

- **Over budget, time-sensitive** — ship the simplest thing that works
- **WP is source of truth** — OJS subscriptions are derived, not authoritative
- **OJS paywall must keep working** — non-member purchases are revenue
- **Minimal custom code** — less to maintain, less to break
- **No OIDC/OpenID SSO** — evaluated and rejected as too fragile
- **Subscription SSO plugin under reconsideration** — simpler than originally understood, now the fastest option pending testing

## Tech Stack

- WordPress (PHP) — existing SEA site
- OJS (PHP) — journal hosting (version TBC)
- Custom plugin(s) — the deliverable (scope depends on architecture choice)

## Repository Structure

```
WP OJS/
├── README.md                   # This file
├── CLAUDE.md                   # AI assistant instructions and gotchas
├── TODO.md                     # Task list, blocking questions, phased plan
├── docs/
│   ├── architecture.md         # Architecture options (A/B/C/D) with recommendation
│   ├── ojs-api.md              # OJS REST API reference, DB schema, PHP internals
│   └── phase0-findings.md      # Raw research findings from API audit
└── plugin/                     # Plugin source (once architecture is decided)
```

## Quick Links

- [OJS REST API swagger spec](https://github.com/pkp/ojs/blob/main/docs/dev/swagger-source.json)
- [OJS subscription classes (GitHub)](https://github.com/pkp/ojs/tree/main/classes/subscription)
- [Subscription SSO plugin](https://github.com/asmecher/subscriptionSSO)
- [OJS 3.5 plugin API example](https://github.com/touhidurabir/apiExample)
- [TODO list](./TODO.md)
- [Architecture options](./docs/architecture.md)
