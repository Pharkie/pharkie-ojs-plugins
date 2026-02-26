import { test, expect } from '@playwright/test';
import {
  createUser,
  deleteUser,
  createSubscription,
  deleteSubscription,
  getSubscriptionProductId,
  getOjsSetting,
  setOjsSetting,
  clearTestSyncData,
  wpEval,
} from '../helpers/wp';
import {
  findOjsUser,
  hasActiveSubscription,
  deleteOjsUser,
  waitForSync,
} from '../helpers/ojs';

const TS = Date.now();
const EMAIL = `e2e_recovery_${TS}@test.invalid`;
const LOGIN = `e2e_recovery_${TS}`;

let wpUserId: number;
let subId: number;
let productId: number;
let originalOjsUrl: string;

test.describe('Error recovery: OJS unreachable → retry succeeds', () => {
  test.beforeAll(() => {
    productId = getSubscriptionProductId();
    originalOjsUrl = getOjsSetting();
  });

  test.afterAll(() => {
    // Always restore the original OJS URL
    setOjsSetting(originalOjsUrl);
    try { deleteSubscription(subId); } catch { /* may already be gone */ }
    try { deleteUser(wpUserId); } catch { /* may already be gone */ }
    deleteOjsUser(EMAIL);
    clearTestSyncData();
  });

  test('sync fails when OJS URL is bad, succeeds after restoring', () => {
    // Point OJS URL to a bad endpoint to simulate unreachability
    setOjsSetting('http://localhost:19999');

    // Create user and subscription (hooks fire, queue sync action)
    wpUserId = createUser(LOGIN, EMAIL);
    subId = createSubscription(wpUserId, productId, 'active');

    // Process queue — should fail because OJS is "unreachable"
    waitForSync();

    // OJS user should NOT exist (sync failed)
    expect(findOjsUser(EMAIL)).toBeNull();

    // Restore the correct OJS URL
    setOjsSetting(originalOjsUrl);

    // Action Scheduler defers retries into the future (5+ min), so the failed
    // action won't re-run immediately. Schedule a fresh activate action to
    // simulate what reconciliation or a manual retry would do.
    wpEval(`
      as_schedule_single_action(time(), 'wpojs_sync_activate', [['wp_user_id' => ${wpUserId}]], 'wpojs-sync');
    `);

    // Process queue — the fresh action should succeed with the restored URL
    waitForSync();

    // Now the OJS user should exist with an active subscription
    const ojsUserId = findOjsUser(EMAIL);
    expect(ojsUserId).not.toBeNull();
    expect(hasActiveSubscription(ojsUserId!)).toBe(true);
  });
});
