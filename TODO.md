# TODO — SEA WP ↔ OJS Integration

## Phase 0: Verify assumptions

### Answered by research (2026-02-16)

- [x] **Audit OJS REST API for subscription endpoints** — **NO subscription endpoints exist.** Not in 3.4, not in 3.5. The original REST API sync plan is not directly possible. See `docs/phase0-findings.md`.
- [x] **Confirm OJS API auth method** — Bearer token via `Authorization` header. Token per-user at Profile > API Key. Requires `api_key_secret` in `config.inc.php`. Apache+PHP-FPM needs `CGIPassAuth on`. Fallback: `?apiToken=` query param.
- [x] **Assess user creation via API** — Conflicting evidence. Swagger spec shows read-only user endpoints. **Must verify against actual OJS version.**
- [x] **Evaluate Subscription SSO plugin** — **Eliminated.** Source code audit confirmed it hijacks the OJS purchase flow. Non-members can't buy articles/issues while it's active. See `docs/phase0-sso-plugin-audit.md`.
- [x] **Evaluate OIDC/OpenID SSO** — **Eliminated.** Only solves login not access. OJS plugin has unresolved bugs, no production-ready release for 3.5, breaks multi-journal setups. Previous developer confirmed this independently.

### Still need from SEA (BLOCKING)

- [x] **Confirm OJS version** — **3.4.0-9.** Upgrade to 3.5 required (see Phase 0.5).
- [x] **Confirm WP membership plugin** — **Ultimate Member + WooCommerce + WooCommerce Subscriptions.** UM handles user registration/profiles/roles. WooCommerce Subscriptions handles the actual subscription billing. Membership = WP role assignment. See `docs/wp-integration.md` for hook details.
- [x] **Map membership tiers** — **All tiers grant journal access.** Simplifies the WP plugin: any active WCS subscription triggers an OJS subscription.
- [x] **Confirm hosting/network** — **Different servers.** WP plugin must make HTTP calls to OJS over the network. Need to confirm OJS is reachable from WP server (no firewall blocking).
- [x] **Get OJS admin access** — **Yes, have Site Administrator access.**
- [x] **Confirm: can we install OJS plugins?** — **Yes.**

---

## Decision: Push-sync (plugins on each side)

**Push-sync: Custom OJS plugin + WP plugin.** See `docs/architecture.md` for full reasoning and decision trail.

WP pushes subscription changes to OJS. A plugin on each side: WP plugin detects membership changes and pushes them; OJS plugin receives the calls and creates/updates/expires subscription records. No SSO, no OIDC, no password sync. The key addition over the previous developer's original Plan C: the OJS REST API doesn't have subscription endpoints, so we build a small OJS plugin to expose them.

