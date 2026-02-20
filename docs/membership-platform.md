# Future: Membership Platform Replacement

Last updated: 2026-02-20.

Not in scope for the OJS sync project, but the current WP membership stack is fragile and expensive. As we work through the sync integration, document what a proper replacement would need to do.

## Why replace it

The current stack is five plugins wired together (WooCommerce + WC Subscriptions + WC Memberships + Ultimate Member + UM-WooCommerce). Problems:

- **Complexity** — five plugins, three vendors, each with their own update cycle, hooks, database tables, and breaking changes
- **Cost** — WC Subscriptions alone is ~£200/year. WC Memberships and UM-WooCommerce are additional paid licences.
- **Fragility** — role assignment chain spans three plugins (WCS → WCM → UM). A bug or breaking update in any one of them can silently break membership access.
- **Indirection** — simple questions ("is this person a member?") require querying across multiple plugin tables and role systems
- **WordPress limitations** — WP was not designed as a membership/CRM platform. Everything is bolted on.

## What the membership system actually does

Core requirements (what SEA members and admins actually need):

- [ ] Member registration with profile fields
- [ ] Recurring subscription billing (£35–60/year depending on tier)
- [ ] Automatic access grant/revoke based on payment status
- [ ] Member directory (with opt-in/opt-out for listing)
- [ ] Member profiles (public-facing)
- [ ] Admin ability to manually grant access (Exco, life members)
- [ ] Email communications to members
- [ ] Integration with journal access (OJS or whatever replaces it)

Tier structure (current):

| Tier | Price | Variants |
|------|-------|----------|
| UK member | £50/yr | with/without directory listing |
| International member | £60/yr | with/without directory listing |
| Student member | £35/yr | with/without directory listing |
| Manual (Exco/life) | Free | admin-assigned |

## Requirements to capture as we go

As we work through the OJS sync, note anything that reveals what a replacement needs:

- Edge cases in the current system
- Things that are hard because of the plugin stack
- What admins actually do day-to-day
- What members complain about
- What a simpler architecture would look like

## Candidates to evaluate (later)

_Not evaluated yet. Capture options here as they come up._

- Purpose-built membership platforms (MemberPress, Paid Memberships Pro, etc.)
- SaaS alternatives (Wild Apricot, MemberSpace, etc.)
- Custom build on a simpler framework
- Keep WP but consolidate plugins
