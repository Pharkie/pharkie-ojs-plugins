# Code Review: `wpojs-sync` WordPress Plugin

> **SUPERSEDED (2026-03-08):** This review predates the password hash sync implementation and Action Scheduler refactoring. References to a custom queue table (`class-wpojs-queue.php`), welcome emails, and 500ms fixed delays no longer apply. Kept for historical context only — see current code for the actual implementation.

Reviewed: 2026-02-21.

Plugin path: `plugins/wpojs-sync/`

Files reviewed (all files in the plugin):

- `wpojs-sync.php` (bootstrap)
- `composer.json`
- `includes/class-wpojs-activator.php`
- `includes/class-wpojs-api-client.php`
- `includes/class-wpojs-cron.php`
- `includes/class-wpojs-dashboard.php`
- `includes/class-wpojs-hooks.php`
- `includes/class-wpojs-logger.php`
- `includes/class-wpojs-queue.php`
- `includes/class-wpojs-resolver.php`
- `includes/class-wpojs-sync.php`
- `includes/cli/class-wpojs-cli.php`
- `includes/admin/class-wpojs-settings.php`
- `includes/admin/class-wpojs-log-page.php`
- `includes/admin/class-wpojs-queue-page.php`

Reviewed against: `docs/plan.md` (endpoint spec, queue state machine, hook mapping), `docs/wp-integration.md` (WCS hooks, resolver logic), `docs/ojs-sync-plugin-api.md` (OJS internals), `CLAUDE.md` (constraints).

---

## Critical

### C1. Queue dedup race condition allows duplicate processing

**File:** `includes/class-wpojs-queue.php`, lines 29-39

The dedup check in `enqueue()` uses a SELECT-then-INSERT pattern with no locking. Under concurrent requests (e.g. two WCS hooks firing near-simultaneously for the same user), both can pass the SELECT check before either INSERT completes, resulting in duplicate queue items.

```php
$existing = $wpdb->get_var( $wpdb->prepare(
    "SELECT id FROM {$this->table}
     WHERE wp_user_id = %d AND action = %s AND status IN ('pending', 'processing')
     LIMIT 1",
    $wp_user_id,
    $action
) );

if ( $existing ) {
    return false;
}

$wpdb->insert( ... );
```

**Fix:** Add a UNIQUE index on `(wp_user_id, action, status)` where status is pending/processing, or use `INSERT ... ON DUPLICATE KEY IGNORE` with a unique constraint. Alternatively, wrap the check+insert in a transaction with `SELECT ... FOR UPDATE`. For WP simplicity, the best approach is a unique index plus catching the duplicate key error:

```sql
KEY idx_dedup (wp_user_id, action, status)
```

Then use `$wpdb->query()` with `INSERT IGNORE` or catch the `$wpdb->last_error` on duplicate.

**Impact:** Duplicate queue items cause duplicate OJS API calls. The OJS endpoints are idempotent so this won't cause data corruption, but it wastes API calls and clutters the log. Severity is mitigated by idempotency but the race window is real during bulk operations.

---

### C2. Queue processor has no locking -- concurrent cron runs can double-process items

**File:** `includes/class-wpojs-queue.php`, lines 62-76 and 81-89

`get_due_items()` fetches items with `status = 'pending'`, and `mark_processing()` updates them individually. If two cron runs overlap (WP Cron is notoriously unreliable with timing), both can fetch the same items before either marks them as processing.

```php
public function get_due_items( $limit = 10 ) {
    // ... SELECT where status = 'pending' ...
}

public function mark_processing( $id ) {
    // ... UPDATE status = 'processing' ...
}
```

**Fix:** Use an atomic claim pattern. Either:
1. Use `UPDATE ... SET status = 'processing' WHERE status = 'pending' LIMIT N` and then `SELECT WHERE status = 'processing'`, or
2. Add a `locked_by` column with a unique process ID, or
3. Use a WP transient as a lock to prevent concurrent queue processing:

```php
public function process_queue() {
    if ( get_transient( 'wpojs_queue_lock' ) ) {
        return;
    }
    set_transient( 'wpojs_queue_lock', true, 60 );
    // ... process items ...
    delete_transient( 'wpojs_queue_lock' );
}
```

**Impact:** Double-processing causes duplicate API calls. Again mitigated by OJS idempotency, but this is a design flaw that should be fixed.

---

### C3. `mark_failed()` and `mark_permanent_fail()` read `attempts` with a separate query -- race condition

**File:** `includes/class-wpojs-queue.php`, lines 112-124, 130-143, 148-154

