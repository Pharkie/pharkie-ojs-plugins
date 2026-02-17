# TODO — SEA WP ↔ OJS Integration

## Phase 0: Verify assumptions

### Answered by research (2026-02-16)

- [x] **Audit OJS REST API for subscription endpoints** — **NO subscription endpoints exist.** Not in 3.4, not in 3.5. The original REST API sync plan is not directly possible. See `docs/phase0-findings.md`.
- [x] **Confirm OJS API auth method** — Bearer token via `Authorization` header. Token per-user at Profile > API Key. Requires `api_key_secret` in `config.inc.php`. Apache+PHP-FPM needs `CGIPassAuth on`. Fallback: `?apiToken=` query param.
- [x] **Assess user creation via API** — Conflicting evidence. Swagger spec shows read-only user endpoints. **Must verify against actual OJS version.**
- [x] **Evaluate Subscription SSO plugin** — **Eliminated.** Source code audit confirmed it hijacks the OJS purchase flow. Non-members can't buy articles/issues while it's active. See `docs/phase0-sso-plugin-audit.md`.
- [x] **Evaluate OIDC/OpenID SSO** — **Eliminated.** Only solves login not access. OJS plugin has unresolved bugs, no production-ready release for 3.5, breaks multi-journal setups. Previous developer confirmed this independently.

### Answered by SEA (2026-02-16)

- [x] **Confirm OJS version** — **3.4.0-9.** Upgrade to 3.5 required (see Phase 0.5).
- [x] **Confirm WP membership plugin** — **Ultimate Member + WooCommerce + WooCommerce Subscriptions.** UM handles user registration/profiles/roles. WooCommerce Subscriptions handles the actual subscription billing. Membership = WP role assignment. See `docs/wp-integration.md` for hook details.
- [x] **Map membership tiers** — **All tiers grant journal access.** Simplifies the WP plugin: any active WCS subscription triggers an OJS subscription.
- [x] **Confirm hosting/network** — **Different servers.** WP plugin must make HTTP calls to OJS over the network. Need to confirm OJS is reachable from WP server (no firewall blocking).
- [x] **Get OJS admin access** — **Yes, have Site Administrator access.**
- [x] **Confirm: can we install OJS plugins?** — **Yes.**

---

## Decision: Push-sync (plugins on each side)

**Push-sync: Custom OJS plugin + WP plugin.** See `docs/plan.md` for the implementation plan, `docs/discovery.md` for the decision trail, `docs/review-findings.md` for the six-perspective review that shaped the final spec.

---

## Phase 0.5: Upgrade OJS to 3.5

OJS 3.5.0 was released June 2025 (LTS). Required for the custom plugin API. This must happen before any plugin development.

