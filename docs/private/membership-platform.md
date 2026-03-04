# Future: Membership Platform Replacement

Last updated: 2026-02-24.

Not in scope for the OJS sync project, but the current WP membership stack is fragile and expensive. As we work through the sync integration, document what a proper replacement would need to do.

## Why replace it

The current stack is six plugins wired together (WooCommerce + WC Subscriptions + WC Memberships + Ultimate Member + UM-WooCommerce + UM-Notifications). Problems:

- **Complexity** — six plugins, three vendors, each with their own update cycle, hooks, database tables, and breaking changes
- **Cost** — WC Subscriptions £206/yr + WC Memberships £147/yr + UM-WooCommerce and UM-Notifications (individual prices not publicly listed; UM Extensions Pass is $249/yr for all 19 extensions). Total stack: ~£400-500/yr in plugin licences alone, before hosting.
- **Fragility** — role assignment chain spans three plugins (WCS → WCM → UM). A bug or breaking update in any one of them can silently break membership access. We hit this directly during the OJS sync build: UM custom roles aren't registered during early WP bootstrap, so `wp user import-csv` can't assign them. Production role assignment depends on the full plugin chain loading in the right order.
- **Indirection** — simple questions ("is this person a member?") require querying across multiple plugin tables and role systems. Our `is_active_member()` function has to check WCS subscription status, exclude the subscription being cancelled, and handle manual roles separately.
- **No real API** — WordPress has no native membership API. Integrating with external systems (like OJS) means writing custom plugins that hook into PHP internals, managing background job queues, writing direct SQL against plugin tables, and deploying code to a virtual server. Every integration is a bespoke engineering project.
- **Not infrastructure as code** — we've spent days wrapping WordPress in Bedrock, Composer, Docker, and scripted setup to make it reproducible. It's still fragile. A modern platform would have this out of the box.
- **WordPress limitations** — WP was not designed as a membership/CRM platform. Everything is bolted on. The underlying architecture (serialized PHP in `wp_usermeta`, no proper ORM, plugin load-order dependencies) creates problems that don't exist on modern platforms.

The main requirement for a replacement is **modern technical architecture**: a proper REST API, webhooks, structured data, so that integrations (like the OJS journal sync) can be built and maintained without hitting a wall of PHP internals, SQL workarounds, and six-plugin dependency chains.

## What the membership system actually does

Core requirements (what SEA members and admins actually need):

- Member registration with profile fields
- Recurring subscription billing (£35–60/year depending on tier)
- Automatic access grant/revoke based on payment status
- Member directory (with opt-in/opt-out for listing)
- Member profiles (public-facing)
- Admin ability to manually grant access (Executive Committee (Exco), life members)
- Email communications to members
- Integration with journal access (OJS or whatever replaces it)
- Annual and 5-year UKCP accreditation processes (forms, CPD hours etc)
- Events (SEA runs workshops and conferences)

Tier structure (current):

| Tier | Price | Variants |
|------|-------|----------|
| UK member | £50/yr | with/without directory listing |
| International member | £60/yr | with/without directory listing |
| Student member | £35/yr | with/without directory listing |
| Manual (Exco/life) | Free | admin-assigned |

## Requirements discovered during OJS sync build

Things that any replacement would need to handle, learned from building the push-sync integration:

- **API access is non-negotiable.** The OJS sync needs to read membership status programmatically. Any platform without an API would require fragile CSV-export workarounds instead of real-time sync.
- **Webhook or event support.** The sync is event-driven — fires on subscription status changes (active, expired, cancelled, on-hold). A replacement must emit events or webhooks when membership status changes.
- **Manual/honorary members.** Some members (Exco, life members) have access without a subscription. The system needs admin-assignable membership that bypasses the payment check.
- **Clean membership status.** WCS creates a new subscription object on each renewal, so a single member can have multiple subscription records (lapsed + current). Our sync code has to check if *any* subscription is active, not just the most recent one. A replacement should have one membership record per person with a clear status — this complexity should go away, not carry over.
- **Email as matching key.** The OJS sync uses email to match users across systems. A replacement must have stable, unique email addresses as user identifiers.
- **GDPR erasure.** When a member is deleted, their data must be cleaned up on OJS too. A replacement must support pre-deletion webhooks or events.
- **Bulk operations.** Initial sync pushes ~700 members to OJS at once. The platform needs to support bulk reads via API without punitive rate limits.
- **UK payment gateway.** Stripe (or any mainstream processor that handles GBP recurring payments).

## Platform comparison (~500 members)

Pricing originally verified 2026-02-21 from official websites. Vendor risk and architecture assessments added 2026-02-24.

### Shortlisted

All prices excl. VAT (20% applies to all — WildApricot and Outseta as reverse-charge on imported services, Beacon and CiviCRM hosting as UK VAT).

| Platform | Cost (~500 members, excl. VAT) | API | Verdict | Details |
|----------|-------------------------------|-----|---------|---------|
| **WildApricot** | ~$125/mo (~£1,200/yr, rising ~25% every 2yr) | Yes (REST, Swagger docs) | Turnkey, all features included, lowest setup effort. But private-equity-owned with aggressive price increases and serious support problems (Trustpilot: 1.6/5). | [Full assessment →](../membership-platform-wildapricot.md) |
| **CiviCRM** (self-hosted standalone) | ~£115/yr (DigitalOcean droplet; software is free) | Yes (REST APIv4, full CRUD) | Most capable and extensible. Best API. Cheapest ongoing cost. But highest setup complexity. | [Full assessment →](membership-platform-civicrm.md) |
| **Beacon CRM** | ~£78/mo (~£936/yr) | Yes (REST) | UK-native charity CRM with REST API. Membership features are add-ons — less proven for association management than WildApricot or CiviCRM. | [Full assessment →](../membership-platform-beacon.md) |
| **Outseta** | ~$67/mo (~£640/yr) + 1% transaction surcharge | Yes (REST + webhooks) | All-in-one SaaS like WildApricot but cheaper, bootstrapped, no PE risk. Good API. Missing member directory and events. | [Full assessment →](membership-platform-outseta.md) |

### Also evaluated (not shortlisted)