Both `mark_failed()` and `mark_permanent_fail()` call `$this->get_attempts($id)` (a separate SELECT) and then UPDATE with `attempts + 1`. This is a read-then-write race. If two processes handle the same item, the counter can be wrong.

```php
'attempts' => $this->get_attempts( $id ) + 1,
```

**Fix:** Use a SQL expression for the increment:

```php
$wpdb->query( $wpdb->prepare(
    "UPDATE {$this->table} SET status = %s, attempts = attempts + 1, next_retry_at = %s WHERE id = %d",
    'failed', $next_retry_at, $id
) );
```

This is atomic and doesn't require a separate read.

**Impact:** Combined with C2, incorrect attempt counts could cause items to either retry too many times or be permanently failed too early.

---

### C4. `on_subscription_inactive()` check for remaining active subscriptions may be unreliable during WCS status transition

**File:** `includes/class-wpojs-hooks.php`, lines 73-88

When a subscription is cancelled/expired/on-hold, the hook calls `$this->resolver->is_active_member()` to check if the user still has other active subscriptions. However, depending on when WCS fires the hook relative to updating the subscription status in the database, the subscription being cancelled might still appear as "active" in the `wcs_get_subscriptions()` query.

```php
public function on_subscription_inactive( $subscription ) {
    // ...
    if ( $this->resolver->is_active_member( $wp_user_id ) ) {
        return; // Still active via another sub — don't expire.
    }
    // ...
}
```

WCS hooks fire *after* the status change is saved, so the cancelled subscription should already be in its new state. However, if there are caching layers (object cache, WCS internal cache), the query might return stale data.

**Fix:** Pass the current subscription ID to `is_active_member()` and exclude it from the check:

```php
public function on_subscription_inactive( $subscription ) {
    $wp_user_id = $subscription->get_user_id();
    // ...
    if ( $this->resolver->is_active_member_excluding( $wp_user_id, $subscription->get_id() ) ) {
        return;
    }
    // ...
}
```

**Impact:** If the cancelled subscription is still seen as active (stale cache), the expire action is silently dropped. The user retains OJS access when they shouldn't. The daily reconciliation would eventually catch this, but there's a window of up to 24 hours.

---

## Important

### I1. `Logger::get_entries()` produces broken SQL when no filter conditions are set

**File:** `includes/class-wpojs-logger.php`, lines 108-120

When no filters are applied, `$values` contains only the `per_page` and `offset` values. The code checks `count($values) > 2` for the count query but doesn't handle the case where `$values` has exactly 2 entries correctly:

```php
if ( count( $values ) > 2 ) {
    $total = $wpdb->get_var( $wpdb->prepare( $count_sql, array_slice( $values, 0, -2 ) ) );
    $items = $wpdb->get_results( $wpdb->prepare( $sql, $values ) );
} else {
    $total = $wpdb->get_var( $count_sql );  // No prepare -- ok, no placeholders
    $items = $wpdb->get_results( $wpdb->prepare( $sql, $values ) );  // Has %d/%d for LIMIT/OFFSET
}
```

When `count($values) == 2` (no filters), the count query runs without prepare (fine, since `$count_sql` has no placeholders). But the `$sql` still has `LIMIT %d OFFSET %d` placeholders. This path works correctly.

However, there is a subtle issue: `$count_sql` includes `WHERE 1=1` but no placeholders, so running it without `prepare()` is safe. The actual bug is that `$wpdb->prepare()` in the else branch is called with the raw `$sql` that contains `{$this->table}` (table name interpolated at class property level, not via prepare). While `$this->table` comes from `$wpdb->prefix`, this is fine in practice. **No actual bug here on closer inspection, but the logic is fragile and hard to follow.** See I2.

### I2. `Queue::get_items()` passes `null` to `$wpdb->prepare()` when values array is empty

**File:** `includes/class-wpojs-queue.php`, lines 251-257

```php
if ( ! empty( $values ) ) {
    $total = $wpdb->get_var( $wpdb->prepare( $count_sql, array_slice( $values, 0, -2 ) ?: null ) );
```

When no filters are set but `$values` has the 2 LIMIT/OFFSET entries, `array_slice($values, 0, -2)` returns an empty array, then `?: null` converts it to `null`. Passing `null` to `$wpdb->prepare()` as the values parameter causes a PHP warning in newer WP versions (6.x) and can produce incorrect SQL.

**Fix:** Use the same pattern as `Logger::get_entries()` -- separate the LIMIT/OFFSET values from filter values:

