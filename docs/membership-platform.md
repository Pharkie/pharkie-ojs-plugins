# Future: Membership Platform Replacement

Last updated: 2026-02-21.

Not in scope for the OJS sync project, but the current WP membership stack is fragile and expensive. As we work through the sync integration, document what a proper replacement would need to do.

## Why replace it

The current stack is six plugins wired together (WooCommerce + WC Subscriptions + WC Memberships + Ultimate Member + UM-WooCommerce + UM-Notifications). Problems:

- **Complexity** — six plugins, three vendors, each with their own update cycle, hooks, database tables, and breaking changes
- **Cost** — WC Subscriptions alone is ~£200/year. WC Memberships, UM-WooCommerce, and UM-Notifications are additional paid licences.
- **Fragility** — role assignment chain spans three plugins (WCS → WCM → UM). A bug or breaking update in any one of them can silently break membership access. We hit this directly during the OJS sync build: UM custom roles aren't registered during early WP bootstrap, so `wp user import-csv` can't assign them. Production role assignment depends on the full plugin chain loading in the right order.
- **Indirection** — simple questions ("is this person a member?") require querying across multiple plugin tables and role systems. Our `is_active_member()` function has to check WCS subscription status, exclude the subscription being cancelled, and handle manual roles separately.
- **WordPress limitations** — WP was not designed as a membership/CRM platform. Everything is bolted on.
- **Test data pain** — seeding a dev environment requires a multi-step workaround: import users as `subscriber` (the only safe role before UM loads), then reassign roles via direct SQL. Normal WP-CLI role assignment fires hooks per user and takes ~10 minutes for 1,400 users.

## What the membership system actually does

Core requirements (what SEA members and admins actually need):

- Member registration with profile fields
- Recurring subscription billing (£35–60/year depending on tier)
- Automatic access grant/revoke based on payment status
- Member directory (with opt-in/opt-out for listing)
- Member profiles (public-facing)
- Admin ability to manually grant access (Exco, life members)
- Email communications to members
- Integration with journal access (OJS or whatever replaces it)
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

- **API access is non-negotiable.** The OJS sync needs to read membership status programmatically. Any platform without an API (membermojo, White Fuse) would require fragile CSV-export workarounds instead of real-time push-sync.
- **Webhook or hook support.** The sync is event-driven — fires on subscription status changes (active, expired, cancelled, on-hold). A replacement must emit events or webhooks when membership status changes.
- **Manual/honorary members.** Some members (Exco, life members) have access without a subscription. The system needs admin-assignable roles that bypass the payment check. Our sync handles this with a "manual roles" config.
- **Multi-subscription awareness.** Members can have multiple subscriptions (e.g. lapsed and renewed). "Is this person a member?" must check if *any* subscription is active, not just the most recent one.
- **Email as matching key.** The OJS sync uses email to match WP users to OJS users. A replacement platform must have stable, unique email addresses as user identifiers — or provide a better matching strategy.
- **GDPR erasure.** When a member is deleted, their data must be cleaned up on OJS too. The sync captures user data *before* WP deletes it (pre-delete hook), then sends the OJS delete request. A replacement must support pre-deletion hooks or events.
- **Bulk operations.** Initial sync pushes ~700 members to OJS at once. The membership platform needs to support bulk reads efficiently (not one API call per member with rate limits).
- **UK payment gateway.** Stripe (or any mainstream processor that handles GBP recurring payments).

## Platform comparison (~500 members)

Evaluated 2026-02-21. Pricing verified from official websites.

### Summary

