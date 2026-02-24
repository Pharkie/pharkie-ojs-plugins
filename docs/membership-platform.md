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
- **Multi-subscription awareness.** Members can have multiple subscriptions (e.g. lapsed and renewed). "Is this person a member?" must check if *any* subscription is active, not just the most recent one.
- **Email as matching key.** The OJS sync uses email to match users across systems. A replacement must have stable, unique email addresses as user identifiers.
- **GDPR erasure.** When a member is deleted, their data must be cleaned up on OJS too. A replacement must support pre-deletion webhooks or events.
- **Bulk operations.** Initial sync pushes ~700 members to OJS at once. The platform needs to support bulk reads via API without punitive rate limits.
- **UK payment gateway.** Stripe (or any mainstream processor that handles GBP recurring payments).

## Platform comparison (~500 members)

Evaluated 2026-02-21. Pricing verified from official websites.

### Shortlisted

All prices excl. VAT (20% applies to all — WildApricot as reverse-charge on imported services, Beacon and CiviCRM hosting as UK VAT).

| Platform | Cost (~500 members, excl. VAT) | API | Verdict |
|----------|-------------------------------|-----|---------|
| **WildApricot** | ~$125/mo (~£1,200/yr) | Yes (REST, Swagger docs) | Strongest turnkey option. All features included. GBP via Stripe. Lowest setup effort. |
| **CiviCRM** (self-hosted standalone) | ~£800-3,500/yr (hosting + support) | Yes (REST APIv4, full CRUD) | Most capable and extensible. Best API. Cheapest ongoing cost. But highest setup complexity — needs professional implementation (~£1,000-5,000 upfront). |
| **Beacon CRM** | ~£78/mo (~£936/yr) | Yes (REST) | UK-native charity CRM with REST API. Membership features are add-ons — less proven for association management than WildApricot or CiviCRM. |

### Detailed notes

#### WildApricot (~£1,200/yr)

All-in-one SaaS: members, payments, events, email, website, API. The best-documented API in this comparison (Swagger/OpenAPI, GitHub code samples). Tiers are by contact count only — all features included at every tier.

- **Pro:** Mature REST API, all features included, large user base. Supports GBP billing via Stripe. No server to manage. Lowest setup effort.
- **Con:** Platform subscription billed in USD (~$125/mo for 500 contacts, 2yr prepay). Export is CSV-only (no full backup). North American company.
- **OJS integration:** Rebuild sync against WildApricot API (webhooks + REST). Same push-sync pattern, different source.

#### CiviCRM (~£800-3,500/yr self-hosted)

Open-source CRM (AGPL). Since v6.0 (March 2025) can run standalone — no WordPress/Drupal required. CiviMember for memberships, CiviEvent for events, CiviMail for email. Used by Amnesty International, EFF, Wikimedia Foundation, and over 9,000 organisations worldwide. Ranked #1 for cost-effectiveness in the 2025 UK Charity CRM Survey.

- **Pricing:** Software is free. Self-hosted costs: hosting ~£200-600/yr (UK providers: Xpdient, 2020Media, CiviHosting) + professional implementation ~£1,000-5,000 upfront + ongoing support ~£600-3,000/yr. CiviCRM Spark (managed cloud) is $15-50/mo but too limited for SEA — can't install custom extensions needed for OJS integration and CPD tracking.
- **Pro:** Best API in this comparison (APIv4 — full CRUD on all entities, API Explorer built in). Strong UK ecosystem (Circle Interactive, Third Sector Design, artfulrobot). Stripe + GoCardless for GBP recurring payments. CiviMember handles tiered memberships, auto-renewal, manual/honorary members, status lifecycle. CiviEvent is mature for workshops/conferences. No vendor lock-in. Data ownership. Standalone mode eliminates WordPress entirely.
- **Con:** Not turnkey — requires professional implementation and ongoing technical maintenance. No native outbound webhooks (membership lifecycle hooks exist for building custom extensions, and CiviRules can automate actions on triggers). Member directory requires configuration (SearchKit + FormBuilder) rather than being built-in. Admin interface is functional rather than polished. CPD/accreditation tracking has no production-ready extension — would need custom build using CiviCase or custom fields.
- **OJS integration:** Build a custom CiviCRM extension using `hook_civicrm_post` on Membership entity changes to push updates to OJS via HTTP. Architecturally identical to the current WP push-sync — replacing the WP side with CiviCRM. API and hooks to support this exist; integration code does not.
- **UK partners for implementation:** Circle Interactive (Bristol, Gold Partner), Third Sector Design (UK, working with CiviCRM since 2008).

