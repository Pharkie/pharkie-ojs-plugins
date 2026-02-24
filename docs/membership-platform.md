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

| Platform | Cost (~500 members, excl. VAT) | API | Verdict |
|----------|-------------------------------|-----|---------|
| **WildApricot** | ~$125/mo (~£1,200/yr, rising ~25% every 2yr) | Yes (REST, Swagger docs) | Turnkey, all features included, lowest setup effort. But private-equity-owned with aggressive price increases and serious support problems (Trustpilot: 1.6/5). |
| **CiviCRM** (self-hosted standalone) | ~£115/yr (DigitalOcean droplet; software is free) | Yes (REST APIv4, full CRUD) | Most capable and extensible. Best API. Cheapest ongoing cost. But highest setup complexity. |
| **Beacon CRM** | ~£78/mo (~£936/yr) | Yes (REST) | UK-native charity CRM with REST API. Membership features are add-ons — less proven for association management than WildApricot or CiviCRM. |
| **Outseta** | ~$67/mo (~£640/yr) + 1% transaction surcharge | Yes (REST + webhooks) | All-in-one SaaS like WildApricot but cheaper, bootstrapped, no PE risk. Good API. Missing member directory and events. |

### Detailed notes

#### WildApricot (~£1,200/yr)

All-in-one SaaS: members, payments, events, email, website, API. Swagger-documented REST API. Tiers are by contact count only — all features included at every tier. Founded 2006 in Canada.

- **Pro:** All features included, large user base (15,000-32,000 organisations). Supports GBP billing via Stripe. No server to manage. Lowest setup effort. REST API with webhooks covers the OJS integration use case.
- **Con:** Platform subscription billed in USD (~$125/mo for 500 contacts, 2yr prepay). No single-operation full backup (must export each data type separately; supports CSV, XLS, and XML). North American company — data hosted in US/Canada (AWS). API rate limits are tight (40 requests/min for contact lists). No SLA or uptime guarantee.
- **OJS integration:** Rebuild sync against WildApricot API (webhooks + REST). Same push-sync pattern, different source.

**Vendor risk: private equity ownership and declining support.**

WildApricot was acquired by Personify in 2017 (backed by private equity firm Rubicon), then Personify was sold to another private equity firm (Pamlico Capital) in 2018. The original founder (Dmitry Buterin) left after the acquisition. Two private equity flips in two years.

- **Support quality has declined significantly post-acquisition.** This is the most consistent complaint across review sites. Trustpilot: 1.6/5 (154 reviews). Capterra: 4.4/5 (554 reviews). The gap is stark — Trustpilot captures more complaint-driven reviews, but 1.6/5 with 154 reviews is not noise. Users describe email/chat-only support with weeks-long response times.
- **Aggressive price increases.** ~25% increases every two years with 60 days notice. At this rate, prices roughly double every 6 years. Users report "zero product improvement despite multiple large price increases."
- **Team size is uncertain.** Reports range from 17 to 200 employees. Glassdoor reviews describe layoffs and loss of engineering staff post-acquisition. One review described "only 8-9 people in an office space designed for 150+."
- **No contractual data portability after termination.** Data is exportable while your account is active, but the Terms of Use contain no provisions for data export after account termination.
- **Another acquisition is likely.** Pamlico has held Personify since 2018 (7+ years, approaching typical private equity hold period). Another sale could mean price hikes, product sunset, or absorption into a larger platform.

**Bottom line:** WildApricot works well as a turnkey membership platform today. But the private equity ownership model is optimised for revenue extraction, not product investment. The risks are corporate/commercial (declining support, price instability, potential acquisition, no source code access) rather than CiviCRM's community/sustainability risks (small team, key-person dependency, financial stress). Neither is risk-free; they fail in different ways.

#### CiviCRM (~£115/yr self-hosted)

Open-source CRM (AGPL). Since v6.0 (March 2025) can run standalone — no WordPress/Drupal required. CiviMember for memberships, CiviEvent for events, CiviMail for email. Used by Amnesty International, EFF, Wikimedia Foundation, and over 9,000 organisations worldwide. Ranked #1 for cost-effectiveness in the 2025 UK Charity CRM Survey.