| Platform | Why excluded |
|----------|-------------|
| **Paid Memberships Pro** (free–£240/yr) | Open-source WP plugin with API on all tiers. Replaces 6 plugins with 1. But migration from UM/WCS is all cost, little benefit — you're still on WordPress with the same hosting, fragility, and infrastructure problems. |
| **MemberPress** (£280–500/yr) | API locked to Scale tier (£500+/yr, doubles after year 1). Same objection as PMPro — still WordPress. |
| **membermojo** (~£95/yr) | No API. Cheapest option but can't integrate with OJS. |
| **White Fuse** (~£1,000/yr) | No API. Good UK all-in-one otherwise. |
| **Join It** (~$99-149/mo) | Similar price to WildApricot/Beacon but weaker: 2% transaction surcharge on top of Stripe fees (others: none), API is read-only (no write endpoints, launched 2022), no native events (Eventbrite integration only), no GoCardless/BACS, weak email (recommends Mailchimp). Less for the same money. |
| **Zoho One** (~£530-1,050/yr) | Generic CRM, not a membership platform. You'd wire together Zoho CRM + Billing + Campaigns + Creator to replicate what WildApricot does out of the box — same multi-product fragility as the current WP stack, different vendor. No native member directory or events (Backstage excluded from bundle, £200+/mo separately). Nonprofit credits reduce licence cost for 5 years but consultant setup and ongoing admin complexity make realistic TCO higher than purpose-built alternatives. SEA doesn't use Zoho. |
| **Salesforce Nonprofit** (free + £10-30k setup) | Massive overkill. Requires dedicated admin expertise SEA doesn't have. |
| **Novi AMS** (~$829/mo) | Far too expensive for a 500-member society. |
| **MemberClicks** (~$375/mo) | Expensive, US-focused. Designed for larger associations. |
| **GrowthZone** (~$250-325/mo) | Expensive, US chamber-of-commerce focused. |
| **sheepCRM** (~£399/mo) | UK-native but too expensive for 500 members. |
| **Tendenci** (~$249/mo hosted) | Open-source AMS but immature API, small community, expensive hosting. |
| **Baserow** (free) | Database, not a membership platform. See "Custom-build option" below. |
| **Corteza** (free, self-hosted) | Open-source (Apache 2.0) low-code platform (Go backend, Vue.js frontend, Docker). Provides auth/RBAC, a data modelling layer with auto-generated REST API, workflow automation, and an admin UI — so it's a starting point for a custom build, not a from-scratch effort. But the hard parts of a membership platform (Stripe billing, subscription lifecycle, OJS sync, member directory, events) all still need building. What Corteza provides for free — auth, schema, API, admin panel — are the *easy* parts of a custom build; a developer comfortable with Stripe and OJS can stand those up in a day on mainstream tools. In exchange you'd take on: learning Corteza's module/namespace/workflow system, depending on a niche platform (16-person company, 2k GitHub stars, ~500 forum topics), heavier hosting (Corteza server + DB + Corredor automation runner vs. one app + one DB), and a narrower hiring pool if the maintainer moves on. Would add value for a complex internal CRM with many entities and workflows; for a 500-member society with 3 tiers and simple data, the scaffolding isn't worth the trade-off. |

### Custom-build option

Build a bespoke membership system on a modern stack (e.g. Node/TypeScript, Astro, Postgres, Stripe Billing for recurring payments). For ~500 members the core requirements are genuinely simple: a users table, a Stripe subscription per member, a webhook handler for payment events, a member directory page, and an API endpoint for the OJS sync. No off-the-shelf platform needed. Hosting cost would be minimal (~£115/yr on a DigitalOcean droplet, or less on a serverless platform).

**What you'd gain:**

- **Exactly what SEA needs, nothing more.** No fighting a platform's assumptions or working around features designed for a different use case.
- **Modern, clean architecture.** Proper schema, TypeScript, tested code, version-controlled, infrastructure as code from day one. No PHP, no legacy codebase, no mid-modernisation inconsistency.
- **Stripe does the hard part.** Recurring billing, payment retries, dunning, invoices, webhook events — all handled by Stripe. You don't build a payment system, you integrate with one.
- **OJS integration is trivial.** The sync is just another webhook handler or API call in the same codebase. No separate plugin, no cross-system hooks.
- **Total control.** No vendor lock-in, no price increases, no private equity acquisition risk, no declining support. Data stays on your own infrastructure.
- **Cheapest option.** Hosting only, no licence fees. Stripe's standard transaction fees (1.5% + 20p for UK cards) apply regardless of platform.

**Why this is probably too risky for SEA:**

- **Key-person dependency.** This is the fundamental problem. If the developer who builds it moves on, SEA is left with a bespoke system that nobody else can maintain. Off-the-shelf platforms (even flawed ones) have communities, documentation, and consultants. A custom build has one person's knowledge.
- **Ongoing maintenance.** Dependencies need updating, Stripe's API evolves, security patches need applying, bugs need fixing. Someone has to do this indefinitely. SEA is a volunteer-run society, not a tech company.
- **Scope creep.** The core requirements are simple, but the full list (CPD tracking, accreditation forms, event management, email communications, member directory with opt-in/out) adds up. What starts as "just a few tables and Stripe" becomes a real application.
- **10-15 year horizon.** SEA needs this to last. Bespoke systems built by one person rarely survive that long without that person. The technology choices that feel modern today (Node, Astro) may feel dated in 10 years, and the next developer may not want to maintain them.

