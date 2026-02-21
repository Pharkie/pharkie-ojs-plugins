# SEA WordPress ↔ OJS Integration

Integration between the Society for Existential Analysis (SEA) WordPress membership system and Open Journal Systems (OJS) hosting the journal *Existential Analysis*.

## The two systems

**WordPress** is SEA's membership site. Members sign up, pay, and manage their accounts here. The membership stack is:

- **Ultimate Member (UM)** — handles user registration, profiles, and roles
- **WooCommerce Subscriptions (WCS)** — handles subscription billing and lifecycle (renewals, cancellations, payment failures)

UM and WCS work together: WCS processes the payment, then the member gets a WP role that UM manages. WCS is the authority on "is this subscription active?"

**OJS (Open Journal Systems)** hosts the journal *Existential Analysis* with a built-in paywall. Non-members can buy individual articles (£3), current issues (£25), or back issues (£18). It's a separate system on a separate server.

## Problem

- SEA members should get journal access automatically — without buying separately on OJS
- Non-members must still be able to buy individual articles/issues via OJS
- The two systems don't talk to each other

## Architecture Decision

**Push-sync:** custom OJS plugin + WP plugin. OJS has no native subscription API, so the OJS plugin exposes subscription CRUD as REST endpoints (using OJS's own internal classes). The WP plugin calls those endpoints in two modes:

1. **Initial bulk sync (~500 existing members):** WP-CLI command reads all active WooCommerce Subscriptions, creates OJS user accounts and subscription records for each member, then sends "set your password" welcome emails. This is how existing members get access at launch.
2. **Ongoing sync (after launch):** WP plugin hooks into WCS lifecycle events and pushes changes to OJS automatically via an async queue with retries and daily reconciliation.

See [docs/plan.md](./docs/plan.md) for the full implementation plan. See [docs/discovery.md](./docs/discovery.md) for the decision trail and why alternatives were eliminated.

### What was eliminated and why

| Approach | Why it's dead |
|---|---|
| Native REST API sync | API has no subscription endpoints. Not in 3.4, 3.5, or main. |
| OIDC SSO | Only solves login, not access. Plugin has unresolved bugs, no 3.5 release, breaks multi-journal. |
| Pull-verify (Subscription SSO plugin) | Source code audit confirmed it hijacks OJS purchase flow. Non-members can't buy content. See [audit](./docs/phase0-sso-plugin-audit.md). |
| XML user import | Creates user accounts only, not subscriptions. Roles that bypass paywall are editorial/admin — inappropriate for members, no expiry control. See [evaluation](./docs/xml-import-evaluation.md). |

**Backup plan:** [Janeway migration](./docs/janeway-paywall-investigation.md) is a genuine alternative if the OJS 3.5 upgrade proves too costly. See [discovery.md](./docs/discovery.md) for the comparison.

### How it works

```
Initial bulk sync (launch)
  → WP-CLI reads all active WCS subscriptions
  → For each member: calls OJS to find-or-create user + create subscription
  → Sends "set your password" welcome email
  → Member visits OJS, sets password → paywall sees subscription → access granted

Member signs up / renews on WordPress (ongoing)
  → WCS status changes to active → WP plugin queues sync
  → WP Cron calls OJS: find-or-create user + create subscription
  → OJS paywall sees subscription → access granted

Member lapses / cancels / payment fails
  → WCS status changes → WP plugin queues expire
  → WP Cron calls OJS: expire subscription
  → OJS paywall denies access → shows purchase options

Non-member visits paywalled content
  → No subscription → normal OJS purchase flow (£3 / £25 / £18)
```

## Access Model

| User type | How they access journal content |
|---|---|
| SEA member (current) | Subscription created in OJS via sync. Separate OJS login (matched by email). Welcome email sent with "set your password" link. |
| Non-member, single article | Buys via OJS paywall (£3) |
| Non-member, current issue | Buys via OJS paywall (£25) |
| Non-member, back issue | Buys via OJS paywall (£18) |
| Lapsed member | Subscription expired in OJS; treated as non-member |

## Key Constraints

- **Over budget, time-sensitive** — ship the simplest thing that works
- **WP is source of truth** — OJS subscriptions are derived, not authoritative
- **Email is the matching key** — same email required on both systems. No mapping table. Members who want a different email on OJS must update their WP email first.
- **OJS paywall must keep working** — non-member purchases are revenue
- **No OJS core modifications** — plugins only
- **Two separate logins** — members have a WP account and a separate OJS account, matched by email. No SSO. Mitigated by welcome email, permanent login page prompt, and cross-links between systems.
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
│   ├── plan.md                        # Implementation plan: what we're building
│   ├── discovery.md                   # Decision trail: what was tried, eliminated, and why
│   ├── review-findings.md            # Six-perspective plan review and how findings were resolved
│   ├── ojs-api.md                     # OJS REST API reference, DB schema, PHP internals
│   ├── wp-integration.md              # WP membership stack, hooks, code patterns
│   ├── phase0-findings.md             # Raw research from API audit
│   ├── phase0-sso-plugin-audit.md     # Source code audit of Subscription SSO plugin
│   ├── xml-import-evaluation.md       # Why OJS XML import doesn't work as a stopgap
│   ├── janeway-paywall-investigation.md  # Janeway backup: concrete Stripe paywall plan
│   ├── membership-platform.md        # Future: WP replacement evaluation + platform comparison
│   ├── ojs-issues-log.md             # OJS bugs encountered (evidence for Janeway eval)
│   ├── code-review-ojs-plugin.md     # OJS plugin code review findings
│   └── code-review-wp-plugin.md      # WP plugin code review findings
├── launch/                            # Pre-launch deliverables (drafts)
│   ├── welcome-email.md              # "Set your password" email copy
│   ├── support-runbook.md            # Staff guide for member queries
│   └── member-faq.md                 # Member-facing FAQ
├── plugins/                           # Plugin source
│   ├── sea-subscription-api/          # OJS plugin — subscription CRUD endpoints
│   └── sea-ojs-sync/                  # WP plugin — hooks into WCS, syncs to OJS
├── wordpress/                         # Bedrock WP project (Composer-managed)
│   ├── composer.json                  # Pins WP core + all plugins
│   └── paid-plugins/                  # Paid plugin ZIPs (gitignored, Composer stubs)
├── docker/                            # Docker config, Dockerfiles, OJS entrypoint
├── scripts/                           # Setup scripts (WP + OJS bootstrap)
└── docker-compose.yml                 # Dev environment (WP + OJS + 2x MariaDB)
```

## Quick Links

- [Implementation plan](./docs/plan.md)
- [Discovery / decision trail](./docs/discovery.md)
- [Plan review findings](./docs/review-findings.md)
- [WP integration details](./docs/wp-integration.md)
- [OJS API reference](./docs/ojs-api.md)
- [XML import evaluation](./docs/xml-import-evaluation.md)
- [Janeway backup plan](./docs/janeway-paywall-investigation.md)
- [Membership platform replacement](./docs/membership-platform.md)
- [OJS issues log](./docs/ojs-issues-log.md)
- [Docker quick reference](./docker/README.md)
- [TODO list](./TODO.md)
- [OJS REST API swagger spec](https://github.com/pkp/ojs/blob/main/docs/dev/swagger-source.json)
- [OJS subscription classes (GitHub)](https://github.com/pkp/ojs/tree/main/classes/subscription)
- [OJS 3.5 plugin API example](https://github.com/touhidurabir/apiExample)
