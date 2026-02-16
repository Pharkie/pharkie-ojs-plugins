# TODO — SEA WP ↔ OJS Integration

## Phase 0: Verify assumptions

### Answered by doc research (2026-02-16)

- [x] **Audit OJS REST API for subscription endpoints** — **CONFIRMED: NO subscription endpoints exist.** Not in 3.4, not in 3.5. Zero. The original plan of "WP calls OJS API to sync subscriptions" cannot work as described. See `docs/phase0-findings.md`.
- [x] **Confirm OJS API auth method** — Bearer token via `Authorization` header. Token generated per-user at Profile > API Key. Requires `api_key_secret` in `config.inc.php`. Apache+PHP-FPM needs `CGIPassAuth on` or header gets stripped. Fallback: `?apiToken=` query param.
- [x] **Assess user creation via API** — Conflicting evidence. Swagger spec for `main` branch shows read-only user endpoints (GET, no POST). Some docs suggest POST /users exists in older versions. **Must verify against actual OJS version.**

### Still need from SEA (BLOCKING)

- [ ] **Confirm OJS version** — 3.4.x vs 3.5.x. Determines which integration approach is viable. 3.5+ has clean plugin API extensibility; 3.4 is harder.
- [ ] **Confirm WP membership plugin** — which plugin? (WooCommerce Memberships, Paid Memberships Pro, MemberPress, custom?)
- [ ] **Map membership tiers** — which tiers exist? Which grant journal access? All of them?
- [ ] **Confirm hosting/network** — are WP and OJS on same server? Same database server? Firewall between them?
- [ ] **Get OJS admin access** — needed for plugin installation, API key setup, subscription type configuration
- [ ] **Confirm: can we install OJS plugins?** — needed for Option A (SSO) or Option B (custom API plugin)

### Technical verification (once we have OJS access)

- [ ] **Test Subscription SSO plugin coexistence** — does it interfere with OJS's normal purchase flow for non-members? **This is the key decision point.** If it coexists, Option A is fastest. If it doesn't, we need Option B.
- [ ] **Verify user API capabilities** on actual OJS version — can we POST to create users?
- [ ] **Identify existing OJS subscription types** — what's already configured?

---

## Decision: Which architecture?

**Pending Phase 0 answers above.** Current recommendation order:

### Option A: Subscription SSO Plugin (FASTEST — days not weeks)
- OJS [Subscription SSO plugin](https://github.com/asmecher/subscriptionSSO) delegates access checks to WP
- WP exposes a verification endpoint: "is this user a member?"
- No subscription records in OJS, no user sync needed
- **Blocker:** Must verify it coexists with OJS purchase flow for non-members
- **Effort:** ~3-5 days

### Option B: Custom OJS Plugin + WP Plugin (CLEANEST — weeks)
- Small OJS plugin exposes subscription CRUD as REST endpoints (using internal DAOs)
- WP plugin calls OJS user API + custom subscription endpoints
- Subscriptions exist in OJS properly, paywall works normally
- **Requires:** OJS 3.5+ for clean plugin API. OJS deployment capability.
- **Effort:** 2-4 weeks

### Option C: Direct DB Writes (FAST but fragile)
- WP writes directly to OJS `subscriptions` table
- No OJS plugin needed
- Bypasses all validation, breaks on schema changes
- **Only if:** same server, can't install OJS plugins, need it fast
- **Effort:** 1-2 weeks, ongoing maintenance debt

### Option D: Janeway (NUCLEAR — only if OJS is a dead end)
- Abandon OJS, migrate to Janeway, build custom paywall
- **Only if:** all OJS options prove unworkable
- **Effort:** weeks to months

---

## Phase 1: Implementation (unblocked after decision above)

### If Option A (Subscription SSO):

- [ ] Install and configure Subscription SSO plugin in OJS
- [ ] Build WP REST endpoint: `POST /wp-json/sea-ojs/v1/verify`
  - Receives user identifier (email or session param)
  - Checks WP membership status
  - Returns success/failure response matching plugin's expected regex
- [ ] Configure SSO plugin: verification URL, parameter name, regex, cache duration
- [ ] Test: member hits paywall → OJS calls WP → access granted
- [ ] Test: non-member hits paywall → OJS calls WP → access denied → normal purchase flow works
- [ ] Test: edge cases (expired member, WP down, slow response)
- [ ] Document the setup for SEA admins

### If Option B (Custom OJS Plugin + WP Plugin):

- [ ] **OJS plugin** (`sea-subscription-api`):
  - [ ] Register REST endpoints: `GET/POST/PUT/DELETE /api/v1/plugin/sea/subscriptions`
  - [ ] Use `IndividualSubscriptionDAO` internally for CRUD
  - [ ] Authenticate via OJS API key
  - [ ] Minimal — just subscription CRUD, nothing else
- [ ] **WP plugin** (`sea-ojs-sync`):
  - [ ] Settings page: OJS URL, API key, subscription type ID mapping
  - [ ] Find-or-create OJS user by email
  - [ ] Create/renew/expire subscription on membership status change
  - [ ] Hook into WP membership plugin events
  - [ ] Bulk sync (WP-CLI or admin button)
  - [ ] Error logging in WP admin
- [ ] Test end-to-end with real OJS instance

### If Option C (Direct DB):

- [ ] Map WP membership fields to OJS `subscriptions` table columns
- [ ] WP plugin writes directly to OJS DB on membership changes
- [ ] Verify DB connectivity between WP and OJS
- [ ] Add version-check safety (detect OJS schema version)
- [ ] Bulk sync for initial population

---

## Phase 2: Robustness (after Phase 1 ships)

- [ ] Scheduled reconciliation (nightly full sync to catch drift)
- [ ] Admin dashboard: sync status per member
- [ ] Email alerts on sync failures
- [ ] Retry logic for transient failures
- [ ] Rate limiting for bulk operations

## Phase 3: UX (nice to have)

- [ ] "Access journal" link on WP member dashboard
- [ ] Onboarding email explaining OJS access
- [ ] OJS login page guidance for members
- [ ] Password reset flow documentation (if Option B — separate accounts)

---

## Open questions

1. ~~Can the OJS REST API manage subscriptions?~~ **ANSWERED: No.**
2. Which WP membership plugin is in use? **(Need from SEA)**
3. What OJS version? **(Need from SEA)**
4. Does the Subscription SSO plugin coexist with OJS purchases? **(Need to test)**
5. What happens to existing OJS users who are also SEA members? (Migration plan)
6. Are there members who need OJS access outside the standard membership? (editorial board, reviewers)
7. Does SEA need members to have OJS accounts (reading history, preferences) or just access to content?

## Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| ~~OJS API lacks subscription endpoints~~ | ~~High~~ | ~~Critical~~ | **CONFIRMED.** See revised options above. |
| Subscription SSO conflicts with OJS purchases | Medium | High | Test before committing to Option A |
| OJS version is 3.4 (no clean plugin API) | Medium | Medium | Fall back to Option C or direct DB |
| User creation API doesn't exist on SEA's OJS | Medium | Medium | Only matters for Option B; verify on real instance |
| Sync failures silently drop members | Medium | High | Logging, nightly reconciliation, admin alerts |
| Members confused by two logins | High | Medium | Clear onboarding emails (only if Option B) |
| OJS upgrade breaks integration | Medium | High | Pin plugin to version, test upgrades in staging |
| WP membership plugin changes | Low | Medium | Abstract hooks behind adapter |
