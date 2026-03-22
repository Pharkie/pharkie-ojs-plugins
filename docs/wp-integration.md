# WP Integration: Ultimate Member + WooCommerce Subscriptions

Last updated: 2026-03-22.

This doc covers the WP membership stack and the raw hooks available. For the full WP plugin spec (queue tables, CLI commands, cron schedule, admin pages), see [`plan.md`](./private/plan.md#wp-plugin-spec).

## Plugin stack

The membership system is six WordPress plugins layered on top of each other. None of them is a membership system on its own — each answers one question:

| Plugin | Question it answers | Without it... |
|--------|-------------------|---------------|
| **WooCommerce** | "Can we take payments?" | No shop, no checkout, no orders. |
| **WC Subscriptions** | "Has this person paid?" | WooCommerce can only do one-off purchases. No recurring billing, no auto-renewal, no expiry. |
| **WC Memberships** | "What access do they get?" | Paying doesn't grant anything. Someone has to manually assign roles after each payment. |
| **Ultimate Member** | "What's their experience?" | No registration forms, no profiles, no member directory. Just raw WP user accounts. |
| **UM-WooCommerce** | "Can UM see WooCommerce data?" | UM profiles and WC purchase history are disconnected. No order history on profiles, no synced fields. |
| **UM-Notifications** | "Do members get notified?" | No email/on-site notifications for UM profile events (new followers, role changes, etc.). |

### How they chain together

```
Member signs up and pays
        │
        ▼
   WooCommerce          ← processes the payment (Stripe, PayPal, etc.)
        │
        ▼
   WC Subscriptions     ← turns it into a recurring subscription
        │                   handles renewals, failed payments, cancellations
        │
        ▼
   WC Memberships       ← "subscription active" → assigns a WordPress role
        │                   "subscription expired" → removes the role
        │
        ▼
   Ultimate Member      ← role determines: profile template, directory listing,
        │                   what the member sees on the site
        │
        ▼
   UM-WooCommerce       ← glues UM profiles to WC data (purchase history, etc.)
```

### Why this matters for our OJS sync

We only care about **WC Subscriptions**. It fires status events (`active`, `expired`, `cancelled`, `on-hold`) that tell us whether someone should have journal access. Everything downstream (Memberships assigning roles, UM rendering profiles) is irrelevant to the sync.

### Why this is fragile

Six plugins, three vendors (Automattic, SkyVerge, Ultimate Member), each with independent update cycles, database tables, hooks, and paid licences (~£200+/year for WCS alone). A breaking update in any one of them can silently break the membership chain. This stack is a strong candidate for future replacement — see [`membership-platform.md`](./private/membership-platform.md).

### Plugin versions and sources

| Plugin | Version | Source | Licence | Bedrock |
|--------|---------|--------|---------|---------|
| WooCommerce | 9.8+ | wordpress.org (wpackagist) | Free | `wpackagist-plugin/woocommerce` |
| Ultimate Member | 2.11+ | wordpress.org (wpackagist) | Free | `wpackagist-plugin/ultimate-member` |
| WooCommerce Subscriptions | 8.4.0 | woocommerce.com | Paid | `wordpress/paid-plugins/` |
| WooCommerce Memberships | 1.27.5 | woocommerce.com | Paid | `wordpress/paid-plugins/` |
| UM - WooCommerce | 2.4.4 | ultimatemember.com | Paid | `wordpress/paid-plugins/` |
| UM - Notifications | 2.3.8 | ultimatemember.com | Paid | `wordpress/paid-plugins/` |

**WC Memberships note:** Several WC Memberships add-ons are deactivated on the live site (Directory Shortcode, Adjust Excerpt Length, Role Handler, Sensei Member Area) but the core plugin is active.

## Hooks used for OJS sync

**WooCommerce Subscriptions hooks only.** These fire on subscription lifecycle events and are the sole trigger for OJS sync. UM role change hooks were considered as a secondary safety net but dropped — they double-fire alongside WCS hooks, fire for unrelated role changes, and add complexity. The daily reconciliation job catches any edge cases WCS hooks miss (e.g. admin manual role changes).

```php
// Subscription activated (new signup or reactivation)
// → Queue: find-or-create OJS user (with WP password hash) + create/renew subscription
add_action('woocommerce_subscription_status_active', 'wpojs_queue_activate');

// Subscription expired
// → Queue: expire OJS subscription
add_action('woocommerce_subscription_status_expired', 'wpojs_queue_expire');

// Subscription cancelled
// → Queue: expire OJS subscription
add_action('woocommerce_subscription_status_cancelled', 'wpojs_queue_expire');

// Subscription on hold (e.g. payment failed)
// → Queue: expire OJS subscription (immediate, no grace period)
add_action('woocommerce_subscription_status_on-hold', 'wpojs_queue_expire');
```

**Important: all hooks queue a sync intent, they do not make inline HTTP calls.** Action Scheduler handles the actual OJS API calls asynchronously. This prevents OJS downtime from blocking WP checkout.

### Additional hooks

```php
// Email change — propagate to OJS so the email-as-key model stays in sync
// → Queue: update OJS user email (old email → new email)
add_action('profile_update', 'wpojs_queue_email_change', 10, 3);

// User deletion — GDPR erasure propagation
// → Queue: delete/anonymise OJS user account
add_action('deleted_user', 'wpojs_queue_delete_user');
```

## WC_Subscription object

The `$subscription` object passed to WCS hooks is a `WC_Subscription` instance. Key methods:

- `$subscription->get_user_id()` — WP user ID
- `$subscription->get_status()` — `active`, `on-hold`, `cancelled`, `expired`, `pending-cancel`
- `$subscription->get_date('end')` — expiry date (Y-m-d H:i:s, or `0` if non-expiring)
- `$subscription->get_date('next_payment')` — next billing date
- `$subscription->get_items()` — line items (products purchased)

## Getting user data for OJS sync

```php
$user_id = $subscription->get_user_id();
$user = get_userdata($user_id);

// For OJS user find-or-create:
$email     = $user->user_email;
$firstname = $user->first_name;
$lastname  = $user->last_name;

// For OJS subscription dates:
$date_end = $subscription->get_date('end'); // Y-m-d H:i:s or 0 if non-expiring
// Non-expiring (0) → pass dateEnd = null to OJS endpoint
```

## Multiple subscriptions per member

A member can have multiple active WCS subscriptions (e.g. overlapping tiers during an upgrade). The sync logic resolves to **one OJS subscription per member per journal**:

- If **any** active WCS subscription exists → OJS subscription is active
- OJS `date_end` = **latest** end date across all active WCS subscriptions
- All tiers grant the same OJS access, so subscription type differences don't matter

```php
// Resolve across all active subscriptions for a user:
$subscriptions = wcs_get_subscriptions([
    'subscription_status' => 'active',
    'customer_id' => $user_id,
]);
$latest_end = null;
foreach ($subscriptions as $sub) {
    $end = $sub->get_date('end');
    if ($end === 0 || $end === '') {
        $latest_end = null; // non-expiring wins
        break;
    }
    if ($latest_end === null || $end > $latest_end) {
        $latest_end = $end;
    }
}
```

## Membership roles

The WP plugin needs to know which WordPress roles represent members with journal access. There are two categories:

**WCS-managed roles** — assigned automatically when a subscription becomes active, removed when it expires/cancels. WCS hooks fire for these, so the sync is automatic.

**Manual/admin roles** — assigned by an admin for complimentary or lifetime members (e.g., board members, honorary members). WCS hooks won't fire for these. The bulk sync and daily reconciliation detect these roles directly via the `WPOJS_MANUAL_MEMBER_ROLES` setting.

Configure manual member roles on the WP plugin settings page (OJS Sync → Settings). The role slugs are visible in WP Admin → Users → edit any user → Role dropdown.

> **Site-specific role audit:** For an example of mapping live site roles, see [`private/sea-site-audit.md`](./private/sea-site-audit.md).

## WooCommerce subscription products

Each WCS subscription product represents a membership tier. The WP plugin settings page maps each WC Product ID to an OJS Subscription Type ID.

If all tiers grant identical journal access, they can all map to a single OJS subscription type. Use separate types if you want tier breakdowns in OJS admin.

Find your WC Product IDs in WP Admin → Products → edit each subscription product (the ID is in the URL).

> **Site-specific product audit:** For an example product mapping, see [`private/sea-site-audit.md`](./private/sea-site-audit.md).

## Mapping membership tiers to OJS subscription types

**All membership tiers grant journal access.** Any active WCS subscription → OJS access regardless of tier. The WP plugin settings page includes a mapping table: WC Product ID → OJS Subscription Type ID. Even though all tiers grant access, the mapping keeps things explicit and supports future differentiation.

```php
// Check which product the subscription is for:
$items = $subscription->get_items();
foreach ($items as $item) {
    $product_id = $item->get_product_id();
    // Look up OJS subscription type from settings mapping
}
```

## Bulk sync

WP-CLI command only (not admin button — avoids HTTP timeouts). See [`plan.md`](./private/plan.md#wp-cli-commands) for the full command spec.

```php
// Core query for bulk sync:
$subscriptions = wcs_get_subscriptions(['subscription_status' => 'active']);
// Then iterate in batches of 50 with adaptive throttling between API calls
```

`wcs_get_subscriptions()` is the WooCommerce Subscriptions helper function for querying subscriptions.

## UM hooks: why they were dropped

Ultimate Member role change hooks (`um_after_member_role_upgrade`, `um_after_user_role_is_updated`, etc.) were originally planned as a secondary safety net. They were dropped because:

1. **Double-firing**: WCS status changes trigger UM role changes, so both hooks fire for the same event — duplicate OJS API calls
2. **False positives**: UM hooks fire for role changes unrelated to membership (admin actions, other plugins)
3. **Complexity**: would need dedup logic (timestamp checks, action comparison) to avoid noise
4. **Unnecessary**: daily reconciliation catches anything WCS hooks miss. For immediate manual grants, use `wp ojs-sync sync --member=<email>`.

The UM hooks are documented here for reference in case the decision is revisited. The raw hook signatures:

```php
add_action('um_after_member_role_upgrade', function($new_roles, $old_roles, $user_id) {}, 10, 3);
add_action('um_when_status_is_set', function($user_id) {}, 10, 1);
add_action('um_after_user_role_is_updated', function($user_id, $role) {}, 10, 2);
```