| Platform | Type | Cost (~500 members) | API | Verdict |
|----------|------|---------------------|-----|---------|
| **WP + current stack** | Self-hosted WP | ~£400/yr (plugin licences) + hosting | WP hooks (PHP) | Current. Fragile but working. |
| **WildApricot** | SaaS | ~$125/mo (~£1,200/yr, 2yr prepay) | Yes (REST, well-documented) | Strongest SaaS option. GBP via Stripe. |
| **Join It** | SaaS | ~£79/mo (~£950/yr) | Yes (Total tier+) | Expensive, 2% transaction surcharge |
| **membermojo** | SaaS | ~£8/mo (~£95/yr) | No | Cheapest, but no API — dealbreaker |
| **White Fuse** | SaaS | ~£83/mo (~£1,000/yr) | No | No API — dealbreaker |
| **Salesforce Nonprofit** | SaaS/CRM | Free (10 licences) + £10-30k setup | Yes (comprehensive) | Massive overkill for a 500-member society |
| **MemberPress** | WP plugin | £280–500/yr | Yes (Scale tier only, £500+/yr) | API locked to expensive tier |
| **Paid Memberships Pro** | WP plugin | Free–£240/yr | Yes (all tiers, basic) | Best value WP option, but API is limited |
| **Baserow** | Self-hosted DB | Free (self-hosted) | Yes | Not a membership platform — you'd build everything |

### Detailed notes

#### WildApricot (~£1,200/yr)

All-in-one SaaS: members, payments, events, email, website, API. The best-documented API in this comparison (Swagger/OpenAPI, GitHub code samples). Tiers are by contact count only — all features included.

- **Pro:** Mature REST API, all features included, large user base. Supports GBP billing via Stripe.
- **Con:** Platform subscription billed in USD (~$125/mo). Export is CSV-only (no full backup). North American company, but currency support is flexible.
- **OJS integration:** Good. Webhook or API polling to read membership status and push to OJS.

#### Join It (~£950/yr)

Modern SaaS membership platform. API requires the Total plan ($99/mo). Adds a 2% transaction surcharge on top of Stripe fees.

- **Pro:** Clean UI, Stripe integration, API available
- **Con:** Expensive. 2% surcharge on payments (on top of Stripe fees). API locked to higher tier. Smaller company.
- **OJS integration:** Possible at Total tier, but expensive for what you get.

#### membermojo (~£95/yr)

UK-based, ultra-cheap. Handles basic membership, Stripe payments, email, directory. No frills.

- **Pro:** By far the cheapest. UK-native, GBP billing. Simple.
- **Con:** No API at all. Limited features. Small company.
- **OJS integration:** Not viable. No API means no automation.

#### White Fuse (~£1,000/yr)

UK-focused all-in-one: website, membership, email, events. Month-to-month, no contracts.

- **Pro:** Full-featured. UK-native. Data portable. GoCardless + Stripe.
- **Con:** No API. 1% transaction surcharge on Stripe. ~£1,000/yr.
- **OJS integration:** Not viable without an API. Would need CSV-export workaround.

#### Salesforce Nonprofit Cloud (free licences + £10-30k setup)

Enterprise CRM. 10 free licences via Power of Us Programme. Comprehensive API.

- **Pro:** Extremely powerful. Excellent API. Free licences for nonprofits.
- **Con:** Implementation costs £10-30k+. Requires dedicated admin expertise. Annual contracts. Payment processing and email require add-ons. Massive overkill.
- **OJS integration:** Technically excellent, but disproportionate to the problem.
- **Verdict:** Do not pursue. SEA does not have the budget or staff for Salesforce administration.

#### MemberPress (£280–500/yr)

WordPress membership plugin. Replaces WCS + WCM + UM in one plugin. API and webhooks on Scale tier only (£500+/yr).

- **Pro:** Single WP plugin replaces the current 6-plugin stack. Stripe on all tiers. Unlimited members.
- **Con:** API locked to Scale tier (£500+/yr, doubles after year 1). No native events. Email via third-party only. Migration from WCS+UM is non-trivial.
- **OJS integration:** Good on Scale plan (REST API + webhooks). Expensive to get there.

#### Paid Memberships Pro (free–£240/yr)

Open-source WordPress membership plugin. REST API on all tiers including free. Replaces WCS + WCM + UM.

