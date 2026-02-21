# TODO — SEA WP ↔ OJS Integration

## Decision

**Push-sync: Custom OJS plugin + WP plugin.** See `docs/plan.md` for the full spec, `docs/discovery.md` for the decision trail.

---

## Phase 0.5: Upgrade OJS to 3.5

Staging upgraded to 3.5.0.3 (2026-02-19). Live is still 3.4.0-9.

- **Staging:** OJS https://testojs.existentialanalysis.org.uk/ · WP https://testj.existentialanalysis.org.uk/
- [x] Verify post-upgrade acceptance criteria on staging (content, users, paywall, purchase flow, admin UI)
- [ ] Write OJS 3.5 upgrade rollback runbook
- [ ] Agree go/no-go threshold with SEA (cutoff for Janeway switch)
- [ ] Upgrade production to 3.5 (after staging passes)

## Phase 0.55: Docker dev environment — DONE

Local dev environment with WP + OJS + two MariaDB instances on Docker. Bedrock (Composer-managed WP), OJS config templating, fully scripted setup. Both plugins bind-mounted for live editing. Paid plugins (WCS 8.4.0, WCM 1.27.5) added via Composer path repositories (`wordpress/paid-plugins/`). See `docker/README.md` for quick reference, `docs/ojs-issues-log.md` for bugs encountered.