```php
$filter_values = array_slice( $values, 0, -2 );
if ( ! empty( $filter_values ) ) {
    $total = $wpdb->get_var( $wpdb->prepare( $count_sql, $filter_values ) );
} else {
    $total = $wpdb->get_var( $count_sql );
}
$items = $wpdb->get_results( $wpdb->prepare( $sql, $values ) );
```

**Impact:** PHP warnings in logs, potential incorrect query results on the admin queue page.

---

### I3. `on_user_deleted()` hook fires after user is deleted -- `get_user_meta()` may fail

**File:** `includes/class-wpojs-hooks.php`, lines 126-147

The `deleted_user` hook fires *after* the user row and all usermeta have been deleted from the database. At this point, `get_user_meta($user_id, '_wpojs_user_id', true)` and `get_user_meta($user_id, '_wpojs_delete_email', true)` will return empty strings because the meta rows no longer exist.

The pre-delete hook `wpojs_pre_delete_user()` (line 154) tries to save the email to `_wpojs_delete_email` meta, but by the time `on_user_deleted()` fires, that meta has been deleted too.

```php
public function on_user_deleted( $user_id, $reassign = null ) {
    $ojs_user_id = get_user_meta( $user_id, '_wpojs_user_id', true ); // Always empty!
    $email = get_user_meta( $user_id, '_wpojs_delete_email', true );   // Always empty!
    // ...
}
```

**Fix:** Use the `delete_user` hook (fires before deletion) instead of `deleted_user`, and capture both the email and OJS user ID before the user is deleted. Or, store the captured data in a class property / static variable from `wpojs_pre_delete_user()` and read it in the `deleted_user` handler:

```php
function wpojs_pre_delete_user( $user_id ) {
    $user = get_userdata( $user_id );
    if ( $user ) {
        WPOJS_Hooks::$pending_deletions[ $user_id ] = array(
            'email'       => $user->user_email,
            'ojs_user_id' => get_user_meta( $user_id, '_wpojs_user_id', true ),
        );
    }
}
```

**Impact:** GDPR deletion of OJS accounts will always fail because the queue item will have `email = 'unknown-{id}@deleted.local'` and `ojs_user_id = null`. The `handle_delete_user()` method can still look up the user by the fallback email on OJS, but that email won't match anything. **GDPR erasure propagation is effectively broken.**

---

### I4. `handle_delete_user()` calls `delete_user_meta()` on an already-deleted user

**File:** `includes/class-wpojs-sync.php`, lines 173-189

After successfully deleting/anonymising the OJS user, the code calls `delete_user_meta( $item->wp_user_id, '_wpojs_user_id' )`. But the WP user was already deleted (this is the GDPR flow). `delete_user_meta()` on a non-existent user is a no-op, so it doesn't cause an error, but it's dead code.

```php
if ( $result['success'] ) {
    delete_user_meta( $item->wp_user_id, '_wpojs_user_id' ); // user already deleted
}
```

**Impact:** No functional impact, but indicates the GDPR flow hasn't been tested end-to-end. Combined with I3, the entire GDPR flow needs reworking.

---

### I5. `resolve_from_wcs()` always sets `date_start` to today, not the subscription's actual start date

**File:** `includes/class-wpojs-resolver.php`, line 100

```php
return array(
    'type_id'    => $type_id ?: $default_type,
    'date_start' => gmdate( 'Y-m-d' ),  // Always today
    'date_end'   => $date_end,
);
```

For bulk sync of existing members, this means the OJS subscription start date will be the date the sync ran, not when the member actually subscribed. For the OJS subscription upsert endpoint, this is used as the `dateStart` parameter.

The plan.md endpoint spec says: "Reactivation (expired->active): applies all incoming values (typeId, dateStart, dateEnd). Already active: extends dateEnd only if later." So for existing active subscriptions, the dateStart is ignored on OJS. But for new subscriptions and reactivations, the start date will be wrong.

**Fix:** For WCS subscriptions, use the subscription's actual start date:

```php
$date_start = $sub->get_date('start'); // Y-m-d H:i:s
if ($date_start) {
    $earliest_start = ($earliest_start === null || $date_start < $earliest_start) ? $date_start : $earliest_start;
}
```

For the initial sync this isn't critical (the subscription is being created fresh on OJS), but it means OJS admin will show inaccurate subscription start dates.

**Impact:** Cosmetic/audit issue. OJS admin shows wrong start dates. Could confuse support staff troubleshooting access issues.

---

### I6. Reconciliation is one-directional -- doesn't detect users who should be expired on OJS

**File:** `includes/class-wpojs-cron.php`, lines 65-119

The daily reconciliation only checks that active WP members have active OJS subscriptions. It does not check the reverse: users with active OJS subscriptions who are no longer active WP members.

