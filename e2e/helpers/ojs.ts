import { dockerExec } from './docker';

/**
 * Run a SQL query against the OJS database. Returns stdout.
 */
export function ojsQuery(sql: string): string {
  return dockerExec(
    'ojs-db',
    `mariadb -u ojs -pdevpass123 ojs -N -e "${sql.replace(/"/g, '\\"')}"`,
  );
}

/**
 * Find an OJS user by email. Returns userId or null.
 */
export function findOjsUser(email: string): number | null {
  const out = ojsQuery(
    `SELECT user_id FROM users WHERE email = '${email}' LIMIT 1`,
  );
  const id = parseInt(out, 10);
  return isNaN(id) ? null : id;
}

/**
 * Check if a user has an active OJS subscription (status = 1).
 */
export function hasActiveSubscription(userId: number): boolean {
  const out = ojsQuery(
    `SELECT COUNT(*) FROM subscriptions WHERE user_id = ${userId} AND status = 1`,
  );
  return parseInt(out, 10) > 0;
}

/**
 * Get the subscription status for a user. Returns status int or null.
 */
export function getSubscriptionStatus(userId: number): number | null {
  const out = ojsQuery(
    `SELECT status FROM subscriptions WHERE user_id = ${userId} ORDER BY subscription_id DESC LIMIT 1`,
  );
  const s = parseInt(out, 10);
  return isNaN(s) ? null : s;
}

/**
 * Get the OJS username for a user ID.
 */
export function getOjsUsername(userId: number): string {
  return ojsQuery(
    `SELECT username FROM users WHERE user_id = ${userId}`,
  ).trim();
}

/**
 * Generate a password reset hash for an OJS user via PHP inside the container.
 */
export function getPasswordResetHash(userId: number): string {
  // Set the datePasswordResetRequested so the hash is valid.
  ojsQuery(
    `UPDATE users SET date_password_reset_requested = NOW() WHERE user_id = ${userId}`,
  );
  const hash = dockerExec(
    'ojs',
    `php -r "
      require_once('/var/www/html/tools/bootstrap.php');
      echo PKP\\\\security\\\\Validation::generatePasswordResetHash(${userId});
    "`,
    { timeout: 15_000 },
  );
  return hash.trim();
}

/**
 * Set a known password for an OJS user.
 * Also clears must_change_password so login doesn't redirect to password change form.
 */
export function setOjsPassword(
  userId: number,
  username: string,
  password: string,
): void {
  dockerExec(
    'ojs',
    `php -r "
      require_once('/var/www/html/tools/bootstrap.php');
      \\\$hash = PKP\\\\security\\\\Validation::encryptCredentials('${username}', '${password}');
      Illuminate\\\\Support\\\\Facades\\\\DB::table('users')->where('user_id', ${userId})->update(['password' => \\\$hash, 'must_change_password' => 0]);
    "`,
    { timeout: 15_000 },
  );
}

/**
 * Delete an OJS user by email — cascading cleanup.
 */
export function deleteOjsUser(email: string): void {
  const userId = findOjsUser(email);
  if (userId === null) return;
  ojsQuery(`DELETE FROM subscriptions WHERE user_id = ${userId}`);
  ojsQuery(`DELETE FROM user_settings WHERE user_id = ${userId}`);
  ojsQuery(`DELETE FROM user_user_groups WHERE user_id = ${userId}`);
  ojsQuery(`DELETE FROM users WHERE user_id = ${userId}`);
}

/**
 * Run Action Scheduler to process pending sync actions.
 */
export function waitForSync(): void {
  dockerExec(
    'wp',
    'wp action-scheduler run --allow-root',
    { timeout: 30_000 },
  );
  // Small buffer for OJS API to finish processing.
}

/**
 * Count rows in the OJS wpojs_api_log table.
 */
export function getOjsApiLogCount(): number {
  const out = ojsQuery('SELECT COUNT(*) FROM wpojs_api_log');
  return parseInt(out, 10) || 0;
}

/**
 * Query a user_settings value by email and setting name.
 * Returns the setting value or null if not found.
 */
export function getOjsUserSetting(
  email: string,
  settingName: string,
): string | null {
  const out = ojsQuery(
    `SELECT us.setting_value FROM user_settings us JOIN users u ON u.user_id = us.user_id WHERE u.email = '${email}' AND us.setting_name = '${settingName}' LIMIT 1`,
  );
  return out.trim() || null;
}

/**
 * Clear all rows from the OJS wpojs_api_log table.
 */
export function clearOjsApiLog(): void {
  ojsQuery('DELETE FROM wpojs_api_log');
}