- [x] **Confirm current OJS version** — **OJS 3.4.0-9** (confirmed via meta generator tag on live site)
- [ ] **Confirm or create staging environment** — staging must exist before upgrade attempt
- [ ] **Write OJS 3.5 upgrade rollback runbook** — step-by-step: restore DB, restore files, verify 3.4 is back. Test the rollback on staging.
- [ ] **Agree go/no-go threshold with SEA** — if staging upgrade takes >X days to stabilise, escalate Janeway decision
- [ ] **Upgrade to 3.5 on staging**
  - [ ] Read [OJS 3.5 upgrade guide](https://docs.pkp.sfu.ca/dev/upgrade-guide/en/)
  - [ ] Read [OJS 3.5 release notes / breaking changes](https://github.com/pkp/pkp-lib/issues/9276)
  - [ ] Back up OJS database and files
  - [ ] Run upgrade on staging
  - [ ] **Post-upgrade acceptance criteria:**
    - [ ] Journal content intact and browsable
    - [ ] Existing user accounts survived
    - [ ] Paywall active on paywalled articles
    - [ ] Non-member purchase flow works: visit paywalled article → see correct prices (£3/£25/£18) → complete test purchase → access granted
    - [ ] OJS admin UI functional
- [ ] **Upgrade to 3.5 on production** (only after staging passes all acceptance criteria)

### Key 3.5 breaking changes to watch for
- Slim → Laravel routing (affects any existing custom code)
- `.inc.php` file suffixes no longer supported
- Non-namespaced plugins no longer supported
- Vue 2 → Vue 3 on frontend
- New `app_key` config required
- Locales now part of URLs

---

## Phase 0.75: Verify OJS API prerequisites

Must complete before writing any plugin code. These are the critical unknowns that gate the plugin design.

- [ ] **Test user creation API** — send `POST /api/v1/users` with a Bearer token on the real OJS 3.5 staging instance. Record result in `docs/ojs-api.md`. If it doesn't exist, the OJS plugin must implement full user creation via internal PHP classes.
- [ ] **Test Bearer token auth from WP server** — `curl` the OJS API with a Bearer token from the WP server's IP address. Confirm 200 response. If 401, enable `CGIPassAuth on` in `.htaccess` and retest.
- [x] **Locate OJS 3.5 password reset token class** — **Found.** `PKP\security\Validation::generatePasswordResetHash($userId)` generates HMAC-SHA256 tokens. `PKP\mail\mailables\PasswordResetRequested` sends the reset email. Default expiry is 2 hours (`security.reset_seconds` in `config.inc.php`) — must increase to 604800 (7 days) for bulk welcome emails. Documented in `docs/ojs-api.md`.
- [ ] **Confirm OJS email config** — check SPF, DKIM, DMARC records on the OJS mail domain. Check whether OJS uses a transactional email service or raw SMTP. If raw SMTP, set up a transactional relay (SES/Mailgun/Postmark) before bulk send.
- [ ] **Document OJS server specs** — RAM, CPU, PHP memory limit, PHP max execution time, web server type, shared or dedicated hosting.
- [x] **Confirm OJS journal structure** — **One journal.** *Existential Analysis* is a single journal in OJS. Sync creates subscriptions for one journal only.
- [ ] **Create dedicated OJS service account** — purpose-built account with minimum required role for sync operations. Generate API key. Do not use a human admin account.
- [x] **Discuss OJS self-registration with SEA** — **Keep enabled.** Non-members need to self-register to buy articles/issues via the paywall (£3/£25/£18). Disabling would break non-member purchases. Email mismatch risk is managed by the email change hook and daily reconciliation.
- [x] **Confirm WP email uniqueness** — **Yes.** WP enforces unique emails at DB level (`wp_users.user_email` has unique index) and in `wp_insert_user()`. UM does not relax this. Email changes require confirmation: WP core sends a verification link to the new address; UM's account form is stricter (logs user out until confirmed). Safe for email-as-key model.
- [ ] **Clarify UM ↔ WCS relationship** — how are Ultimate Member and WooCommerce Subscriptions connected on SEA's site? Is there a bridge plugin (e.g. "UM WooCommerce" extension)? Is UM purely the registration/profile layer while WCS is the sole authority on active subscriptions? Or does UM have its own membership state that could diverge from WCS? This determines whether WCS hooks alone are sufficient or whether UM has independent membership logic we need to account for.

---

## Phase 1: Build it

### OJS plugin (`sea-subscription-api`)

- [ ] Plugin skeleton: `plugins/generic/seaSubscriptionApi/`
- [ ] Register REST endpoints per the [endpoint spec in plan.md](docs/plan.md#ojs-endpoint-spec)
- [ ] **Idempotency**: all endpoints safe to call repeatedly with same payload
  - [ ] `find-or-create-user`: atomic lookup+insert in transaction, return existing user if email exists
  - [ ] `create/renew subscription`: upsert — check `getByUserIdForJournal()`, extend `dateEnd` if later, insert only if not found
- [ ] **Input validation**: email format (`filter_var`), integer casting on all IDs, max string lengths, HTTP 400 on invalid input. Use OJS `$request->getUserVar()` sanitisation, not raw `$_POST`.
- [ ] **IP allowlisting**: only accept requests from configured WP server IP
- [ ] **Authentication**: Bearer token, dedicated service account (not Site Admin)
- [ ] Endpoint: `GET /status` — connectivity + compatibility check
- [ ] Endpoint: `POST /users/find-or-create`
- [ ] Endpoint: `PUT /users/{userId}/email` — for WP email change propagation
- [ ] Endpoint: `DELETE /users/{userId}` — GDPR erasure
- [ ] Endpoint: `POST /subscriptions` — idempotent upsert
- [ ] Endpoint: `PUT /subscriptions/{id}/expire`
- [ ] Endpoint: `GET /subscriptions` — for reconciliation
- [ ] Endpoint: `POST /welcome-email` — sends "set your password" email, with dedup flag (skip if already sent)
- [ ] Configure password reset token expiry to 7 days (for bulk send)
- [ ] Custom login page message (permanent, WCAG 2.1 AA): "SEA member? First time here? Set your password"
- [ ] Paywall message for logged-in users with no subscription: "SEA member? Contact [support email]"
- [ ] OJS footer: "Your journal access is provided by your SEA membership. Manage at [WP URL]"
- [ ] No `eval()`, no raw SQL, no dynamic file includes, no shell execution
- [ ] Run PHPStan before production deploy
- [ ] Code review (second pair of eyes) before production deploy

### WP plugin (`sea-ojs-sync`)

- [ ] Plugin skeleton
- [ ] **Settings page**: OJS URL (HTTPS enforced, reject non-HTTPS), subscription type ID mapping, journal ID(s), "Test connection" button (calls `/status`)
- [ ] **API key**: read from `wp-config.php` constant `SEA_OJS_API_KEY`, not stored in database
- [ ] **Sync queue table**: custom DB table for async dispatch. Columns: `id`, `wp_user_id`, `email`, `action`, `payload`, `status`, `attempts`, `next_retry_at`, `created_at`, `completed_at`
- [ ] **Structured sync log table**: `id`, `wp_user_id`, `email`, `action` (activate/expire/create_user/email_change), `status` (success/fail), `ojs_response_code`, `ojs_response_body`, `attempt_count`, `created_at`
- [ ] **Admin log viewer**: list table with filters by status, date, email
- [ ] **WCS hooks** (primary): `status_active`, `status_expired`, `status_cancelled`, `status_on-hold` — log sync intent to queue, not inline HTTP
  - [ ] `status_active`: find-or-create user + create/renew subscription + trigger welcome email (for new users)
  - [ ] `status_expired` / `status_cancelled`: expire subscription
  - [ ] `status_on-hold`: expire subscription (decision: immediate, no grace period — document this)
- [ ] **Email change hook**: hook into WP `profile_update`, detect email change, call OJS update-user-email endpoint
- [ ] **Multiple subscriptions logic**: if member has multiple active WCS subscriptions, use latest `date_end` across all of them. If any active subscription exists, OJS access is active.
- [ ] **WP Cron queue processor**: process sync queue, retry failed items (3 attempts: 5min / 15min / 1hr), mark permanently failed after 3 attempts
- [ ] **Admin email alerts**: `wp_mail()` to admin on permanent failure; daily digest of failure count
- [ ] **Daily reconciliation**: WP Cron job — compare active WCS subscriptions vs successful sync log, retry any drift
- [ ] **Bulk sync command** (WP-CLI only):
  - [ ] Dry-run mode: report what it would do without making changes
  - [ ] Batched: process 50 users per batch, 500ms delay between API calls
  - [ ] Resume: per-user success/fail log persists across runs; skip already-synced users
  - [ ] Welcome email dedup: track "welcome email sent" flag, don't re-send on re-run
  - [ ] Welcome email batching: max 50/hour to protect email deliverability
  - [ ] Verification: after sync, compare active WCS count vs OJS subscriptions created, log any failures
  - [ ] Acceptance criteria: 100% of active WCS subscriptions result in an OJS subscription (zero failures target). Any failure individually logged with email and error reason.
- [ ] **WP member dashboard**: "Access Existential Analysis" link to OJS. Journal access status: active / not yet set up / contact us.
- [ ] **GDPR**: on WP account deletion, call OJS delete-user endpoint
- [ ] Code review before production deploy

### Pre-launch deliverables (not code)

- [ ] **Draft welcome email copy** — open with "Your SEA membership now includes access to Existential Analysis", single CTA ("Set your password"), show which email address was used, include OJS URL as plain text. No email-change instructions (put in FAQ). Send from recognisable SEA address.
- [ ] **Staff support runbook** — 1 page:
  1. How to check WP subscription status for a member
  2. How to check OJS user account and subscription status (OJS admin UI, Users page)
  3. How to manually trigger a single-member sync (WP-CLI)
  4. How to handle email mismatch (update WP email, re-trigger sync)
  5. How to read the WP sync error log
- [ ] **Member FAQ** — for common questions:
  - "I already have an OJS account with a different email"
  - "I didn't receive the setup email"
  - "I'm paying but can't access the journal"
  - "How do I update my email address?"
- [ ] **OJS upgrade rollback runbook** (if not done in Phase 0.5)
- [ ] **Plugin deployment runbook** — how to deploy/rollback each plugin

### Launch communication

- [ ] **Define launch sequence timing**: (1) complete bulk sync, verify counts → (2) send welcome emails → (3) wait for email delivery → (4) send member announcement
- [ ] **Member announcement** — via SEA's normal newsletter/email channel. "Your membership now includes access to Existential Analysis. Check your email for instructions on setting up your access."
- [ ] **Pre-launch communication** (optional) — heads up that the email is coming, so members don't mistake it for phishing

### End-to-end smoke test checklist

- [ ] Create test WCS subscription → WP hook fires → OJS user created → OJS subscription active
- [ ] Visit paywalled article as synced user → access granted
- [ ] Expire WCS subscription → OJS subscription expired → paywall denies access
- [ ] Visit paywalled article as non-member → purchase options displayed with correct prices
- [ ] Complete test non-member purchase → access granted
- [ ] Change WP email → OJS email updated
- [ ] Trigger welcome email → email received → set password link works
- [ ] Expired token → test error page → recovery path works
- [ ] "Test connection" button → success/failure correctly displayed
- [ ] OJS down during sync → event queued → OJS back up → event retried → subscription created
- [ ] Bulk sync dry-run → correct output → full run → verify counts match

---

## Phase 2: Robustness (after Phase 1 ships)

- [ ] Admin dashboard: per-member sync status with visual indicator
- [ ] Smarter nightly reconciliation: differential (modified in last 25hr first, then count comparison, full record-by-record only if counts differ)
- [ ] Rate limiting on OJS plugin endpoints (application-layer, beyond IP allowlist)
- [ ] 30-day follow-up email to members who haven't set their OJS password
- [ ] OJS `on-hold` grace period (if SEA decides they want one — currently immediate expiry)
- [ ] API key rotation support: OJS plugin accepts two keys simultaneously during rotation window
- [ ] Track OJS subscription ID in WP usermeta for more precise expire/update calls

## Phase 3: UX (nice to have)

- [ ] Onboarding email for new members who join after launch (beyond the welcome email)
- [ ] OJS notification on access suspension/restoration (WP plugin sends email on on-hold/reactivation)
- [ ] Password reset flow documentation for members
- [ ] OJS accessibility audit (WCAG 2.1 AA on core flows)

---

## Open questions

1. ~~Can the OJS REST API manage subscriptions?~~ **No.**
2. ~~Does the Subscription SSO plugin coexist with purchases?~~ **No.**
3. ~~Which WP membership plugin is in use?~~ **Ultimate Member + WooCommerce + WooCommerce Subscriptions.**
4. ~~What OJS version?~~ **3.4.0-9. Upgrade to 3.5 required.**
5. ~~What happens to existing OJS users who are also SEA members?~~ **Not an issue.** OJS is a fresh install — no existing member accounts.
6. Are there members who need OJS access outside the standard membership? (editorial board, reviewers) — only admin logins exist currently.
7. Does the OJS user creation API (POST /users) work on SEA's version? **Must verify on real instance — Phase 0.75.**
8. ~~Is *Existential Analysis* one OJS journal or two?~~ **One journal.**
9. ~~Should OJS self-registration be disabled?~~ **No.** Non-members need it for paywall purchases.

## Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| ~~OJS API lacks subscription endpoints~~ | — | — | **Confirmed.** Custom OJS plugin exposes them. |
| ~~SSO plugin conflicts with purchases~~ | — | — | **Confirmed.** Eliminated. |
| ~~OJS version is 3.4~~ | — | — | **Confirmed 3.4.0-9.** Upgrade to 3.5 is Phase 0.5. |
| User creation API doesn't exist | Medium | Medium | OJS plugin handles this too (Phase 0.75 verifies) |
| OJS 3.5 upgrade fails or causes data corruption | Medium | Critical | Staging first, rollback runbook, go/no-go threshold |
| Sync failures silently drop members | Medium | High | Async queue with retries, daily reconciliation, admin alerts |
| Members confused by two logins | High | Medium | Welcome email, permanent login prompt, cross-links between systems, support runbook |
| OJS upgrade breaks custom plugin | Medium | High | `/status` endpoint runs a compatibility check verifying every PHP class/method the plugin depends on (see plan.md endpoint spec). WP "Test connection" button calls it. Run after any OJS upgrade. Also: test upgrades in staging first, annual maintenance budget. |
| WP membership plugin changes | Low | Medium | Abstract hooks behind adapter |
| Bulk sync failures | Low | Medium | Dry-run mode, batched execution, per-user log, resume capability |
| Members don't set OJS password | High | Medium | Welcome email + permanent login prompt + 30-day follow-up (Phase 2) |
| Email change breaks sync | Medium | High | WP email change hook propagates to OJS |
| API key compromised | Low | Critical | `wp-config.php` constant, dedicated service account, IP allowlist, rotation runbook |
| Bulk welcome emails spam-filtered | Medium | Medium | Transactional email relay, SPF/DKIM/DMARC, batch 50/hour, warm-up send |
| Apache strips auth headers silently | Medium | High | Phase 0.75 verification from WP server IP |
| GDPR erasure request | Low | High | Delete-user endpoint on OJS plugin, triggered on WP account deletion |
