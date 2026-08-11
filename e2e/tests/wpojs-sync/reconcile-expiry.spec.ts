import { test, expect } from '@playwright/test';
import {
  createUser,
  addUserRole,
  removeUserRole,
  countQueuedActionsForUser,
  runReconciliation,
  pruneQueueExcept,
  cleanupWpUser,
  wpEval,
} from '../../helpers/wp';
import {
  findAndVerifyOjsUser,
  hasActiveSubscription,
  deleteOjsUser,
  waitForSync,
} from '../../helpers/ojs';

const TS = Date.now();

/**
 * Reconciliation expires stale access ONCE, not every day for ever.
 *
 * `_wpojs_user_id` stays on a WP user after they lapse, so the stale-access
 * check kept re-detecting the same people and re-expiring an already-expired
 * OJS subscription: on live, 25 no-op calls a day, 713 in a month, burying the
 * real expiries in the log (2026-08-11).
 */
test.describe('Reconciliation expiry is not repeated', () => {
  const PREFIX = `e2e_reexp_${TS}`;
  const MANUAL_ROLE = 'um_custom_role_7'; // in wpojs_manual_roles on the rig

  test('a lapsed member is expired on the first reconciliation and left alone on the next', () => {
    const email = `${PREFIX}@test.invalid`;
    let wpUserId: number;

    try {
      wpUserId = createUser(`${PREFIX}`, email);
      addUserRole(wpUserId, MANUAL_ROLE);
      wpEval(`as_schedule_single_action(time(), 'wpojs_sync_activate', [['wp_user_id' => ${wpUserId}]], 'wpojs-sync');`);
      waitForSync();

      const { userId: ojsUserId, hasActive } = findAndVerifyOjsUser(email);
      expect(ojsUserId).not.toBeNull();
      expect(hasActive).toBe(true);

      // They lapse.
      removeUserRole(wpUserId, MANUAL_ROLE);

      // First run: they still have OJS access, so it must be withdrawn.
      runReconciliation();
      expect(countQueuedActionsForUser('wpojs_sync_expire', wpUserId)).toBe(1);
      pruneQueueExcept(wpUserId);
      waitForSync(120_000);
      expect(hasActiveSubscription(ojsUserId!)).toBe(false);

      // Second run: nothing left to withdraw. This is the bug — it used to
      // queue another expire here, and again every day after that.
      runReconciliation();
      expect(countQueuedActionsForUser('wpojs_sync_expire', wpUserId)).toBe(0);
    } finally {
      cleanupWpUser({ wpUserId: wpUserId! });
      deleteOjsUser(email);
    }
  });
});
