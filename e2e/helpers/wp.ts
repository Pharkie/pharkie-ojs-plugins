import { type Page } from '@playwright/test';
import { dockerExec } from './docker';

/** Admin credentials — must match .env / setup-wp.sh. */
export const WP_ADMIN_USER = 'admin';
export function getAdminPassword(): string {
  const p = process.env.WP_ADMIN_PASSWORD;
  if (!p) throw new Error('WP_ADMIN_PASSWORD env var not set — required for admin login tests');
  return p;
}

/**
 * Run a WP-CLI command inside the wp container.
 */
export function wpCli(command: string): string {
  return dockerExec('wp', `wp ${command} --allow-root`);
}

/**
 * Evaluate PHP code inside the wp container.
 * Pipes PHP via stdin into a temp file, then runs wp eval-file on it.
 * This completely avoids shell quoting issues with embedded single quotes.
 */
export function wpEval(php: string): string {
  return dockerExec(
    'wp',
    'f=$(mktemp /tmp/_wpojs_eval_XXXXXX.php) && cat > "$f" && wp eval-file "$f" --allow-root; rm -f "$f"',
    { stdin: `<?php\n${php}` },
  );
}

/**
 * Create a WordPress user. Returns the user ID.
 */
export function createUser(
  login: string,
  email: string,
  opts: { firstName?: string; lastName?: string; role?: string } = {},
): number {
  const { firstName = 'E2E', lastName = 'Test', role = 'subscriber' } = opts;
  const out = wpCli(
    `user create ${login} ${email} --role=${role} --first_name=${firstName} --last_name=${lastName} --porcelain`,
  );
  return parseInt(out, 10);
}

/**
 * Delete a WordPress user by ID.
 */
export function deleteUser(userId: number): void {
  wpCli(`user delete ${userId} --yes`);
}

/**
 * Create a WCS subscription for a user via wcs_create_subscription().
 * Fires all WCS hooks (including our sync plugin). Returns subscription ID.
 */
export function createSubscription(
  userId: number,
  productId: number,
  status: 'active' | 'expired' = 'active',
): number {
  // wcs_create_subscription() creates the subscription and fires hooks.
  // We then add a line item for the product so the type mapping works.
  const php = `
    $sub = wcs_create_subscription([
      'customer_id' => ${userId},
      'status' => '${status}',
      'billing_period' => 'year',
      'billing_interval' => 1,
      'start_date' => gmdate('Y-m-d H:i:s'),
    ]);
    if (is_wp_error($sub)) { echo 'ERROR:' . $sub->get_error_message(); exit(1); }
    $product = wc_get_product(${productId});
    $item_id = $sub->add_product($product, 1);
    echo $sub->get_id();
  `;

  const out = wpEval(php);
  if (out.startsWith('ERROR:')) {
    throw new Error(`Failed to create subscription: ${out}`);
  }
  return parseInt(out, 10);
}

/**
 * Delete a WCS subscription by ID.
 */
export function deleteSubscription(subId: number): void {
  wpCli(`post delete ${subId} --force`);
}

/**
 * Update a WCS subscription status (e.g. to 'expired').
 */
export function updateSubscriptionStatus(
  subId: number,
  status: string,
): void {
  const php = `wcs_get_subscription(${subId})->update_status('${status}');`;
  wpEval(php);
}

/**
 * Get the first subscription product ID by SKU.
 */
export function getSubscriptionProductId(
  sku = 'wpojs-uk-no-listing',
): number {
  const out = wpEval(`echo wc_get_product_id_by_sku("${sku}");`);
  const id = parseInt(out, 10);
  if (!id) throw new Error(`Product not found for SKU: ${sku}`);
  return id;
}

/**
 * Log in to WordPress via the browser.
 */
export async function wpLogin(
  page: Page,
  username: string,
  password: string,
): Promise<void> {
  await page.goto('/wp/wp-login.php', { waitUntil: 'networkidle' });

  const loginField = page.locator('#user_login');
  const passField = page.locator('#user_pass');

  await loginField.waitFor({ state: 'visible' });

  // Disable autocomplete to prevent browser from overwriting values.
  await loginField.evaluate(el => el.setAttribute('autocomplete', 'off'));
  await passField.evaluate(el => el.setAttribute('autocomplete', 'off'));

  await loginField.fill(username);
  await passField.fill(password);

  // Submit and wait for navigation away from the login page.
  await Promise.all([
    page.waitForNavigation({ waitUntil: 'networkidle' }),
    page.locator('#wp-submit').click(),
  ]);

  // Verify we left the login page (subscriber users may redirect to homepage).
  if (/wp-login\.php/.test(page.url())) {
    throw new Error(`Login failed: still at ${page.url()}`);
  }
}

/**
 * Set a known password on a WP user (for browser login).
 */
