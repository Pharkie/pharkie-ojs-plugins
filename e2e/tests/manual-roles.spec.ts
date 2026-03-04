import { test, expect } from '@playwright/test';
import {
  createUser,
  deleteUser,
  createSubscription,
  deleteSubscription,
  getSubscriptionProductId,
  clearTestSyncData,
  wpEval,
  addUserRole,
  removeUserRole,
  runReconciliation,
  pruneQueueExcept,
} from '../helpers/wp';
import {
  findOjsUser,
  hasActiveSubscription,
  deleteOjsUser,
  waitForSync,
  ojsQuery,
} from '../helpers/ojs';

const TS = Date.now();

/**
 * Manual role-based access: users with specific WP roles get OJS access
 * without needing a WooCommerce Subscription.
 *
 * Manual roles don't fire WCS hooks, so we must explicitly schedule the
 * activate action via Action Scheduler (just like the bulk sync or
 * reconciliation would).
 */
test.describe('Manual role-based access', () => {
  const PREFIX = `e2e_mrole_${TS}`;

  // The dev environment has um_custom_role_7 in wpojs_manual_roles.
  // We use this role for testing.
  const MANUAL_ROLE = 'um_custom_role_7';

  test('user with manual role (no WCS sub) gets OJS access', () => {
    const email = `${PREFIX}_roleonly@test.invalid`;
    const login = `${PREFIX}_roleonly`;
    let wpUserId: number;

    try {
      wpUserId = createUser(login, email);
      addUserRole(wpUserId, MANUAL_ROLE);

      // Manual role doesn't fire WCS hooks — schedule activate explicitly.
      wpEval(`
        as_schedule_single_action(time(), 'wpojs_sync_activate', [['wp_user_id' => ${wpUserId}]], 'wpojs-sync');
      `);
      waitForSync();

      const ojsUserId = findOjsUser(email);
      expect(ojsUserId).not.toBeNull();
      expect(hasActiveSubscription(ojsUserId!)).toBe(true);
    } finally {
      try { deleteUser(wpUserId!); } catch { /* ok */ }
      deleteOjsUser(email);
      clearTestSyncData();
    }
  });

  test('manual role creates non-expiring subscription (date_end is NULL)', () => {
    const email = `${PREFIX}_nonexp@test.invalid`;
    const login = `${PREFIX}_nonexp`;
    let wpUserId: number;

    try {
      wpUserId = createUser(login, email);
      addUserRole(wpUserId, MANUAL_ROLE);

      wpEval(`
        as_schedule_single_action(time(), 'wpojs_sync_activate', [['wp_user_id' => ${wpUserId}]], 'wpojs-sync');
      `);
      waitForSync();

      const ojsUserId = findOjsUser(email);
      expect(ojsUserId).not.toBeNull();

      // Manual roles are always non-expiring — date_end should be NULL.
      const dateEnd = ojsQuery(
        `SELECT date_end FROM subscriptions WHERE user_id = ${ojsUserId} ORDER BY subscription_id DESC LIMIT 1`,
      ).trim();
      // NULL in MySQL CLI comes back as empty string
      expect(dateEnd === '' || dateEnd === 'NULL').toBe(true);
    } finally {
      try { deleteUser(wpUserId!); } catch { /* ok */ }
      deleteOjsUser(email);
      clearTestSyncData();
    }
  });

  test('both WCS sub and manual role — date_end overridden to null', () => {
    const email = `${PREFIX}_both@test.invalid`;
    const login = `${PREFIX}_both`;
    let wpUserId: number;
    let subId: number;
    const productId = getSubscriptionProductId();

    try {
      wpUserId = createUser(login, email);

      // Create WCS subscription (has an end date)
      subId = createSubscription(wpUserId, productId, 'active');
      wpEval(`
        $sub = wcs_get_subscription(${subId});
        $sub->update_dates(['end' => '2028-12-31 00:00:00']);
      `);

      // Also add manual role
      addUserRole(wpUserId, MANUAL_ROLE);

      waitForSync();

      const ojsUserId = findOjsUser(email);
      expect(ojsUserId).not.toBeNull();
      expect(hasActiveSubscription(ojsUserId!)).toBe(true);

      // Manual role is non-expiring and overrides WCS date_end to null.
      const dateEnd = ojsQuery(
        `SELECT date_end FROM subscriptions WHERE user_id = ${ojsUserId} ORDER BY subscription_id DESC LIMIT 1`,
      ).trim();
      expect(dateEnd === '' || dateEnd === 'NULL').toBe(true);
    } finally {
      try { deleteSubscription(subId!); } catch { /* ok */ }
      try { deleteUser(wpUserId!); } catch { /* ok */ }
      deleteOjsUser(email);
      clearTestSyncData();
    }
  });

  test('WCS sub expires but manual role keeps access alive', () => {
    const email = `${PREFIX}_keepalive@test.invalid`;
    const login = `${PREFIX}_keepalive`;
    let wpUserId: number;
    let subId: number;
    const productId = getSubscriptionProductId();

    try {
      wpUserId = createUser(login, email);
      addUserRole(wpUserId, MANUAL_ROLE);
      subId = createSubscription(wpUserId, productId, 'active');
      waitForSync();

      const ojsUserId = findOjsUser(email);
      expect(ojsUserId).not.toBeNull();
      expect(hasActiveSubscription(ojsUserId!)).toBe(true);

      // Expire the WCS subscription — the hooks check is_active_member()
      // which returns true because of the manual role, so expire is skipped.
      wpEval(`
        wcs_get_subscription(${subId})->update_status('expired');
      `);
      waitForSync();

      // OJS subscription should still be active (manual role keeps it alive).
      expect(hasActiveSubscription(ojsUserId!)).toBe(true);
    } finally {
      try { deleteSubscription(subId!); } catch { /* ok */ }
      try { deleteUser(wpUserId!); } catch { /* ok */ }
      deleteOjsUser(email);
      clearTestSyncData();
    }
  });

  test('remove manual role → reconciliation detects stale access', () => {
    const email = `${PREFIX}_remove@test.invalid`;
    const login = `${PREFIX}_remove`;
    let wpUserId: number;

    try {
      wpUserId = createUser(login, email);
      addUserRole(wpUserId, MANUAL_ROLE);

      // Schedule activate and sync
      wpEval(`
        as_schedule_single_action(time(), 'wpojs_sync_activate', [['wp_user_id' => ${wpUserId}]], 'wpojs-sync');
      `);
      waitForSync();

      const ojsUserId = findOjsUser(email);
      expect(ojsUserId).not.toBeNull();
      expect(hasActiveSubscription(ojsUserId!)).toBe(true);

      // Remove the manual role — user is no longer an active member.
      removeUserRole(wpUserId, MANUAL_ROLE);

      // Run reconciliation to detect stale access.
      runReconciliation();
      pruneQueueExcept(wpUserId);
      waitForSync(120_000);

      // OJS subscription should be expired (status 16).
      expect(hasActiveSubscription(ojsUserId!)).toBe(false);
    } finally {
      try { deleteUser(wpUserId!); } catch { /* ok */ }
      deleteOjsUser(email);
      clearTestSyncData();
    }
  });
});