**Verdict:** Technically the best solution. Practically the riskiest. The right choice only if SEA has a developer committed to maintaining it long-term — and even then, it's a bet on one person.

## What leaving WordPress fixes

The point of moving isn't to get a shinier UI. It's to get off a platform that fights you every time you try to extend it.

### Architecture comparison

| | WordPress (current) | WildApricot | CiviCRM (standalone) | Outseta |
|---|---|---|---|---|
| **"Is this person a member?"** | Query WCS subscription status across multiple DB tables, check role assignment chain, handle manual roles separately, write custom PHP | One API call: `GET /contacts/{id}` — returns membership level and status | One API call: `GET /api4/Membership/get` — returns status, type, dates | One API call: `GET /crm/accounts/{id}` — returns subscription plan and status |
| **Integrating with external systems** | Write a custom WP plugin, hook into PHP internals, manage Action Scheduler background jobs, deploy to a server, pray the six-plugin chain doesn't break | Call the REST API or receive a webhook. Standard HTTP. Any language. | REST APIv4 with full CRUD. Build a CiviCRM extension using PHP hooks, or poll the API externally. | REST API + webhooks. Standard HTTP. Any language. |
| **Membership status changes** | Hook into `woocommerce_subscription_status_*`, which only fires if WCS is active, loaded in the right order, and not broken by an update | Webhook fires on membership status change. Documented. Reliable. | `hook_civicrm_post` fires on membership changes. No native outbound webhooks, but CiviRules can trigger actions. | Webhook fires on subscription activity. SHA256 signed. 20 retries. |
| **Querying members in bulk** | `wp user list` + WP_User_Query + manual joins to subscription tables. Or raw SQL. | OData filtering, pagination, all fields | APIv4 with joins, filtering, pagination. API Explorer built in. | REST API with pagination and filtering. Rate limits undocumented. |
| **Dev environment** | Bedrock + Composer + Docker + custom Dockerfiles + scripted setup + SQL workarounds for role seeding. Days of work. | Sign up for a sandbox account. | Composer install. Simpler than WP+6 plugins, but still self-hosted. | Sign up for a trial account. 7-day free trial. |
| **Deploying changes** | SSH to a server, manage PHP versions, Apache config, database backups, plugin updates that might break each other | Nothing to deploy. It's SaaS. | Still a server to manage (PHP, MySQL, backups). But one application, not six plugins. | Nothing to deploy. It's SaaS. |
| **Data model** | Serialized PHP arrays in `wp_usermeta`. No ORM. No schema. Six plugins each with their own tables and conventions. | Structured JSON via API. Documented schema. | Structured schema with proper ORM. Documented API. | Structured JSON via API. One subscription per account. |

### What you stop doing (any replacement)

- **Stop debugging plugin interactions.** No more "WCS fires before WCM assigns the role" or "UM roles aren't registered during early bootstrap." One platform, one data model.
- **Stop paying for six plugin licences.** WCS (~£206/yr) + WCM (~£147/yr) + UM extensions. All replaced by one platform.
- **Stop maintaining Bedrock/Composer/Docker.** The infrastructure-as-code wrapper we built is impressive but shouldn't be necessary.

### What you gain (any off-the-shelf replacement)

- **A real API.** REST endpoints with documented schema. Any developer can integrate with it, in any language, without learning WordPress internals.
- **One source of truth.** Members, payments, events, email — all in one system with one data model. No more querying across plugin tables.
- **Portability.** The OJS sync becomes a service calling a REST API — not tied to WordPress. If you later change platform, the sync adapts by changing API calls, not rewriting a plugin.
- **Sustainability.** Any developer can pick this up. The API is documented, the platform is maintained by someone else, the data is accessible. No single person's WordPress plugin knowledge required. (This does not apply to a custom build — see "Custom-build option" above.)

