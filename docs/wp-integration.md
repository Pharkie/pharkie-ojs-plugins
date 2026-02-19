# WP Integration: Ultimate Member + WooCommerce Subscriptions

Last updated: 2026-02-17.

This doc covers the WP membership stack and the raw hooks available. For the full WP plugin spec (queue tables, CLI commands, cron schedule, admin pages), see [`plan.md`](./plan.md#wp-plugin-spec).

## Stack

SEA uses **Ultimate Member + WooCommerce + WooCommerce Subscriptions**:

- **Ultimate Member** — user registration, profiles, role management
- **WooCommerce** — e-commerce layer (products, orders, checkout)
- **WooCommerce Subscriptions** — recurring subscription billing, status management

Membership = WP role assignment. When a user buys a subscription via WooCommerce, WooCommerce Subscriptions manages the billing cycle and Ultimate Member assigns/removes the corresponding WP role.

## Hooks used for OJS sync

**WooCommerce Subscriptions hooks only.** These fire on subscription lifecycle events and are the sole trigger for OJS sync. UM role change hooks were considered as a secondary safety net but dropped — they double-fire alongside WCS hooks, fire for unrelated role changes, and add complexity. The daily reconciliation job catches any edge cases WCS hooks miss (e.g. admin manual role changes).

```php
// Subscription activated (new signup or reactivation)
// → Queue: find-or-create OJS user + create/renew subscription + welcome email (if new user)
add_action('woocommerce_subscription_status_active', 'sea_ojs_queue_activate');

// Subscription expired
// → Queue: expire OJS subscription
add_action('woocommerce_subscription_status_expired', 'sea_ojs_queue_expire');

// Subscription cancelled
// → Queue: expire OJS subscription
add_action('woocommerce_subscription_status_cancelled', 'sea_ojs_queue_expire');

// Subscription on hold (e.g. payment failed)
// → Queue: expire OJS subscription (immediate, no grace period)
add_action('woocommerce_subscription_status_on-hold', 'sea_ojs_queue_expire');
```

**Important: all hooks queue a sync intent, they do not make inline HTTP calls.** The WP Cron queue processor handles the actual OJS API calls asynchronously. This prevents OJS downtime from blocking WP checkout.

### Additional hooks

```php
// Email change — propagate to OJS so the email-as-key model stays in sync
// → Queue: update OJS user email (old email → new email)
add_action('profile_update', 'sea_ojs_queue_email_change', 10, 3);

// User deletion — GDPR erasure propagation
// → Queue: delete/anonymise OJS user account
add_action('deleted_user', 'sea_ojs_queue_delete_user');
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

## Membership roles on live site

From the WP admin roles list (confirmed 2026-02-19):

**SEA membership roles** (all grant journal access):
- SEA student member (with listing)
- SEA student member (no listing)
- SEA international member (with listing)
- SEA international member (no listing)
- SEA UK member (with listing)
- SEA UK member (no listing)

**Manual/admin roles** (for Exco/life members — set by admin, not via WCS checkout):
- Manually set student listing
- Manually set international listing
- Manually set UK listing

These also grant journal access. Because they're assigned manually (not via WCS checkout), WCS hooks won't fire for them. The bulk sync and daily reconciliation must detect these roles directly, not rely solely on WCS subscription status.

**Standard WP/WooCommerce/other plugin roles** (not relevant to sync):
- Subscriber, Shop Manager, Editor, Customer, Contributor
- SEO Manager, SEO Editor
- GiveWP Worker, GiveWP Manager, GiveWP Donor, GiveWP Accountant

The "with listing" / "no listing" distinction is a member directory feature (UM), not relevant to journal access. All nine SEA membership roles (six standard + three manual) grant OJS access.

## Mapping membership tiers to OJS subscription types

**All membership tiers grant journal access**, but there are **multiple WooCommerce Subscription products** (e.g. different tiers like student, full, etc.). Any active subscription triggers OJS access regardless of tier.

The WP plugin settings page includes a mapping table: WooCommerce Product ID → OJS Subscription Type ID. Even though all tiers grant access, the mapping keeps things explicit and supports future differentiation if needed.

```php
// Check which product the subscription is for:
$items = $subscription->get_items();
foreach ($items as $item) {
    $product_id = $item->get_product_id();
    // Look up OJS subscription type from settings mapping
}
```

## Bulk sync

WP-CLI command only (not admin button — avoids HTTP timeouts). See [`plan.md`](./plan.md#wp-cli-commands) for the full command spec.

```php
// Core query for bulk sync:
$subscriptions = wcs_get_subscriptions(['subscription_status' => 'active']);
// Then iterate in batches of 50 with 500ms delay between API calls
```

`wcs_get_subscriptions()` is the WooCommerce Subscriptions helper function for querying subscriptions.

## UM hooks: why they were dropped

Ultimate Member role change hooks (`um_after_member_role_upgrade`, `um_after_user_role_is_updated`, etc.) were originally planned as a secondary safety net. They were dropped because:

1. **Double-firing**: WCS status changes trigger UM role changes, so both hooks fire for the same event — duplicate OJS API calls
2. **False positives**: UM hooks fire for role changes unrelated to membership (admin actions, other plugins)
3. **Complexity**: would need dedup logic (timestamp checks, action comparison) to avoid noise
4. **Unnecessary**: daily reconciliation catches anything WCS hooks miss. For immediate manual grants, use `wp sea-ojs sync --user=<email>`.

The UM hooks are documented here for reference in case the decision is revisited. The raw hook signatures:

```php
add_action('um_after_member_role_upgrade', function($new_roles, $old_roles, $user_id) {}, 10, 3);
add_action('um_when_status_is_set', function($user_id) {}, 10, 1);
add_action('um_after_user_role_is_updated', function($user_id, $role) {}, 10, 2);
```