```php
// Also check for OJS subscriptions that should be expired.
// This is handled by checking users who are NOT active members but have OJS subscriptions.
// For efficiency, we rely on the normal expire hooks for this.
// The reconciliation focuses on ensuring active WP members have active OJS subscriptions.
```

The comment acknowledges this explicitly. However, the plan.md spec says "daily reconciliation comparing active WCS subscriptions + manual member roles vs OJS, retrying any drift." The word "drift" implies bidirectional detection.

If an expire hook fails silently (e.g. queue item never created due to a PHP error, or the hook is temporarily deregistered), the user retains OJS access indefinitely until someone notices manually.

**Fix:** Add a reverse check: query `_wpojs_user_id` usermeta to find all synced users, then check which ones are no longer active members and queue expire actions for them. This doesn't require querying OJS -- it only needs local WP data:

```php
$synced_users = $wpdb->get_col("SELECT user_id FROM {$wpdb->usermeta} WHERE meta_key = '_wpojs_user_id'");
$active_set = array_flip($active_members);
foreach ($synced_users as $uid) {
    if (!isset($active_set[$uid])) {
        // This user was synced but is no longer active -- queue expire.
    }
}
```

**Impact:** Users who should have lost access retain it. The "WP is source of truth" invariant is violated until manual intervention.

---

### I7. Reconciliation makes one API call per active member -- will hammer OJS for ~700 members

**File:** `includes/class-wpojs-cron.php`, lines 70-104

The daily reconciliation loops through all ~700 active members and calls `GET /wpojs/subscriptions?email=...` for each one. With no delay between calls, this fires 700 HTTP requests at OJS in rapid succession.

```php
foreach ( $active_members as $wp_user_id ) {
    // ...
    $result = $this->api->get_subscriptions( array( 'email' => $email ) );
    // ...
}
```

Compare this to the CLI `reconcile` command which adds a 100ms delay (`usleep(100000)`) between calls.

**Fix:** Add a delay between API calls in the cron reconciliation, similar to the CLI version. Also consider batching (e.g. process 50 members per cron run rather than all 700 at once):

```php
foreach ( $active_members as $index => $wp_user_id ) {
    // ...
    usleep( 100000 ); // 100ms delay
}
```

**Impact:** Could overload OJS if it's on modest hardware. May cause timeouts, 429s, or service degradation during the reconciliation window.

---

### I8. `on_subscription_active()` resolves subscription data at hook time but it's also resolved again at processing time

**File:** `includes/class-wpojs-hooks.php`, lines 43-61 and `includes/class-wpojs-sync.php`, lines 69-111

The hook captures `$sub_data` (type_id, date_start, date_end) and stores it in the queue payload. But when the queue processor runs `handle_activate()`, it calls `$this->resolver->resolve_subscription_data()` again (line 96) and ignores the payload data entirely.

```php
// In hooks:
$sub_data = $this->resolver->resolve_subscription_data( $wp_user_id );
$payload = array( 'subscription_id' => $subscription->get_id() );
if ( $sub_data ) {
    $payload['type_id']    = $sub_data['type_id'];
    $payload['date_start'] = $sub_data['date_start'];
    $payload['date_end']   = $sub_data['date_end'];
}

// In sync processor -- ignores payload, re-resolves:
$sub_data = $this->resolver->resolve_subscription_data( $item->wp_user_id );
```

This means:
1. The payload data is wasted storage.
2. If the subscription state changes between queue time and processing time (e.g. within the retry window), the processor uses the latest state, not the state at event time.

The second point is actually correct behavior for most cases (you want the latest state), but it means the hook-time resolution is pointless work.

**Fix:** Remove the `resolve_subscription_data()` call from the hook and only store the `subscription_id` in the payload. Or, use the payload data in the processor and only re-resolve as a fallback. Be intentional about which approach is correct.

**Impact:** Minor performance waste. No functional bug because the processor re-resolves correctly. But the code is confusing about intent.

---

### I9. Settings page creates a new `WPOJS_Queue` instance instead of using the injected one

**File:** `includes/admin/class-wpojs-settings.php`, lines 264-265

```php
$queue = new WPOJS_Queue();
$stats = $queue->get_stats();
```

The settings page already has access to the API client via DI (`$this->api`), but creates a standalone Queue instance. This bypasses any future dependency injection improvements and creates unnecessary coupling.

**Fix:** Pass the `$queue` instance to `WPOJS_Settings` via the constructor, or accept this as minor coupling for a display-only feature.

**Impact:** Minor code quality issue. No functional bug.

---

### I10. `activate` handler sends `sendWelcomeEmail: true` for ALL queue-triggered activations, including renewals

