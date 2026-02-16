# WP Integration: Ultimate Member + WooCommerce Subscriptions

Last updated: 2026-02-16.

## Stack

SEA uses **Ultimate Member + WooCommerce + WooCommerce Subscriptions**:

- **Ultimate Member** — user registration, profiles, role management
- **WooCommerce** — e-commerce layer (products, orders, checkout)
- **WooCommerce Subscriptions** — recurring subscription billing, status management

Membership = WP role assignment. When a user buys a subscription via WooCommerce, WooCommerce Subscriptions manages the billing cycle and Ultimate Member assigns/removes the corresponding WP role.

## Primary hooks (WooCommerce Subscriptions)

These are the most reliable integration points. They fire on subscription lifecycle events:

```php
// Subscription activated (new signup or reactivation)
add_action('woocommerce_subscription_status_active', function($subscription) {
    $user_id = $subscription->get_user_id();
    $user = get_userdata($user_id);
    $email = $user->user_email;
    // → Call OJS: create/renew subscription
});

// Subscription expired
add_action('woocommerce_subscription_status_expired', function($subscription) {
    $user_id = $subscription->get_user_id();
    // → Call OJS: expire subscription
});

// Subscription cancelled
add_action('woocommerce_subscription_status_cancelled', function($subscription) {
    $user_id = $subscription->get_user_id();
    // → Call OJS: expire subscription
});

// Subscription on hold (e.g. payment failed)
add_action('woocommerce_subscription_status_on-hold', function($subscription) {
    $user_id = $subscription->get_user_id();
    // → Call OJS: expire subscription
});

// Catch-all for any status transition
add_action('woocommerce_subscription_status_updated', function($subscription, $new_status, $old_status) {
    // Useful for logging or handling edge cases
}, 10, 3);
```

The `$subscription` object is a `WC_Subscription` instance. Key methods:
- `$subscription->get_user_id()` — WP user ID
- `$subscription->get_status()` — `active`, `on-hold`, `cancelled`, `expired`, `pending-cancel`
- `$subscription->get_date('end')` — expiry date
- `$subscription->get_date('next_payment')` — next billing date
- `$subscription->get_items()` — line items (products purchased)

## Secondary hooks (Ultimate Member role changes)

These fire when UM changes a user's role. Less reliable for our purposes (role changes can happen for reasons unrelated to membership), but useful as a safety net:

```php
// Role upgrade
add_action('um_after_member_role_upgrade', function($new_roles, $old_roles, $user_id) {
    // Check if new role is a member role
}, 10, 3);

// Status change (active, inactive, etc.)
add_action('um_when_status_is_set', function($user_id) {
    $status = um_user('account_status');
}, 10, 1);

// Role update (more general)
add_action('um_after_user_role_is_updated', function($user_id, $role) {
    // Fires after any role change
}, 10, 2);
```

## Recommended approach

**Use WooCommerce Subscriptions hooks as the primary trigger.** They map directly to what we care about:

| WCS event | OJS action |
|---|---|
| `status_active` | Create or renew subscription |
| `status_expired` | Expire subscription |
| `status_cancelled` | Expire subscription |
| `status_on-hold` | Expire subscription |

**Use UM hooks as a secondary safety net** — if a role change happens that wasn't triggered by WCS (e.g. admin manually changes role), catch it and sync.

## Getting user data for OJS sync

```php
$user_id = $subscription->get_user_id();
$user = get_userdata($user_id);

// For OJS user find-or-create:
$email     = $user->user_email;
$firstname = $user->first_name;
$lastname  = $user->last_name;
$username  = $user->user_login;

// For OJS subscription dates:
$date_end = $subscription->get_date('end'); // Y-m-d H:i:s or 0 if non-expiring
$next_payment = $subscription->get_date('next_payment');
```

## Mapping membership tiers to OJS subscription types

**All membership tiers grant journal access.** Any active WCS subscription triggers an OJS subscription. This simplifies the mapping — no need to filter by product/tier.

The WP plugin settings page will still have a mapping table (WooCommerce Product ID → OJS Subscription Type ID) in case SEA adds tiers that shouldn't grant access in future, but the default is "all active = access".

```php
// Check which product the subscription is for:
$items = $subscription->get_items();
foreach ($items as $item) {
    $product_id = $item->get_product_id();
    // Look up OJS subscription type from settings mapping
}
```

## Bulk sync

For initial population, the WP plugin needs a bulk sync command that:
1. Queries all active WooCommerce Subscriptions
2. For each, calls OJS to find-or-create user + create subscription

```php
// WP-CLI command or admin page:
$subscriptions = wcs_get_subscriptions(['subscription_status' => 'active']);
foreach ($subscriptions as $subscription) {
    sea_ojs_sync_subscription($subscription);
}
```

`wcs_get_subscriptions()` is the WooCommerce Subscriptions helper function for querying subscriptions.
