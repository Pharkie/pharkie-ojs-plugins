import { dockerExec } from './docker';

/**
 * Run a SQL query against the OJS database. Returns stdout.
 * Uses stdin to avoid shell quoting issues with SQL containing special chars.
 */
export function ojsQuery(sql: string): string {
  return dockerExec(
    'ojs-db',
    'mariadb -u ojs -pdevpass123 ojs -N',
    { stdin: sql },
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
 * Run a PHP script inside the OJS container via stdin (avoids shell quoting).
 * The PHP code must include the <?php tag.
 */
export function ojsPhp(php: string, timeout = 15_000): string {
  return dockerExec(
    'ojs',
    'f=$(mktemp /tmp/_ojs_eval_XXXXXX.php) && cat > "$f" && php "$f"; rm -f "$f"',
    { stdin: php, timeout },
  );
}

/**
 * Generate a password reset hash for an OJS user via PHP inside the container.
 */
export function getPasswordResetHash(userId: number): string {
  // Set the datePasswordResetRequested so the hash is valid.
  ojsQuery(
    `UPDATE users SET date_password_reset_requested = NOW() WHERE user_id = ${userId}`,
  );
  const hash = ojsPhp(`<?php
require_once('/var/www/html/tools/bootstrap.php');
echo PKP\\security\\Validation::generatePasswordResetHash(${userId});
`);
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
  ojsPhp(`<?php
require_once('/var/www/html/tools/bootstrap.php');
$hash = PKP\\security\\Validation::encryptCredentials('${username}', '${password}');
Illuminate\\Support\\Facades\\DB::table('users')->where('user_id', ${userId})->update(['password' => $hash, 'must_change_password' => 0]);
`);
}

/**
 * Delete an OJS user by ID — cascading cleanup.
 */
export function deleteOjsUserById(userId: number): void {
  ojsQuery(`DELETE FROM subscriptions WHERE user_id = ${userId}`);
  ojsQuery(`DELETE FROM user_settings WHERE user_id = ${userId}`);
  ojsQuery(`DELETE FROM user_user_groups WHERE user_id = ${userId}`);
  ojsQuery(`DELETE FROM users WHERE user_id = ${userId}`);
}

/**
 * Delete an OJS user by email — cascading cleanup.
 */
export function deleteOjsUser(email: string): void {
  const userId = findOjsUser(email);
  if (userId === null) return;
  deleteOjsUserById(userId);
}

/**
 * Run Action Scheduler to process pending sync actions.
 * @param timeout - ms to wait (default 30s; use longer after reconciliation)
 */
export function waitForSync(timeout = 30_000): void {
  dockerExec(
    'wp',
    'wp action-scheduler run --allow-root 2>/dev/null',
    { timeout },
  );
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

/**
 * Get the email address for an OJS user by user_id.
 */
export function getOjsUserEmail(userId: number): string | null {
  const out = ojsQuery(
    `SELECT email FROM users WHERE user_id = ${userId}`,
  );
  return out.trim() || null;
}

/**
 * Check if an OJS user account is disabled.
 */
export function isOjsUserDisabled(userId: number): boolean {
  const out = ojsQuery(
    `SELECT disabled FROM users WHERE user_id = ${userId}`,
  );
  return parseInt(out, 10) === 1;
}

/**
 * Get the username for an OJS user by user_id.
 */
export function getOjsUserUsername(userId: number): string | null {
  const out = ojsQuery(
    `SELECT username FROM users WHERE user_id = ${userId}`,
  );
  return out.trim() || null;
}

/**
 * Delete an OJS subscription directly (bypass API — for creating test drift).
 */
export function deleteOjsSubscription(userId: number): void {
  ojsQuery(`DELETE FROM subscriptions WHERE user_id = ${userId}`);
}

/**
 * Make a direct HTTP call to the OJS API (for validation tests).
 * Uses curl inside the wp container (has network access to OJS).
 * Reads OJS base URL and API key from WP config.
 */
export function ojsApiCall(
  method: string,
  path: string,
  body?: object,
): { status: number; body: any } {
  // Read OJS URL and API key from WP in a single eval
  const config = dockerExec(
    'wp',
    "wp eval 'echo get_option(\"wpojs_url\", \"\") . \"\\n\" . (defined(\"WPOJS_API_KEY\") ? WPOJS_API_KEY : \"\");' --allow-root 2>/dev/null | tail -2",
    { timeout: 10_000 },
  ).trim();
  const [baseUrl, apiKey] = config.split('\n');

  const apiUrl = `${baseUrl}/api/v1${path}`;

  const curlCmd = [
    'curl -s',
    `-X ${method}`,
    `-H 'Authorization: Bearer ${apiKey}'`,
    body ? "-H 'Content-Type: application/json'" : '',
    body ? `-d '${JSON.stringify(body).replace(/'/g, "'\\''")}'` : '',
    `-w '\\n%{http_code}'`,
    `'${apiUrl}'`,
  ]
    .filter(Boolean)
    .join(' ');

  const out = dockerExec('wp', curlCmd, { timeout: 15_000 });
  const lines = out.trimEnd().split('\n');
  const statusCode = parseInt(lines.pop()!, 10);
  const responseBody = lines.join('\n');

  let parsed: any;
  try {
    parsed = JSON.parse(responseBody);
  } catch {
    parsed = responseBody;
  }

  return { status: statusCode, body: parsed };
}