**File:** `includes/class-wpojs-sync.php`, line 80

```php
$result = $this->api->find_or_create_user( $email, $first_name, $last_name, true );
```

The queue processor always sends `sendWelcomeEmail: true`. According to the OJS endpoint spec, this is only honoured when `created: true` (existing users already have a password). So for renewals (user already exists on OJS), it's a no-op on OJS.

However, the plan.md says "sendWelcomeEmail: true is only honoured when created: true" -- so the OJS side handles this correctly. But the WP code is sloppy about intent.

Compare to the CLI bulk sync which explicitly passes `false`:

```php
// Bulk sync does NOT send welcome emails
$result = $this->sync->sync_user( $wp_user_id, $dry_run, false );
```

**Impact:** No functional bug (OJS ignores it for existing users), but the code doesn't match the documented behavior pattern. The intent should be explicit.

---

### I11. `type_id` resolution takes the LAST matched product's type, not necessarily the correct one

**File:** `includes/class-wpojs-resolver.php`, lines 80-88

```php
foreach ( $subscriptions as $sub ) {
    // ...
    $items = $sub->get_items();
    foreach ( $items as $item ) {
        $product_id = $item->get_product_id();
        if ( isset( $type_mapping[ $product_id ] ) ) {
            $type_id = (int) $type_mapping[ $product_id ];
            break; // breaks inner loop only
        }
    }
}
```

The `break` only exits the inner `$items` loop, not the outer `$subscriptions` loop. So if a user has multiple active subscriptions with different products, `$type_id` will be overwritten by the last subscription's product mapping.

Since the plan says "All tiers grant the same OJS access," all products likely map to the same OJS type, making this a non-issue in practice. But if the mapping ever differentiates types, this logic is wrong.

**Fix:** Break out of the outer loop once a type_id is found, or document that the last-wins behavior is intentional. Alternatively, since all tiers grant the same access, just use `$default_type` always and skip the mapping lookup entirely in the resolver.

**Impact:** No functional bug given current requirements (all tiers map to same type). Would become a bug if type differentiation is added later.

---

## Minor

### M1. Plugin installed at wrong path

The plugin source is at `plugins/wpojs-sync/` (project root), but the plan and CLAUDE.md say it should be installed at `wordpress/web/app/plugins/wpojs-sync/`. The latter directory exists but is empty.

**Impact:** Deployment question. May be intentional (plugin developed separately, deployed via symlink or copy). Verify the deployment process includes copying the plugin to the correct WP plugins directory.

---

### M2. `$response_body` in `Logger::log()` is not sanitized for SQL storage

**File:** `includes/class-wpojs-logger.php`, lines 35-48

The `ojs_response_body` column receives data via `$wpdb->insert()` with `%s` format specifier, which is properly escaped by `$wpdb->prepare()` internally. So there's no SQL injection risk. However, the response body comes from OJS and is stored as-is (after truncation). It could contain HTML or scripts that would be rendered in the admin log page.

The log page does use `esc_html()` on the response body (line 167), but then puts the full unescaped body in the `title` attribute:

```php
return '<span title="' . esc_attr( $item->ojs_response_body ) . '">' . substr( $body, 0, 100 ) . '...</span>';
```

`esc_attr()` is the correct escaping for title attributes, so this is actually safe. No issue.

---

### M3. `sanitize_email()` in `Logger::log()` strips legitimate debug info

**File:** `includes/class-wpojs-logger.php`, line 39

```php
'email' => sanitize_email( $email ),
```

For the reconciliation log entry (line 115 of cron.php), the email is `'system'`, which `sanitize_email()` will strip to an empty string since it's not a valid email address.

**Fix:** Use `sanitize_text_field()` instead of `sanitize_email()` for the log table, since the email column is used for display/search, not for sending email.

**Impact:** System log entries have blank email fields, making them harder to filter in the admin log page.

---

### M4. No `dbDelta` version check for table schema upgrades

**File:** `includes/class-wpojs-activator.php`, lines 26-69

The `create_tables()` method runs `dbDelta()` on every activation but only stores a version number. There's no check like "if current version < new version, run migrations." For the initial release this is fine, but if table schema changes are needed in a future version, there's no migration framework.

**Fix:** Add a version comparison before running `dbDelta()`:

```php
$installed_version = get_option('wpojs_db_version', '0');
if (version_compare($installed_version, WPOJS_VERSION, '<')) {
    self::create_tables();
}
```

**Impact:** No current issue. Future-proofing.

---

### M5. `$_SERVER['SERVER_ADDR']` may not contain the outgoing IP