- **Pricing:** Software is free. Hosting ~£115/yr (DigitalOcean droplet, $12/mo — 2GB RAM for PHP + MySQL on one server). CiviCRM Spark (managed cloud) is $15-50/mo but too limited for SEA — can't install custom extensions needed for OJS integration and CPD tracking.
- **Pro:** Best API in this comparison (APIv4 — full CRUD on all entities, API Explorer built in). Stripe + GoCardless for GBP recurring payments. CiviMember handles tiered memberships, auto-renewal, manual/honorary members, status lifecycle. CiviEvent is mature for workshops/conferences. No vendor lock-in. Data ownership. Standalone mode eliminates WordPress entirely. Strong UK ecosystem. See "Architecture" section below for detailed comparison with WordPress.
- **Con:** Not turnkey — requires implementation and ongoing technical maintenance. No native outbound webhooks (membership lifecycle hooks exist for building custom extensions, and CiviRules can automate actions on triggers). Member directory requires configuration (SearchKit + FormBuilder) rather than being built-in. Admin interface is functional rather than polished. CPD/accreditation tracking has no production-ready extension — would need custom build using CiviCase or custom fields.
- **OJS integration:** Build a custom CiviCRM extension using `hook_civicrm_post` on Membership entity changes to push updates to OJS via HTTP. Architecturally identical to the current WP push-sync — replacing the WP side with CiviCRM. API and hooks to support this exist; integration code does not.
- **UK tech partners** available if needed (e.g. Circle Interactive, Third Sector Design).

**Architecture: genuinely better than WordPress, but not without risk.**

CiviCRM is still PHP/MySQL — but the problem with the current WP stack is WordPress's architecture on top of PHP/MySQL, not PHP/MySQL itself. CiviCRM has a properly normalised relational schema (dedicated tables for contacts, memberships, contributions — not serialised arrays in `wp_usermeta`), a Symfony DI container, a well-designed APIv4 with full CRUD, and a real event system. These are genuine architectural improvements over WordPress.

However:

- **Key-person dependency is high.** Two developers (Eileen McNaughton and Coleman Watts) account for 43% of all 72,704 commits and dominate current weekly activity. Core team is 7 people. Only 19 contributors active in the last month. If key contributors stopped, the project would be in trouble.
- **Financial health is weak.** Charitable income down ~30% YoY. Running a budget deficit. Health score 60/100. Subscription income is growing but not enough to offset the decline.
- **Mid-modernisation codebase.** Legacy `CRM_*` classes (no namespaces, 2000s-era PHP) coexist with modern `\Civi\*` code (namespaces, PSR-0, Symfony components). The transition has been going on for years and is not complete. You will encounter both styles.
- **Upgrades can break things.** Monthly releases with forward-only migrations (no rollback). Users report "after every update there are things that break" (Capterra: 3.9/5 stars). Better than WordPress's complete lack of migrations, but not modern.
- **Tiny community.** 718 GitHub stars, ~41 active contributors per year. If you hit a bug, the pool of people who can help is vastly smaller than WordPress's ecosystem. Documentation is adequate but uneven.

**Bottom line:** CiviCRM is architecturally better than WordPress in the areas that matter (data model, API, DI, events). But it's a 20-year-old application maintained by a small, financially stressed team. You'd be trading WordPress's problems (terrible data model, six-plugin fragility, no API) for CiviCRM's problems (small community, key-person dependency risk, mid-modernisation inconsistency, financial fragility). The architecture is better; the ecosystem is worse.

#### Beacon CRM (~£936/yr excl. VAT)

UK-built charity CRM with membership management. Designed for UK charities and nonprofits.

- **Pricing:** Starter plan £59.50/mo + Memberships add-on £11/mo + Events & ticketing £7.50/mo = **£78/mo (£936/yr)** on annual billing (10% discount). Excludes VAT. Up to 1,000 contacts. 25 custom fields included.
- **Pro:** UK-native, GBP billing. REST API. Stripe integration. Zero transaction surcharges on payments. Built for UK charities.
- **Con:** Leans more charity/fundraising than association management. Memberships and Events are paid add-ons, not included in base price. Less feature depth than WildApricot or CiviCRM for tiered membership levels and member directories.
- **OJS integration:** Possible via REST API. Would need investigation.

#### Outseta (~£640/yr + 1% surcharge)

All-in-one SaaS: billing, CRM, email marketing, authentication, help desk. All features included at every tier — no add-ons. Tiers are by contact count only. Founded 2016 in San Diego, USA. Bootstrapped (no VC, no private equity). ~6,000 customers. Team of 7 with equity stakes. Profitable.

