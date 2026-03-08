# OAuth Revisited: Post-Implementation Reassessment

Last updated: 2026-03-08

After building and testing the push-sync plugins, we revisited whether a custom OAuth approach would have been better. This documents that reassessment honestly.

---

## Context

The original discovery phase (Feb 2026) eliminated OIDC SSO because the existing OJS OIDC plugin was broken. But the question was never properly asked: **what if we built a custom OAuth flow ourselves, controlling both sides?**

With push-sync now working, we revisited this with fresh eyes (Mar 2026).

## The OAuth variant we evaluated

Not the broken OJS OIDC plugin. A custom-built flow:

1. OJS login page has two options: "Register / Log in" (normal OJS) and "Log in as SEA member" (OAuth)
2. "Log in as SEA member" redirects to WP
3. WP authenticates (standard WP login), checks WCS membership status
4. WP signs a short-lived JWT with claims (WP user ID, email, name, membership status, subscription end date) using a per-site secret
5. WP redirects back to OJS callback URL with the JWT
6. OJS plugin validates JWT signature and expiry
7. OJS creates/finds user (matched by WP user ID in metadata), creates/extends subscription
8. Member accesses content via OJS's native paywall — no custom access control hooks

**Key design choice:** the OJS plugin creates real user + subscription records and lets OJS's native paywall handle access. It does NOT try to intercept the paywall check (which is what broke the Subscription SSO plugin).

## What we'd need to build

**WP side (~200 lines):** A single endpoint that requires WP login, checks WCS membership, signs a JWT with membership claims, and redirects back to OJS. No standards-compliant OAuth2 server needed — we control both ends, so a signed redirect is sufficient.

**OJS side (similar scope to current plugin):** OAuth consumer that handles the callback, validates the JWT, provisions/updates the OJS user and subscription record. Reuses the same `IndividualSubscriptionDAO` and user creation logic as the current push-sync plugin.

**miniOrange OAuth Server** (already installed on live WP) was evaluated but the free version has shared HS256 signing keys across all installations (security dealbreaker) and no custom claims. Premium would work but adds a paid dependency for something we can build in ~200 lines.

## Reassessment of original objections

The original discovery doc's reasons for eliminating OIDC/OAuth were based on the broken OJS plugin, not the architecture. A custom build avoids those specific problems:

| Original objection | Reassessment |
|---|---|
| "OIDC only solves auth, not authorization" | A custom OAuth flow includes membership claims in the JWT. Auth = authorization for our use case (one subscription type, one journal, fixed terms). |
| "OJS OIDC plugin has unresolved bugs" | Irrelevant — we'd build our own consumer, not use the broken plugin. |
| "You'd still need to sync subscriptions" | True, but the sync happens at login time (on-demand) rather than via a push queue. Same OJS DAOs, different trigger. |
| "Non-members can't buy content" (Pull-verify) | Doesn't apply — OAuth uses a separate login button, not a paywall intercept. Non-members use normal OJS registration/login. |

## What OAuth does better

**Member UX:** No second password, no welcome email, no "set up your OJS account" step. Click a button, log in with existing WP credentials, done.

**Email changes:** OAuth identifies users by WP user ID, not email. Email changes propagate automatically on next login. No separate sync needed.

## What OAuth doesn't solve — and what it makes harder

**OJS still needs user + subscription records.** The OAuth plugin creates these on login, using the same DAOs as push-sync. The end state in the OJS database is identical — the difference is when and how records get created. This is NOT a simplification of the OJS plugin; it's the same subscription provisioning code with a different trigger.

**Subscription expiry is more complex, not less.** Push-sync expires subscriptions immediately when WCS fires a status change hook. OAuth only checks membership at login time. Between logins, OJS relies on its own subscription table. You'd need to set end dates and manage rolling expiry windows — complexity that push-sync avoids because it reacts to events in real time.

**Two auth paths on OJS.** Members use OAuth; non-members, authors, and reviewers use normal OJS accounts. Two buttons on the login page, two code paths to maintain, two sets of edge cases.

**GDPR deletion loses automation.** Push-sync anonymizes OJS users automatically on WP user deletion. OAuth would need a separate mechanism.

**The auth flow itself is a new attack surface.** JWT signing, token validation, redirect URI handling, CSRF protection — all security-critical code that doesn't exist in push-sync. Push-sync uses a simple shared API key over HTTPS between servers on the same box. OAuth introduces browser redirects, token-in-URL risks, and session management.

**Unknown unknowns.** Push-sync's failure modes are well-understood: the queue, retries, and reconciliation exist precisely because we know what can go wrong and handle it explicitly. OAuth's failure modes are less predictable — what happens when OJS upgrades break the consumer plugin's login hooks? When JWT libraries have vulnerabilities? When browser cookie policies change? Push-sync is server-to-server; OAuth flows through the user's browser, adding a whole class of client-side edge cases.

**OJS plugin fragility.** The Subscription SSO plugin broke because it hooked into OJS's access control flow — the exact area that changes between OJS versions. An OAuth consumer plugin would hook into OJS's authentication flow, which is equally version-sensitive. Push-sync's OJS plugin only exposes REST endpoints using stable DAOs — it doesn't hook into any user-facing OJS flow.

## Conclusion

OAuth has genuinely better member UX (no second password). But it's not simpler — it trades push-sync's explicit sync machinery for auth-flow complexity, and introduces browser-based failure modes that push-sync avoids entirely by being server-to-server.

The two approaches are roughly equivalent in implementation effort and total complexity. Push-sync's complexity is visible and well-understood (queues, retries, reconciliation). OAuth's complexity is in the auth flow, session management, and vulnerability to OJS upgrade breakage — less visible but no less real.

Push-sync was a reasonable choice. OAuth would also have been a reasonable choice. Neither is clearly superior. We're sticking with push-sync because it's built, tested, and its failure modes are known — not because we evaluated OAuth and rejected it on the merits alone.

## If we ever revisit

The tagged commit `v1-push-sync` preserves the current working implementation. If push-sync causes ongoing maintenance problems (sync drift, queue failures, reconciliation issues), OAuth remains a viable alternative. The WP side is ~200 lines; the OJS side reuses most of the existing plugin's subscription provisioning logic.
