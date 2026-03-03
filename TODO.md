# Roadmap

## Done

- OJS plugin (`wpojs-subscription-api`) — code complete, reviewed
- WP plugin (`wpojs-sync`) — code complete, reviewed
- Docker dev environment
- Member dashboard widget (WooCommerce My Account)
- UI messages (OJS login hint, paywall hint, footer)
- Non-Docker deployment guide — `docs/deployment.md`
- Dev environment clean rebuild verified (2026-03-03) — all 35 e2e tests passing

## Staging test results (2026-03-02)

External tester deployed to staging (WP and OJS staging instances — URLs in `docs/private/hosting-requirements.md`).

- [x] Connection test passes (WP can reach OJS, authenticated, compatible)
- [x] Bulk backfill — existing members synced to OJS (user accounts created, subscriptions activated)
- [x] OJS API endpoint `/api/v1/wpojs` mounted and responding
- [x] WP mapping configured: WC Product 23042 → OJS Type 1
- [ ] **New member flow** — create WCS subscription, verify OJS user + access created automatically
- [ ] **Cancellation/expiry** — cancel subscription, verify OJS access removed
- [ ] **On-hold / failed payment** — test payment failure scenarios
- [ ] **All products mapped** — configure all 6 WC products (1892, 1924, 1927, 23040, 23041, 23042), not just 23042
- [ ] **Password flow** — verify welcome email delivery and password set process

Bugs found and fixed: `hash_equals()` TypeError on numeric secrets (cast to string), `checkRateLimit()` crash when `wpojs_api_log` table missing (try/catch), `getInstallSchemaFile()` missing (table not auto-created on plugin enable), preflight didn't check for API log table or subscription types.

## Before deploying to production

- [ ] Set up OJS 3.5 production server — new DigitalOcean droplet running OJS 3.5 in Docker (staging already on 3.5.0.3). This replaces the current 3.4 server, not an in-place upgrade. See `docs/private/hosting-requirements.md` for full specs.
- [ ] Set up transactional email relay on OJS server (SPF/DKIM/DMARC) — verify welcome email delivery end-to-end
- [ ] Configure WP settings: type mapping for **all 6 WC products**, manual roles, OJS URL
- [ ] Configure OJS `config.inc.php` `[wpojs]` section: allowed IPs, WP member URL, support email. See `docs/deployment.md` for config reference.
- [ ] Create OJS subscription type(s) and record the `type_id`(s) — required before sync. Preflight check warns if missing.
- [ ] Run `wp ojs-sync test-connection` to verify connectivity (now also checks API log table and subscription types)
- [ ] Run `wp ojs-sync sync --dry-run` to verify member resolution
- [ ] Run `wp ojs-sync sync` for bulk initial sync
- [ ] Run `wp ojs-sync send-welcome-emails` to invite members

## Post-deploy checks

- [ ] Non-member → purchase options displayed → purchase → access (requires OJS payment plugin configured with live gateway)
- [ ] Welcome email delivery end-to-end (requires SMTP/SPF/DKIM on production OJS)

## End-to-end smoke tests

- [x] WCS subscription activate → OJS user + subscription created → paywall grants access
- [x] WCS expiry → OJS subscription expired → paywall denies access
- [x] WP email change → OJS email updated
- [x] Welcome email → API logic, dedup, idempotency (email transport is a deployment infra check, not a code smoke test)
- [x] OJS down → queued → OJS up → retried → synced
- [x] Bulk sync dry-run → full run → counts match (683/683)

### Playwright E2E browser tests (`e2e/`)

- [x] Sync lifecycle — WCS activate/expire → OJS subscription status
- [x] OJS login — synced user sets password + logs in
- [x] WP dashboard — My Account journal access widget (active + inactive)
- [x] OJS UI messages — login hint, footer, paywall hint
- [x] Admin monitoring — Sync Log page stats, nonce, retry actions
- [x] OJS API request logging — log table + `wpojs_created_by_sync` flag
- [x] Email change sync — WP email change → OJS email updated
- [x] User deletion / GDPR — WP user delete → OJS user anonymised + disabled
- [x] Test connection — settings page AJAX test reports success
- [x] Error recovery — sync fails when OJS unreachable, succeeds on retry

## Future improvements

- [x] Rate limiting on OJS API — implemented: per-IP rate limiting using the API log table, configurable via `rate_limit_requests` / `rate_limit_window` in `config.inc.php [wpojs]`.
- [x] Automatic API log cleanup — runs at most once per hour during API requests, deletes entries older than 30 days.
- [x] 429 rate-limit handling — WP client now treats 429 as retryable (not permanent fail), captures `Retry-After` header.
- [x] GDPR log anonymization — sync log emails anonymized on WP user deletion (`deleted-user-{id}@anonymised.invalid`).
- [x] Email change race condition fix — stale pending email change actions cancelled on new change; staleness check in handler skips superseded changes.
- [x] Bulk sync CLI confirmation — `WP_CLI::confirm()` before bulk sync, skippable with `--yes`.
- [x] Admin failure notices — warning banner on dashboard/OJS Sync pages when >= 5 failures in last 24h.
- [x] Dashboard "last synced" timestamp — shows last successful sync date on the journal access card.
- [x] OJS settings length validation — `mb_substr(..., 0, 500)` on message settings, `maxlength="500"` on textareas.
- [x] Type mapping validation on test-connection — warns if WC product IDs don't exist or no mapping configured.
- [x] Rollback runbook — added to `docs/private/plan.md` covering OJS upgrade, plugin rollback, bulk sync, welcome emails.
- [x] Support runbook additions — password reset, welcome email customization, monitoring endpoint, email security note.
- [x] waitForSync E2E buffer — 500ms sleep after Action Scheduler run.
- [x] Concurrent change E2E test — activate + email change before queue processes.
- [ ] Admin per-member sync status — the Sync Log page shows global stats but no per-user view. Data already exists in `wp_wpojs_sync_log` + `_wpojs_user_id` usermeta; just needs a UI. Useful for support ("is this member synced?").
- [ ] Follow-up email for members who haven't set OJS password — requires a new OJS endpoint to query users with `must_change_password=true`, or direct DB query. Implement post-launch once you have data on how many members actually set their passwords.

Dropped (not worth the complexity):
- ~~Differential reconciliation~~ — current full scan already batches (chunks of 100). Fast enough for thousands of members.
- ~~API key rotation~~ — single shared secret between two systems we control. Rotation is an ops procedure (update both configs), not a code feature.
- ~~On-hold grace period~~ — on-hold immediately expires OJS access, but WCS retries failed payments automatically. If this generates support tickets post-launch, revisit then.