export function setUserPassword(userId: number, password: string): void {
  wpCli(`user update ${userId} --user_pass=${password}`);
}

/**
 * Insert a fake row into wp_wpojs_sync_log (for testing UI without a real failure).
 * Returns the inserted row ID.
 */
export function insertLogEntry(
  email: string,
  action: string,
  status: 'success' | 'fail',
  opts: { responseCode?: number; responseBody?: string; wpUserId?: number } = {},
): number {
  const { responseCode = 500, responseBody = 'Test error', wpUserId = 0 } = opts;
  const php = `
    global $wpdb;
    $wpdb->insert(
      $wpdb->prefix . 'wpojs_sync_log',
      [
        'wp_user_id' => ${wpUserId},
        'email' => '${email}',
        'action' => '${action}',
        'status' => '${status}',
        'ojs_response_code' => ${responseCode},
        'ojs_response_body' => '${responseBody.replace(/'/g, "\\'")}',
        'attempt_count' => 1,
        'created_at' => current_time('mysql', true),
      ],
      ['%d','%s','%s','%s','%d','%s','%d','%s']
    );
    echo $wpdb->insert_id;
  `;
  const out = wpEval(php);
  return parseInt(out, 10);
}

/**
 * Delete log entries by email from wp_wpojs_sync_log.
 */
export function deleteLogEntries(email: string): void {
  const php = `
    global $wpdb;
    $wpdb->delete($wpdb->prefix . 'wpojs_sync_log', ['email' => '${email}'], ['%s']);
  `;
  wpEval(php);
}

/**
 * Delete sync log and Action Scheduler entries created by E2E tests.
 * Targets e2e_ prefixed emails, empty-email rows (from deleted-user hook firings),
 * and test domain addresses. Also cleans up failed Action Scheduler jobs.
 */
export function clearTestSyncData(): void {
  const php = `
    global $wpdb;
    $log = $wpdb->prefix . 'wpojs_sync_log';
    $wpdb->query("DELETE FROM {$log} WHERE email LIKE 'e2e_%'");
    $wpdb->query("DELETE FROM {$log} WHERE email = ''");
    $wpdb->query("DELETE FROM {$log} WHERE email LIKE '%test.example.com'");
    $wpdb->query("DELETE FROM {$log} WHERE email LIKE '%test.invalid'");
    // Clean up failed/pending Action Scheduler jobs from sync hooks
    $as = $wpdb->prefix . 'actionscheduler_actions';
    $wpdb->query("DELETE FROM {$as} WHERE hook LIKE 'wpojs_%' AND status = 'failed'");
    $wpdb->query("DELETE FROM {$as} WHERE hook LIKE 'wpojs_%' AND status = 'pending'");
  `;
  wpEval(php);
}

/**
 * Change a WP user's email address.
 * Fires the profile_update hook which our plugin listens on.
 */
export function changeUserEmail(userId: number, newEmail: string): void {
  const php = `
    wp_update_user([
      'ID' => ${userId},
      'user_email' => '${newEmail}',
    ]);
  `;
  wpEval(php);
}

/**
 * Get the OJS URL setting value.
 */
export function getOjsSetting(): string {
  return wpEval(`echo get_option('wpojs_url', '');`);
}

/**
 * Set the OJS URL setting value.
 */
export function setOjsSetting(url: string): void {
  wpEval(`update_option('wpojs_url', '${url}');`);
}

/**
 * Count Action Scheduler actions matching a hook and status.
 */
export function getActionSchedulerCount(
  hook: string,
  status: 'pending' | 'complete' | 'failed' = 'pending',
): number {
  const php = `
    global $wpdb;
    $table = $wpdb->prefix . 'actionscheduler_actions';
    echo (int) $wpdb->get_var($wpdb->prepare(
      "SELECT COUNT(*) FROM {$table} WHERE hook = %s AND status = %s",
      '${hook}', '${status}'
    ));
  `;
  const out = wpEval(php);
  return parseInt(out, 10);
}

/**
 * Run the daily reconciliation synchronously.
 * Fires the wpojs_daily_reconcile action which compares WP members vs OJS subs.
 * Extended timeout: reconciliation iterates all active WCS subscriptions.
 */
export function runReconciliation(): void {
  dockerExec(
    'wp',
    'f=$(mktemp /tmp/_wpojs_eval_XXXXXX.php) && cat > "$f" && wp eval-file "$f" --allow-root; rm -f "$f"',
    { stdin: "<?php\ndo_action('wpojs_daily_reconcile');", timeout: 120_000 },
  );
}

/**
 * Get a WP user meta value.
 */
export function getUserMeta(userId: number, key: string): string {
  return wpEval(`echo get_user_meta(${userId}, '${key}', true);`);
}