**File:** `includes/admin/class-wpojs-settings.php`, line 242

```php
$ip = isset( $_SERVER['SERVER_ADDR'] ) ? sanitize_text_field( $_SERVER['SERVER_ADDR'] ) : 'Unknown';
```

`$_SERVER['SERVER_ADDR']` is the server's listening address, which may differ from the outgoing IP seen by OJS (especially behind load balancers, NAT, or cloud hosting). The plan says this is "display only" to help the admin configure the OJS IP allowlist, but it could show the wrong IP.

**Fix:** Add a note in the UI: "This is the server's local address. The IP seen by OJS may differ if behind a load balancer or NAT. Test with 'Test Connection' to verify."

**Impact:** Admin configures wrong IP in OJS allowlist, connection test fails. Easily diagnosed but could cause confusion.

---

### M6. Cron schedule registration in activator runs during activation hook, but `add_filter` for `cron_schedules` may not be registered yet

**File:** `includes/class-wpojs-activator.php`, lines 72-87

The `schedule_cron()` method calls `add_filter('cron_schedules', ...)` inside itself, but this filter needs to be registered *before* `wp_schedule_event()` is called with the custom interval name `'every_minute'`. The code does register the filter first, so the order within the method is correct. Additionally, the filter is also registered at the bottom of the file (line 102) for runtime use. This is fine.

However, during activation, the filter registration via `add_filter()` in `schedule_cron()` is a runtime-only registration. If WP has already loaded the cron schedules before the activation hook fires, the custom schedule won't be available. In practice, `wp_schedule_event()` checks the schedule name against `wp_get_schedules()` which triggers the `cron_schedules` filter, so the runtime registration should work.

**Impact:** Edge case. Should work in practice. The dual registration (in `schedule_cron()` and at file bottom) is the correct pattern.

---

### M7. CLI `sync_bulk()` delays every single request, not just batch boundaries

**File:** `includes/cli/class-wpojs-cli.php`, lines 142-148

```php
if ( ! $dry_run && ( $index + 1 ) % $batch_size === 0 ) {
    WP_CLI::log( sprintf( '  Batch %d complete. Pausing...', ceil( ( $index + 1 ) / $batch_size ) ) );
}
if ( ! $dry_run ) {
    usleep( $delay_ms ); // 500ms on EVERY request
}
```

The 500ms delay runs on every single user, not just at batch boundaries. The batch message is logged every 50 users but the delay isn't any different. For 700 users at 500ms each, that's 350 seconds (about 6 minutes) of just sleeping, plus API call time.

The plan says "batched (50 users), 500ms delay" -- it's ambiguous whether the delay is per-user or per-batch. Per-user is more conservative but slow.

**Fix:** Move the delay inside the batch boundary check if per-batch delay is intended:

```php
if ( ! $dry_run && ( $index + 1 ) % $batch_size === 0 ) {
    WP_CLI::log( sprintf( '  Batch %d complete. Pausing...', ... ) );
    usleep( $delay_ms );
}
```

Or keep per-user delay but use a shorter interval (50-100ms).

**Impact:** Bulk sync takes longer than necessary. Plan estimates ~12 minutes for 700 users, which is roughly consistent with the current per-user delay approach (700 * 500ms sleep + 700 * ~500ms API call = ~700 seconds = ~12 minutes). So this may be intentional.

---

### M8. Log page response body column has potential XSS via `substr()` before `esc_html()`

**File:** `includes/admin/class-wpojs-log-page.php`, lines 167-169

```php
$body = esc_html( $item->ojs_response_body );
if ( strlen( $body ) > 100 ) {
    return '<span title="' . esc_attr( $item->ojs_response_body ) . '">' . substr( $body, 0, 100 ) . '...</span>';
}
```

`esc_html()` is applied to the full body first, then `strlen()` and `substr()` operate on the escaped string. This means the length check and truncation operate on the escaped version (which could be longer than the original due to entity encoding). If `esc_html()` produces a `&amp;` entity and the `substr()` cuts it mid-entity, the output will contain a broken HTML entity.

This isn't an XSS risk (the data is escaped), but it could produce garbled display.

**Fix:** Truncate first, then escape:

```php
$raw = $item->ojs_response_body;
$truncated = strlen($raw) > 100 ? substr($raw, 0, 100) . '...' : $raw;
return '<span title="' . esc_attr($raw) . '">' . esc_html($truncated) . '</span>';
```

**Impact:** Cosmetic. Occasionally garbled text in the admin log table.

---

### M9. `composer.json` is minimal -- no autoloading, no dependencies declared

**File:** `composer.json`

