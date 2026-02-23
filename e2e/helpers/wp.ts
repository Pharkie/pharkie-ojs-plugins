import { type Page } from '@playwright/test';
import { dockerExec } from './docker';

/**
 * Run a WP-CLI command inside the wp container.
 */
export function wpCli(command: string): string {
  return dockerExec('wp', `wp ${command} --allow-root`);
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
    \$sub = wcs_create_subscription([
      'customer_id' => ${userId},
      'status' => '${status}',
      'billing_period' => 'year',
      'billing_interval' => 1,
      'start_date' => gmdate('Y-m-d H:i:s'),
    ]);
    if (is_wp_error(\$sub)) { echo 'ERROR:' . \$sub->get_error_message(); exit(1); }
    \$product = wc_get_product(${productId});
    \$item_id = \$sub->add_product(\$product, 1);
    echo \$sub->get_id();
  `.replace(/\n/g, ' ');

  const out = wpCli(`eval '${php}'`);
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
  wpCli(`eval '${php}'`);
}

/**
 * Get the first subscription product ID by SKU.
 */
export function getSubscriptionProductId(
  sku = 'wpojs-uk-no-listing',
): number {
  const out = wpCli(
    `eval 'echo wc_get_product_id_by_sku("${sku}");'`,
  );
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
  await page.goto('/wp/wp-login.php');
  await page.locator('#user_login').fill(username);
  await page.locator('#user_pass').fill(password);
  await page.locator('#wp-submit').click();
  await page.waitForURL(/wp-admin|my-account/);
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
  `.replace(/\n/g, ' ');
  const out = wpCli(`eval '${php}'`);
  return parseInt(out, 10);
}

/**
 * Delete log entries by email from wp_wpojs_sync_log.
 */
export function deleteLogEntries(email: string): void {
  const php = `
    global $wpdb;
    $wpdb->delete($wpdb->prefix . 'wpojs_sync_log', ['email' => '${email}'], ['%s']);
  `.replace(/\n/g, ' ');
  wpCli(`eval '${php}'`);
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
  `.replace(/\n/g, ' ');
  const out = wpCli(`eval '${php}'`);
  return parseInt(out, 10);
}