- **Pro:** Free tier with Stripe + API (unique). Open-source core. Active community. WooCommerce compatible.
- **Con:** API is basic (7 endpoints). Directory/profiles require Plus tier (£240+/yr). Email via third-party. Same migration effort as MemberPress.
- **OJS integration:** Possible. API is basic but sufficient for push-sync. WP hooks (PHP) are well-documented.

#### Baserow (free self-hosted)

Open-source Airtable alternative. A database, not a membership platform. Excellent API.

- **Verdict:** Not viable. You'd build a bespoke membership system from scratch — payments, renewals, email, portal, directory, auth. Beyond the initial build cost, the real problem is sustainability: a custom system is all eggs in one basket. If the developer who built it moves on, SEA is left with an undocumented system that nobody else knows how to maintain. This needs to last 10-15 years. Off-the-shelf platforms have documentation, support teams, and communities. Custom builds have one person's head.

## Recommendation

**Replace WordPress.** The current stack is not viable long-term. We've spent days making it "infrastructure as code" — Bedrock, Composer, Docker, scripted setup — and it's still fragile. Six plugins from three vendors, paid licences, role assignment chains that break during bootstrap, no native API, test data that can't be seeded without SQL workarounds. This is technical debt, not a platform.

The OJS sync is being built against the current stack because it exists today, but the push-sync pattern (event → API call → OJS) would work the same against any platform with webhooks or an API. The sync plugin is the easy part. The membership platform is the hard part.

### Shortlist

Eliminating platforms with no API (membermojo, White Fuse — can't integrate with OJS), no UK fit (Join It — expensive, US-focused surcharges), and overkill (Salesforce, Baserow):

| | WildApricot | Paid Memberships Pro | MemberPress |
|---|---|---|---|
| **Cost** | ~£1,200/yr ($125/mo, 2yr prepay) | Free–£240/yr | £280–500/yr (doubles yr 2) |
| **Leaves WordPress** | Yes | No | No |
| **API** | Full REST, well-documented | Basic (7 endpoints), all tiers | Full REST, Scale tier only (£500+) |
| **GBP payments** | Yes (via Stripe) | Yes (via Stripe) | Yes (via Stripe) |
| **Migration effort** | High (new platform, data export, retrain staff) | Medium (new WP plugin, same database) | Medium (new WP plugin, same database) |
| **Ongoing maintenance** | Low (SaaS, they manage it) | Medium (still WordPress) | Medium (still WordPress) |
| **OJS integration** | Rebuild sync against WildApricot API | Minimal change (same WP hooks) | Minimal change (same WP hooks) |

### Assessment

1. **WildApricot** is the strongest option *if* SEA wants to leave WordPress entirely. Full-featured SaaS, mature API, no server maintenance. Supports GBP member billing via Stripe. The OJS sync would need rebuilding against the WildApricot API (webhooks + REST), but the pattern is identical. The bigger cost is staff retraining and content migration.

2. **Paid Memberships Pro** is the pragmatic middle ground. Stays on WordPress (lower migration risk), replaces the 6-plugin stack with one open-source plugin, API available on the free tier. The sync plugin would need minor changes (different hooks, different membership check). Still WordPress though — still fragile, still needs hosting and maintenance.

3. **MemberPress** is PMPro but more polished and more expensive. API locked to the £500+/yr tier. Not worth the premium over PMPro unless the extra features (courses, communities) matter to SEA.

### What to do next

This is a decision for SEA, not a technical call. The questions are:

- **Does SEA want to stop managing WordPress?** → WildApricot. ~£1,200/yr, lower maintenance, clean break.
- **Does SEA want to stay on WordPress but simplify?** → Paid Memberships Pro. Lower cost, moderate migration, still self-hosted.
- **What's the budget?** → PMPro is free. WildApricot is ~£1,200/yr. The current stack is ~£400/yr in licences plus hosting plus developer time that dwarfs both.

The OJS sync work is not wasted regardless of which path SEA chooses — the push-sync pattern ports to any platform with an API.