- [x] Docker Compose setup (base + override + staging configs)
- [x] Bedrock WP project (Composer pins WP core + plugins, config from `.env`)
- [x] Custom WP Dockerfile (`docker/wp/Dockerfile`) — PHP 8.2 + Composer + WP-CLI
- [x] Custom OJS Dockerfile (`docker/ojs/Dockerfile`) — extends PKP image + envsubst + mariadb client
- [x] OJS config templating (`docker/ojs/config.inc.php.tmpl` + `entrypoint.sh`)
- [x] OJS automated install (entrypoint auto-installs via curl, no wizard)
- [x] OJS reset script (`docker/reset-ojs.sh`)
- [x] WP setup script (`scripts/setup-wp.sh`) — WP-CLI: install, activate plugins, set options
- [x] OJS setup script (`scripts/setup-ojs.sh`) — REST API: create journal, SQL: subscription type + plugin
- [x] Import anonymized WP users (727 users from live export, emails → `@example.com`)
- [x] Import OJS content from live export (2 issues, 43 articles — required XML fix for pkp/pkp-lib#12276)
- [x] File pkp/containers#26 (CLI install broken for OJS 3.5)
- [x] Document OJS bugs encountered (`docs/ojs-issues-log.md`)
- [x] Add paid plugin ZIPs to Bedrock via Composer path repos: WooCommerce Subscriptions 8.4.0, WooCommerce Memberships 1.27.5 (`wordpress/paid-plugins/`, gitignored, `composer.json` stubs)

## Phase 0.6: Set up staging for testing

- [ ] **WP staging**: Install WooCommerce + WCS, create one subscription product (test mode), create 5-10 test users, verify WCS hooks fire
- [ ] **OJS staging**: Confirm test journal + paywalled article, create subscription type (record `type_id`), configure `api_key_secret`, create dedicated service account with Journal Manager role, test Bearer token auth from WP server

## Phase 0.75: Verify OJS API prerequisites

- [ ] **Set up transactional email relay** (Mailgun/SES/Postmark) on OJS — hard prerequisite for bulk sync. Check SPF/DKIM/DMARC. Welcome emails go out inline during bulk sync (~700 at once), not batched.
- [ ] Document OJS server specs (RAM, PHP limits, web server type)

---

## Phase 1: Build it

### OJS plugin (`sea-subscription-api`) — DONE

Code complete. 11 endpoints, reviewed and all critical/important issues fixed.

**What's built:** Plugin skeleton (SeaSubscriptionApiPlugin.php, SeaApiController.php, version.xml, locale). Endpoints: `GET /ping` (no auth), `GET /preflight`, `GET /users?email=`, `POST /users/find-or-create`, `PUT /users/{id}/email`, `DELETE /users/{id}`, `POST /subscriptions`, `PUT /subscriptions/{id}/expire`, `PUT /subscriptions/expire-by-user/{id}`, `GET /subscriptions`, `POST /welcome-email`. Auth: Bearer token + Journal Manager/Site Admin role + IP allowlist (REMOTE_ADDR). Full PII anonymisation on delete. Idempotent upserts. Atomic welcome email dedup. Password reset via AccessKeyManager.

**Remaining before deploy:**
- [x] Set `password_reset_timeout = 7` in config.inc.php
- [x] Custom login page message: "SEA member? First time here? Set your password"
- [x] Paywall message for logged-in users with no subscription: "SEA member? Contact [support email]"
- [x] OJS footer: "Your journal access is provided by your SEA membership. Manage at [WP URL]"
- [x] Run PHPStan (1 real fix: renamed `authorize()` → `checkAuth()` to avoid parent clash; rest are OJS autoloader false positives)
- [x] Code review — `docs/code-review-ojs-plugin.md` (2 critical, 6 important, 7 minor, 9 notes). All critical + important fixed (2026-02-21):
  - C1: Preflight field name `check` → `name` (matched WP plugin expectation)
  - C2: XSS in login hint — URL now escaped for HTML + JS context
  - I1: `getJournalId()` null-checks `getContext()`, throws RuntimeException
  - I3: Preflight now checks `AccessKeyManager::createKey()` (was checking unused `generatePasswordResetHash`)
  - I4: `findOrCreateUser` catch narrowed to `QueryException` for race condition
  - I6: GDPR delete now clears `access_keys`, `datePasswordResetRequested`, all `user_settings`

### WP plugin (`sea-ojs-sync`) — CODE COMPLETE

12 files (was 14 — removed custom queue class + queue admin page). PHP syntax verified. Internal consistency reviewed.

**What's built:** Plugin bootstrap, activator (DB table + cron scheduling), API client (10 OJS endpoint methods, Bearer auth, error classification), Action Scheduler integration (dedup, automatic retry), logger (paginated queries, cleanup), subscription resolver (multi-sub resolution, manual roles, active member query), sync processor (activate/expire/email_change/delete_user with correct API call sequences, admin alerts), WCS hooks + profile_update + deleted_user, cron handlers (daily reconciliation with bidirectional drift detection, daily digest), settings page (OJS URL, type mapping, manual roles, test connection AJAX, server info), admin log viewer (WP_List_Table with filters), WP-CLI commands (sync, reconcile, status, test-connection).

**Remaining before deploy:**
- [x] **Member dashboard**: "Access Existential Analysis" link, access status indicator (WooCommerce My Account)
- [ ] Configure settings: type mapping (WC Product IDs → OJS Type IDs), manual roles (`um_custom_role_7`, `_8`, `_9`)
- [x] Code review — `docs/code-review-wp-plugin.md` (4 critical, 11 important, 12 minor, 10 notes). All critical + important fixed (2026-02-21):
  - C1-C3: Replaced custom queue with Action Scheduler (eliminates all race conditions — dedup, locking, atomic retries all built-in). Deleted `class-sea-ojs-queue.php` + `class-sea-ojs-queue-page.php` (net -421 lines)
  - C4: `is_active_member()` now excludes the subscription being cancelled (prevents stale cache miss)
  - I3/I4: GDPR erasure fixed — `pre_delete_user()` captures email + OJS user ID before WP deletes the user; removed dead `delete_user_meta()` call
  - I5: `resolve_from_wcs()` uses actual subscription start date (was always today)
  - I6: Reconciliation now bidirectional — detects synced users who are no longer active and queues expire
  - I7: 100ms delay between reconciliation API calls (prevents OJS overload)
  - I8: Removed redundant `resolve_subscription_data()` from hooks (processor re-resolves at processing time)
  - I11: Type ID resolution breaks outer loop once found (was taking last match)

### Pre-launch deliverables

- [x] Draft welcome email copy — `launch/welcome-email.md`
- [x] Staff support runbook — `launch/support-runbook.md`
- [x] Member FAQ — `launch/member-faq.md`
- [ ] OJS upgrade rollback runbook
- [ ] Plugin deployment runbook (deploy/rollback each plugin)

### Launch communication

- [ ] Define launch sequence timing: bulk sync → verify counts → welcome emails → member announcement
- [ ] Member announcement via SEA newsletter
- [ ] Pre-launch heads-up (optional, prevents phishing confusion)

### End-to-end smoke tests

- [ ] WCS subscription → OJS user + subscription created → paywall grants access
- [ ] WCS expiry → OJS subscription expired → paywall denies access
- [ ] Non-member → purchase options displayed (£3/£25/£18) → purchase → access
- [ ] WP email change → OJS email updated
- [ ] Welcome email → set password link works → expired token → recovery path
- [ ] "Test connection" → success/failure/IP diagnostic displayed correctly
- [ ] OJS down → queued → OJS up → retried → subscription created
- [ ] Bulk sync dry-run → full run → counts match

---

## Phase 2: Robustness (after launch)

- [ ] Rate limiting on OJS endpoints (application-layer)
- [ ] Smarter reconciliation (differential: last 25hr first, count comparison, full scan only if counts differ). Note: bidirectional checking already done (Phase 1 code review fix I6)
- [ ] 30-day follow-up email for members who haven't set OJS password
- [ ] Admin dashboard: per-member sync status
- [ ] API key rotation support (two keys during rotation window)
- [ ] On-hold grace period (if SEA wants one — currently immediate expiry)

## Phase 3: UX (nice to have)

- [ ] Onboarding email for new post-launch members
- [ ] OJS notification on access suspension/restoration
- [ ] Password reset flow documentation for members
- [ ] OJS accessibility audit (WCAG 2.1 AA)

---

## WP housekeeping (low priority)

- [ ] Enable Wordfence CAPTCHA on WooCommerce forms (Wordfence → Login Security → Settings)
- [ ] Review Site Health issues (Dashboard → Site Health — 7 items flagged)
- [ ] Remove deactivated WooCommerce Memberships add-ons (Directory Shortcode, Adjust Excerpt Length, Role Handler, Sensei Member Area)
- [ ] Investigate 404s on `/product/existential-analysis-v...` — broken links or old SEO URLs
- [ ] Apply pending plugin updates (test on staging first)
- [ ] **Verify WCS hooks fire with WC Memberships in the chain**: WC Memberships 1.27.5 is active and sits between WCS and UM for role assignment. Confirm `woocommerce_subscription_status_*` hooks still fire normally when creating/expiring a test subscription on staging.

---

## Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| OJS 3.5 upgrade fails | Medium | Critical | Staging first (done), rollback runbook, Janeway backup |
| Sync failures silently drop members | Medium | High | Action Scheduler retries + bidirectional reconciliation + admin alerts |
| Members confused by two logins | High | Medium | Welcome email, login prompt, cross-links, support runbook |
| OJS upgrade breaks plugin | Medium | High | `/preflight` verifies all dependencies after upgrade |
| Members don't set OJS password | High | Medium | Welcome email + login prompt + 30-day follow-up (Phase 2) |
| API key compromised | Low | Critical | `wp-config.php` constant, role check, IP allowlist |
| Bulk emails spam-filtered | Low | Medium | Transactional relay (hard prereq), SPF/DKIM/DMARC, OJS dedup prevents duplicates on re-run |
| Apache strips auth headers | Medium | High | Phase 0.75 verification from WP server IP |
| GDPR erasure request | Low | High | DELETE endpoint: full PII anonymisation + access_keys + user_settings cleanup; WP pre-delete capture ensures OJS deletion succeeds |
| Manual roles missed by sync | Medium | High | Bulk sync + reconciliation check WP roles directly |