- **Pricing:** Start-up plan $87/mo ($67/mo on annual billing) for up to 5,000 contacts. All features included. Billed in USD. **1% transaction surcharge** on top of Stripe's standard payment processing fees (1.5% + 20p for UK cards). The Founder tier ($37/mo, 1,000 contacts) is cheaper but has a 2% surcharge — and SEA would likely exceed 1,000 contacts once lapsed members, prospects, and admin accounts are counted. At $67/mo annual, that's ~$804/yr (~£640/yr). The 1% surcharge on ~£32,500/yr of membership revenue (~650 members x ~£50 avg) adds ~£325/yr, making the **effective cost ~£965/yr** — comparable to Beacon, cheaper than WildApricot. **Nuance on transaction fees:** Outseta handles subscription/recurring logic itself and only uses Stripe Payments to process each charge — so you don't pay Stripe Billing's additional 0.7% fee. Outseta claims most other membership platforms create Stripe Subscription objects under the hood, triggering that hidden 0.7%. If true, the real gap between Outseta's 1% surcharge and a platform with no surcharge but Stripe Billing is only ~0.3%. However, we haven't confirmed whether WildApricot, Beacon, or other shortlisted platforms actually use Stripe Billing — so take Outseta's comparison at face value but verify before relying on it.
- **Pro:** Genuinely all-in-one at a fraction of WildApricot's price. REST API with webhooks (POST callbacks on membership events, SHA256 signature verification, 20 retries at 20-minute intervals on failure). GBP billing supported (any Stripe-supported currency). Email marketing with drip sequences and CRM segmentation. Self-service member portal (profile management, subscription management, payment updates). Team/group memberships supported. Bootstrapped and profitable — no private equity risk, no aggressive price increases, founders have a track record (co-founder Dimitris Georgakopoulos previously co-founded Buildium, acquired by RealPage for $580M). Reviews are positive: Capterra 4.4/5, Product Hunt 4.9/5 — though review volume is low across all platforms.
- **Con:** **No member directory** — this is a real gap for SEA, which needs a public opt-in/opt-out directory. Outseta provides self-service profiles and a CRM, but no public-facing directory page. Building one would mean querying the API externally and rendering it yourself. **No event management** — SEA runs workshops and conferences; would need a separate tool (e.g. Eventbrite, Tito). **1% transaction surcharge** on the Start-up tier. Outseta claims this is partially offset because they don't use Stripe Billing (saving 0.7% that other platforms incur silently) — but we haven't verified which competitors actually use Stripe Billing (see pricing note above). **Honorary/manual members require a workaround** — create a $0 plan or apply a 100% discount code; not a first-class feature. **API documentation has gaps** — rate limits are not documented, webhook event types are not fully enumerated, Postman collection is the best reference. **Data export is limited** — CSV export from CRM, API-based extraction possible, but no "export everything" button. **Small team** (7 people) — same bus-factor concern as CiviCRM. **SaaS-startup roots** — originally built for SaaS companies, not associations, though they now actively market to associations and have 24+ association/club customers (BHRLA, NIFA, IADS, Mezcla Media Collective, UK Soul Choirs, etc.). **US-hosted** — data sovereignty same concern as WildApricot. **No Trustpilot presence** — too small for independent review volume.
- **OJS integration:** Rebuild sync against Outseta API. Webhooks fire on subscription events → push to OJS. REST API for bulk member reads. Same push-sync pattern as the current WP approach. The API is adequate for this; the real question is whether it handles edge cases (subscription status transitions, multiple subscriptions per person) cleanly. Outseta uses one subscription per account, which is cleaner than WCS's multiple-subscription model.

**Vendor risk: small but stable.**

Outseta is the anti-WildApricot in terms of ownership structure. Bootstrapped for 9 years, no external investors, all 7 team members hold equity. Revenue growing 55% YoY. The founders have significant SaaS experience (Buildium exit). There's no PE firm optimising for revenue extraction.

However:

- **Team of 7.** If Outseta were acquired, wound down, or lost key people, there's no open-source code to fork and no large community to fall back on. You'd need to migrate away, same as any SaaS.
- **Young product.** Founded 2016, ~6,000 customers. WildApricot has 15,000-32,000. CiviCRM has 9,000+. Outseta is smaller and less proven at scale.
- **Low review volume.** Capterra: 9 reviews. G2: minimal. Product Hunt: 17. The positive sentiment is real, but the sample size is small. Hard to know how it performs under stress (platform outages, billing disputes, complex migrations).

**Bottom line:** Outseta is a compelling WildApricot alternative — same all-in-one model, much cheaper, better ownership structure. The API and webhooks cover the OJS integration use case. The two real gaps are **no member directory** (SEA needs this) and **no event management** (SEA needs this). If those can be solved externally (custom directory page via API, separate events tool), Outseta deserves serious consideration. The 1% transaction surcharge is a cost to factor in but doesn't change the overall value proposition. The biggest risk is the small team and young product — you're betting on a 7-person company being around in 10 years.

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