#### Beacon CRM (~£936/yr excl. VAT)

UK-built charity CRM with membership management. Designed for UK charities and nonprofits.

- **Pricing:** Starter plan £59.50/mo + Memberships add-on £11/mo + Events & ticketing £7.50/mo = **£78/mo (£936/yr)** on annual billing (10% discount). Excludes VAT. Up to 1,000 contacts. 25 custom fields included.
- **Pro:** UK-native, GBP billing. REST API. Stripe integration. Zero transaction surcharges on payments. Built for UK charities.
- **Con:** Leans more charity/fundraising than association management. Memberships and Events are paid add-ons, not included in base price. Less feature depth than WildApricot or CiviCRM for tiered membership levels and member directories.
- **OJS integration:** Possible via REST API. Would need investigation.

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
| **Outseta** (~$37-47/mo) | Cheap with API, but no member directory. 2% transaction fee. SaaS/startup-oriented, not association-focused. |
| **Tendenci** (~$249/mo hosted) | Open-source AMS but immature API, small community, expensive hosting. |
| **Baserow** (free) | Database, not a membership platform. Would mean building everything custom — unsustainable. If the developer who built it moves on, SEA is left with a bespoke system nobody else can maintain. This needs to last 10-15 years. |

## What leaving WordPress fixes

The point of moving isn't to get a shinier UI. It's to get off a platform that fights you every time you try to extend it.

### Architecture comparison

| | WordPress (current) | WildApricot | CiviCRM (standalone) |
|---|---|---|---|
| **"Is this person a member?"** | Query WCS subscription status across multiple DB tables, check role assignment chain, handle manual roles separately, write custom PHP | One API call: `GET /contacts/{id}` — returns membership level and status | One API call: `GET /api4/Membership/get` — returns status, type, dates |
| **Integrating with external systems** | Write a custom WP plugin, hook into PHP internals, manage Action Scheduler background jobs, deploy to a server, pray the six-plugin chain doesn't break | Call the REST API or receive a webhook. Standard HTTP. Any language. | REST APIv4 with full CRUD. Build a CiviCRM extension using PHP hooks, or poll the API externally. |
| **Membership status changes** | Hook into `woocommerce_subscription_status_*`, which only fires if WCS is active, loaded in the right order, and not broken by an update | Webhook fires on membership status change. Documented. Reliable. | `hook_civicrm_post` fires on membership changes. No native outbound webhooks, but CiviRules can trigger actions. |
| **Querying members in bulk** | `wp user list` + WP_User_Query + manual joins to subscription tables. Or raw SQL. | OData filtering, pagination, all fields | APIv4 with joins, filtering, pagination. API Explorer built in. |
| **Dev environment** | Bedrock + Composer + Docker + custom Dockerfiles + scripted setup + SQL workarounds for role seeding. Days of work. | Sign up for a sandbox account. | Composer install. Simpler than WP+6 plugins, but still self-hosted. |
| **Deploying changes** | SSH to a server, manage PHP versions, Apache config, database backups, plugin updates that might break each other | Nothing to deploy. It's SaaS. | Still a server to manage (PHP, MySQL, backups). But one application, not six plugins. |
| **Data model** | Serialized PHP arrays in `wp_usermeta`. No ORM. No schema. Six plugins each with their own tables and conventions. | Structured JSON via API. Documented schema. | Structured schema with proper ORM. Documented API. |

### What you stop doing (any replacement)