```json
{
  "name": "sea/wpojs-sync",
  "type": "wordpress-plugin",
  "description": "SEA OJS Sync — pushes WP membership changes to OJS",
  "version": "1.0.0"
}
```

No autoloading (`autoload`), no dependencies, no `require` section. The plugin uses manual `require_once` includes. This is fine for a simple plugin but means there's no Composer dependency management if it's ever needed.

**Impact:** None for current scope. The manual includes work fine.

---

### M10. `wpojs_pre_delete_user()` function is in class-wpojs-hooks.php but outside the class

**File:** `includes/class-wpojs-hooks.php`, lines 150-160

```php
function wpojs_pre_delete_user( $user_id ) {
    $user = get_userdata( $user_id );
    if ( $user ) {
        update_user_meta( $user_id, '_wpojs_delete_email', $user->user_email );
    }
}
add_action( 'delete_user', 'wpojs_pre_delete_user' );
```

This function and hook registration live outside the `WPOJS_Hooks` class, at the file's top level. It registers immediately when the file is loaded, regardless of whether `register()` is called. This works but is inconsistent with the rest of the architecture where hooks are registered explicitly via `register()` methods.

**Fix:** Move this into the `WPOJS_Hooks` class and register it in the `register()` method. Or at minimum, add it as a static method.

**Impact:** Code style inconsistency. The hook fires unconditionally when the file is loaded, but since the file is always loaded (via `require_once` in the bootstrap), this is functionally equivalent.

---

### M11. No uninstall hook to clean up database tables and options

The plugin has activation and deactivation hooks but no `uninstall.php` or `register_uninstall_hook()`. If the plugin is deleted from WP admin, the custom tables (`wpojs_sync_queue`, `wpojs_sync_log`) and options (`wpojs_url`, `wpojs_type_mapping`, etc.) remain in the database.

**Fix:** Create an `uninstall.php` file or register an uninstall hook that drops the tables and deletes the options.

**Impact:** Database clutter if the plugin is uninstalled. No functional issue while the plugin is active.

---

### M12. Missing text domain loading for i18n

The plugin uses `__()` and `esc_html_e()` with the `'wpojs-sync'` text domain (e.g., in `class-wpojs-dashboard.php`) but never calls `load_plugin_textdomain()`. Translation files would not be loaded.

**Fix:** Add to `wpojs_init()`:

```php
load_plugin_textdomain( 'wpojs-sync', false, dirname( plugin_basename( __FILE__ ) ) . '/languages' );
```

**Impact:** Translations won't work. Since the site is English-only, this has no current impact.

---

## Notes

### N1. Verify OJS subscription status constant

**Files:** `includes/class-wpojs-cron.php` line 92, `includes/cli/class-wpojs-cli.php` line 288

Both the cron reconciliation and CLI reconcile use `(int) $sub['status'] === 1` to check for active subscriptions. According to `docs/ojs-sync-plugin-api.md`, `1 = SUBSCRIPTION_STATUS_ACTIVE` is correct. Verify this matches the actual OJS response format from the `GET /wpojs/subscriptions` endpoint.

---

### N2. `$_SERVER['SERVER_ADDR']` availability varies by hosting environment

**File:** `includes/admin/class-wpojs-settings.php`, line 242

On some hosting environments (especially CLI or certain SAPI configurations), `$_SERVER['SERVER_ADDR']` may not be set. The code handles this with an `isset` check, so it will show "Unknown" in those cases. Consider using `gethostbyname(gethostname())` as a fallback.

---

### N3. WP Cron reliability

WP Cron is not a real cron -- it fires on page loads. If the site has low traffic, the queue processor may not fire every minute. For production, consider recommending a system cron to trigger WP Cron:

```
* * * * * wget -q -O /dev/null https://your-wp-site.example.org/wp-cron.php?doing_wp_cron
```

This is mentioned in the general WordPress guidance but should be part of the deployment checklist.

---

### N4. No rate limiting on admin alert emails

**File:** `includes/class-wpojs-sync.php`, lines 284-287

If many queue items fail simultaneously (e.g., OJS goes down and items are 400-erroring), each permanent failure sends a separate admin email. For 700 items failing at once, that's 700 emails.

Consider batching admin alerts or using a transient to rate-limit:

```php
private function send_admin_alert( $subject, $message ) {
    $throttle_key = 'wpojs_alert_' . md5($subject);
    if ( get_transient( $throttle_key ) ) {
        return; // Already sent recently.
    }
    set_transient( $throttle_key, true, 300 ); // 5 minute cooldown
    wp_mail( get_option('admin_email'), $subject, $message );
}
```

---