**OJS 3.5+ is required.** The clean plugin API pattern was restored in 3.5 ([pkp-lib #9434](https://github.com/pkp/pkp-lib/issues/9434)). SEA is on 3.4.0-9, so upgrade first.

**Janeway migration is a genuine backup** if the OJS 3.5 upgrade proves too costly. See architecture doc.

---

## Phase 0.5: Upgrade OJS to 3.5 (if not already on 3.5+)

OJS 3.5.0 was released June 2025 (LTS). Required for the custom plugin API. This must happen before any plugin development.

- [x] **Confirm current OJS version** — **OJS 3.4.0-9** (confirmed via meta generator tag on live site)
- [ ] **Upgrade to 3.5** — required before plugin development
  - [ ] Read [OJS 3.5 upgrade guide](https://docs.pkp.sfu.ca/dev/upgrade-guide/en/)
  - [ ] Read [OJS 3.5 release notes / breaking changes](https://github.com/pkp/pkp-lib/issues/9276)
  - [ ] Back up OJS database and files
  - [ ] Set up staging environment for upgrade testing
  - [ ] Run upgrade on staging, verify journal content intact
  - [ ] Verify existing subscriptions and user accounts survived
  - [ ] Verify paywall and purchase flow still work
  - [ ] Run upgrade on production
- [ ] **If already on 3.5+:** skip this phase, proceed to Phase 1

### Key 3.5 breaking changes to watch for
- Slim → Laravel routing (affects any existing custom code)
- `.inc.php` file suffixes no longer supported
- Non-namespaced plugins no longer supported
- Vue 2 → Vue 3 on frontend
- New `app_key` config required
- Locales now part of URLs

---

## Phase 1: Build it (unblocked after OJS is on 3.5+ and SEA answers above)

### OJS plugin (`sea-subscription-api`)

- [ ] Plugin skeleton: `plugins/generic/seaSubscriptionApi/`
- [ ] Register REST endpoints for subscription CRUD using OJS 3.5 plugin API pattern
- [ ] Endpoint: find user by email
- [ ] Endpoint: create user (if native API can't do this — verify first)
- [ ] Endpoint: create/renew individual subscription
- [ ] Endpoint: expire/delete subscription
- [ ] Endpoint: list subscriptions (for bulk sync verification)
- [ ] Authentication via OJS API key
- [ ] Test against real OJS instance

### WP plugin (`sea-ojs-sync`)

- [ ] Plugin skeleton
- [ ] Settings page: OJS URL, API key, subscription type ID mapping
- [ ] Core sync function: given a WP user + membership status → call OJS endpoints
- [ ] Hook into WooCommerce Subscriptions events (primary integration point — see `docs/wp-integration.md`)
- [ ] Bulk sync command (WP-CLI or admin button) for initial population
- [ ] Error logging visible in WP admin
- [ ] Retry logic for failed API calls
- [ ] Test end-to-end

### OJS cosmetic changes

- [ ] "First time here from SEA? Set your password" message on login page
- [ ] "To subscribe, visit SEA" link on paywall page
- [ ] Launch email to existing members explaining OJS access

---

## Phase 2: Robustness (after Phase 1 ships)

- [ ] Scheduled reconciliation (nightly full sync to catch drift)
- [ ] Admin dashboard: sync status per member
- [ ] Email alerts on sync failures
- [ ] Rate limiting for bulk operations

## Phase 3: UX (nice to have)

- [ ] "Access journal" link on WP member dashboard
- [ ] Onboarding email for new members explaining OJS account
- [ ] Password reset flow documentation

---

## Open questions

1. ~~Can the OJS REST API manage subscriptions?~~ **No.**
2. ~~Does the Subscription SSO plugin coexist with purchases?~~ **No.**
3. ~~Which WP membership plugin is in use?~~ **Ultimate Member + WooCommerce + WooCommerce Subscriptions.**
4. ~~What OJS version?~~ **3.4.0-9. Upgrade to 3.5 required.**
5. What happens to existing OJS users who are also SEA members? (Migration/dedup plan)
6. Are there members who need OJS access outside the standard membership? (editorial board, reviewers)
7. Does the OJS user creation API (POST /users) work on SEA's version? (Verify on real instance)

## Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| ~~OJS API lacks subscription endpoints~~ | — | — | **Confirmed.** Custom OJS plugin exposes them. |
| ~~SSO plugin conflicts with purchases~~ | — | — | **Confirmed.** Eliminated. |
| ~~OJS version is 3.4~~ | — | — | **Confirmed 3.4.0-9.** Upgrade to 3.5 is Phase 0.5. |
| User creation API doesn't exist | Medium | Medium | Custom OJS plugin handles this too |
| Sync failures silently drop members | Medium | High | Logging, nightly reconciliation, admin alerts |
| Members confused by two logins | High | Medium | Clear onboarding, strategic "set password" prompts |
| OJS upgrade breaks custom plugin | Medium | High | Use DAO layer (stable), test upgrades in staging |
| WP membership plugin changes | Low | Medium | Abstract hooks behind adapter |