### Trade-offs by platform

| | WildApricot | CiviCRM (standalone) | Outseta |
|---|---|---|---|
| **Infrastructure** | None. It's SaaS. | Still a server to manage, but one application instead of six plugins. UK hosting providers available. | None. It's SaaS. |
| **Codebase control** | Black box. If they don't support something, you can't fix it. | Open source (AGPL). Full control. Can extend with custom code. | Black box, same as WildApricot. |
| **Data sovereignty** | Data on WildApricot's servers (US/Canada). | Data on your own server (or UK hosting provider). | Data on Outseta's servers (US). |
| **Webhooks** | Native outbound webhooks on membership changes. | No native outbound webhooks. Hooks and CiviRules exist for building equivalent functionality. | Native outbound webhooks with SHA256 signing. |
| **Website** | Built-in website builder (limited). | No CMS included. | No CMS. Designed to embed into any website (Webflow, Squarespace, custom). |
| **SEA's existing WP site** | Move to WildApricot's website builder (limited), or rebuild with a static site generator (e.g. Astro). | No CMS included. Rebuild website with a static site generator (e.g. Astro). | Embed Outseta widgets into existing site or rebuild with static site generator. More flexible than WildApricot. |
| **Member directory** | Built-in. | Configurable (SearchKit + FormBuilder). | Not included. Must build externally via API. |
| **Events** | Built-in. | CiviEvent (mature). | Not included. Separate tool needed. |

## Recommendation

**Replace WordPress for membership management.** The current stack is not viable long-term. Six plugins from three vendors, paid licences, role assignment chains that break during bootstrap, no native API, test data that can't be seeded without SQL workarounds. This is technical debt, not a platform.

This is a decision for SEA, not a technical call. Five paths:

- **Stay on WordPress** (~£400-500/yr in plugin licences plus hosting) — the current stack is working and the OJS sync is built. It's fragile but functional. Don't migrate to another WP plugin (PMPro, MemberPress) — same infrastructure, all migration cost, no architectural benefit.
- **Outseta** (~£890/yr effective cost incl. 1% surcharge, Start-up tier) — all-in-one SaaS like WildApricot but cheaper and bootstrapped. REST API with signed webhooks covers the OJS integration. No servers to manage. No PE risk, no aggressive price increases. **Gaps:** no member directory (would need custom build via API) and no event management (separate tool needed). Small team (7 people), young product (~6,000 customers), low review volume. The best WildApricot alternative if the missing features can be solved externally.
- **WildApricot** (~£1,200/yr, likely to increase) — all-in-one SaaS. Most features included (directory, events, email, website). Lowest setup effort, highest ongoing cost. REST API with native webhooks. No servers to manage. **Serious red flag:** Trustpilot 1.6/5 (154 reviews) — support quality has collapsed post-acquisition. Private-equity-owned with ~25% price increases every 2 years, vendor lock-in, US-hosted data, no SLA.
- **CiviCRM standalone** (~£115/yr hosting; software free) — open-source, self-hosted on a DigitalOcean droplet. Best API and most extensible. Genuinely better architecture than WordPress (proper schema, Symfony DI, APIv4). No vendor lock-in, data ownership. Trade-off: small community (7-person core team, key-person dependency risk), financial health concerns, mid-modernisation codebase, needs ongoing technical maintenance.
- **Custom build** (~£115/yr hosting) — bespoke system on a modern stack (Node/TypeScript, Astro, Postgres, Stripe Billing). Technically the cleanest solution: exactly what SEA needs, no platform compromises, cheapest to run. But the key-person dependency is too high — if the developer moves on, SEA is left with a bespoke system nobody else can maintain. Only viable if someone is committed to maintaining it long-term.

Beacon CRM (~£936/yr excl. VAT) is also shortlisted but is the weakest of the shortlisted options — less proven for association management, with membership and events as paid add-ons rather than core features.