- **Stop debugging plugin interactions.** No more "WCS fires before WCM assigns the role" or "UM roles aren't registered during early bootstrap." One platform, one data model.
- **Stop paying for six plugin licences.** WCS (~£206/yr) + WCM (~£147/yr) + UM extensions. All replaced by one platform.
- **Stop maintaining Bedrock/Composer/Docker.** The infrastructure-as-code wrapper we built is impressive but shouldn't be necessary.

### What you gain (any replacement)

- **A real API.** REST endpoints with documented schema. Any developer can integrate with it, in any language, without learning WordPress internals.
- **One source of truth.** Members, payments, events, email — all in one system with one data model. No more querying across plugin tables.
- **Portability.** The OJS sync becomes a service calling a REST API — not tied to WordPress. If you later change platform, the sync adapts by changing API calls, not rewriting a plugin.
- **Sustainability.** Any developer can pick this up. The API is documented, the data is accessible. No single person's WordPress plugin knowledge required.

### Trade-offs by platform

| | WildApricot | CiviCRM (standalone) |
|---|---|---|
| **Infrastructure** | None. It's SaaS. | Still a server to manage, but one application instead of six plugins. UK hosting providers available. |
| **Codebase control** | Black box. If they don't support something, you can't fix it. | Open source (AGPL). Full control. Can extend with custom code. |
| **Data sovereignty** | Data on WildApricot's servers (US/Canada). | Data on your own server (or UK hosting provider). |
| **Webhooks** | Native outbound webhooks on membership changes. | No native outbound webhooks. Hooks and CiviRules exist for building equivalent functionality. |
| **Website** | Built-in website builder (limited). | No CMS included. |
| **SEA's existing WP site** | Move to WildApricot's website builder, or keep a separate WP install without the membership plugins. | Keep a separate WP install without the membership plugins, or use any other website platform. |

## Recommendation

**Replace WordPress for membership management.** The current stack is not viable long-term. Six plugins from three vendors, paid licences, role assignment chains that break during bootstrap, no native API, test data that can't be seeded without SQL workarounds. This is technical debt, not a platform.

The OJS sync is being built against the current stack because it exists today, but the push-sync pattern (event → API call → OJS) would work the same against any platform with an API. The sync is the easy part. The membership platform is the hard part.

### Top candidates

1. **WildApricot** (~£1,200/yr) — all-in-one SaaS. Lowest setup effort. Mature REST API with native webhooks. No servers to manage. The strongest option if SEA wants a clean break with minimal technical overhead. Trade-off: vendor lock-in, US-hosted data, no custom code.

2. **CiviCRM standalone** (~£800-3,500/yr) — open-source, self-hosted. Best API and most extensible. Strongest membership and event features. No vendor lock-in, data ownership. Strong UK partner ecosystem. The strongest option if SEA wants maximum capability and is willing to invest in professional implementation upfront (~£1,000-5,000). Trade-off: not turnkey, needs ongoing technical maintenance.

3. **Beacon CRM** (~£936/yr excl. VAT) — UK-native SaaS. REST API, Stripe, zero transaction fees. Less proven for association management — membership and events are paid add-ons, not core features. Worth investigating further but the weakest of the three shortlisted options.

### What to do next

This is a decision for SEA, not a technical call. Three paths:

- **Stay on WordPress** — the current stack is working, the OJS sync is built, the cost is ~£400-500/yr in plugin licences plus hosting. It's fragile but functional. Don't migrate to another WP plugin (PMPro, MemberPress) — same infrastructure, all migration cost, no architectural benefit.
- **WildApricot** — highest ongoing cost but lowest setup effort. Infrastructure burden goes to zero. The OJS sync would need rebuilding against WildApricot's API.
- **CiviCRM standalone** — lowest ongoing cost but highest upfront investment. Best API and extensibility. Open source, data ownership, strong UK support ecosystem. The OJS sync would need rebuilding as a CiviCRM extension. Next step: contact Circle Interactive or Third Sector Design (UK-based CiviCRM partners) for a scoping conversation.

The OJS sync work is not wasted regardless of which path SEA chooses — the push-sync pattern ports to any platform with an API.
