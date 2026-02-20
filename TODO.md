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

Local dev environment with WP + OJS + two MariaDB instances on Docker. Bedrock (Composer-managed WP), OJS config templating, fully scripted setup. Both plugins bind-mounted for live editing. See `docker/README.md` for quick reference, `docs/ojs-issues-log.md` for bugs encountered.

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

## Phase 0.6: Set up staging for testing

- [ ] **WP staging**: Install WooCommerce + WCS, create one subscription product (test mode), create 5-10 test users, verify WCS hooks fire
- [ ] **OJS staging**: Confirm test journal + paywalled article, create subscription type (record `type_id`), configure `api_key_secret`, create dedicated service account with Journal Manager role, test Bearer token auth from WP server

## Phase 0.75: Verify OJS API prerequisites

- [ ] **Set up transactional email relay** (Mailgun/SES/Postmark) on OJS — hard prerequisite for bulk sync. Check SPF/DKIM/DMARC. Welcome emails go out inline during bulk sync (~700 at once), not batched.
- [ ] Document OJS server specs (RAM, PHP limits, web server type)

---

## Phase 1: Build it

### OJS plugin (`sea-subscription-api`) — DONE

Code complete. 11 endpoints, reviewed by 4 agents (QA, security, PHP architect, WP consumer). All blockers and important issues fixed.

**What's built:** Plugin skeleton (SeaSubscriptionApiPlugin.php, SeaApiController.php, version.xml, locale). Endpoints: `GET /ping` (no auth), `GET /preflight`, `GET /users?email=`, `POST /users/find-or-create`, `PUT /users/{id}/email`, `DELETE /users/{id}`, `POST /subscriptions`, `PUT /subscriptions/{id}/expire`, `PUT /subscriptions/expire-by-user/{id}`, `GET /subscriptions`, `POST /welcome-email`. Auth: Bearer token + Journal Manager/Site Admin role + IP allowlist (REMOTE_ADDR). Full PII anonymisation on delete. Idempotent upserts. Atomic welcome email dedup. Password reset via AccessKeyManager.

**Remaining before deploy:**
- [ ] Set `password_reset_timeout = 7` in config.inc.php
- [ ] Custom login page message: "SEA member? First time here? Set your password"
- [ ] Paywall message for logged-in users with no subscription: "SEA member? Contact [support email]"
- [ ] OJS footer: "Your journal access is provided by your SEA membership. Manage at [WP URL]"
- [ ] Run PHPStan
- [ ] Code review (second pair of eyes)

### WP plugin (`sea-ojs-sync`) — CODE COMPLETE

13 files. PHP syntax verified. Internal consistency reviewed (constructor args, method names, hook names, DB columns, settings keys — all match).

**What's built:** Plugin bootstrap, activator (DB tables + cron scheduling), API client (10 OJS endpoint methods, Bearer auth, error classification), queue (dedup, retry, status transitions), logger (paginated queries, cleanup), subscription resolver (multi-sub resolution, manual roles, active member query), sync processor (activate/expire/email_change/delete_user with correct API call sequences, retry logic 5min/15min/1hr, admin alerts), WCS hooks + profile_update + deleted_user, cron handlers (queue processor, daily reconciliation, daily digest), settings page (OJS URL, type mapping, manual roles, test connection AJAX, server info), admin log viewer (WP_List_Table with filters), admin queue viewer (WP_List_Table with retry action), WP-CLI commands (sync, reconcile, status, test-connection).

**Remaining before deploy:**
- [ ] **Member dashboard**: "Access Existential Analysis" link, access status indicator
- [ ] Configure settings: type mapping (WC Product IDs → OJS Type IDs), manual roles (`um_custom_role_7`, `_8`, `_9`)
- [ ] Code review

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
- [ ] Smarter reconciliation (differential: last 25hr first, count comparison, full scan only if counts differ)
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
| Sync failures silently drop members | Medium | High | Queue + retries + reconciliation + admin alerts |
| Members confused by two logins | High | Medium | Welcome email, login prompt, cross-links, support runbook |
| OJS upgrade breaks plugin | Medium | High | `/preflight` verifies all dependencies after upgrade |
| Members don't set OJS password | High | Medium | Welcome email + login prompt + 30-day follow-up (Phase 2) |
| API key compromised | Low | Critical | `wp-config.php` constant, role check, IP allowlist |
| Bulk emails spam-filtered | Low | Medium | Transactional relay (hard prereq), SPF/DKIM/DMARC, OJS dedup prevents duplicates on re-run |
| Apache strips auth headers | Medium | High | Phase 0.75 verification from WP server IP |
| GDPR erasure request | Low | High | DELETE endpoint: full PII anonymisation + subscription expiry |
| Manual roles missed by sync | Medium | High | Bulk sync + reconciliation check WP roles directly |