### N5. Bulk sync creates all OJS users with `$user->first_name ?: $user->display_name` fallback

**File:** `includes/class-wpojs-sync.php`, lines 76-77

```php
$first_name = $user->first_name ?: $user->display_name;
$last_name  = $user->last_name ?: '';
```

If a WP user has no first name set, their display name is used as the OJS first name. Display names can be "admin", "webmaster", or full names like "Jane Smith". This is a reasonable fallback but could produce odd-looking OJS accounts. The same logic appears in `sync_user()` (line 322).

---

### N6. `create_subscription()` always sends `dateEnd: null` explicitly when null

**File:** `includes/class-wpojs-api-client.php`, lines 71-82

```php
if ( $date_end !== null ) {
    $body['dateEnd'] = $date_end;
} else {
    $body['dateEnd'] = null;
}
```

The if/else is redundant -- both branches assign `$body['dateEnd']`. This could be simplified to:

```php
$body['dateEnd'] = $date_end;
```

The explicit `null` is fine (it signals non-expiring to OJS), but the branching is unnecessary.

---

### N7. Verify `wp_json_encode()` handles null correctly for `dateEnd`

**File:** `includes/class-wpojs-api-client.php`, line 189

`wp_json_encode( $body )` where `$body['dateEnd']` is PHP `null` will produce `"dateEnd": null` in JSON, which is the correct representation. The OJS endpoint expects `null` for non-expiring subscriptions. Verified correct.

---

### N8. Queue completed items accumulate indefinitely

The `wpojs_sync_queue` table has no cleanup mechanism for completed items. Over time, with 700+ members syncing and the queue running every minute, the table will grow. The log table has `cleanup_old()` (line 142 of `class-wpojs-logger.php`) but it's never called from anywhere in the code.

Consider adding a cleanup step to the daily cron or a CLI command to purge old completed items and old log entries.

---

### N9. API client constructs base_url from option on instantiation

**File:** `includes/class-wpojs-api-client.php`, lines 13-17

The `WPOJS_API_Client` reads `wpojs_url` once in the constructor. If the setting is changed during a request (e.g., via the settings page), existing instances won't pick up the change. The settings page's AJAX handler works around this by creating a fresh client (line 319), which is correct.

For the queue processor running in cron, the URL is read at plugin init time and stays constant for the entire request, which is the expected behavior.

---

### N10. `add_query_arg()` in API client's `get()` method may double-encode query parameters

**File:** `includes/class-wpojs-api-client.php`, lines 168-171

```php
if ( ! empty( $query_args ) ) {
    $url = add_query_arg( $query_args, $url );
}
```

`add_query_arg()` URL-encodes the values. If the email address contains special characters (e.g. `+`), it will be encoded correctly. This is fine.

---

## Summary

| Severity | Count |
|----------|-------|
| Critical | 4 |
| Important | 11 |
| Minor | 12 |
| Note | 10 |

### Overall assessment

The plugin is well-structured and follows WordPress conventions. The architecture is clean: proper dependency injection, separation of concerns (hooks/queue/processor/resolver/API client), correct use of WP APIs (`wp_remote_*`, `$wpdb->prepare()`, nonces, capability checks). The code matches the plan.md spec closely and covers all the documented hook-to-action mappings.

**Security is solid.** Nonce validation on AJAX and admin actions, `manage_options` capability checks on all admin pages, `$wpdb->prepare()` for all dynamic SQL, proper output escaping with `esc_html()` / `esc_attr()` / `esc_url()`, API key stored as a constant not in the database. No SQL injection or XSS vulnerabilities found.

**The main concerns are operational:**

1. **GDPR erasure is broken** (I3/I4). The `deleted_user` hook fires after user data is gone, so the queue item can never successfully delete the OJS account. This must be fixed before deploy.

2. **Queue concurrency has race conditions** (C1/C2/C3). The SELECT-then-INSERT dedup pattern and lack of processing locks mean duplicate work can happen under concurrent load. Mitigated by OJS endpoint idempotency, but should be hardened.

3. **Reconciliation is one-directional** (I6). Users who should lose access but whose expire hook failed silently will retain OJS access until manual intervention. Add reverse checking.

4. **Reconciliation hammers OJS** (I7). 700 sequential API calls with no delay in the cron handler. Add pacing.

5. **Subscription inactive hook may miss expires due to caching** (C4). The check for remaining active subscriptions should exclude the subscription being cancelled.

**Recommended deploy blockers:** C1-C4, I3, I6.

**Recommended pre-deploy fixes:** I1, I2, I5, I7, I8.

**Safe to defer:** Everything under Minor and Note.
