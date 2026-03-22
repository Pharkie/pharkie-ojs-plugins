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
  cleanupWpUser,
  createUserWithSubscription,
} from '../../helpers/wp';
import {
  findOjsUser,
  hasActiveSubscription,
  findAndVerifyOjsUser,
  deleteOjsUser,
  waitForSync,
} from '../../helpers/ojs';

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
    cleanupWpUser({ subIds: [subId], wpUserId });
    deleteOjsUser(EMAIL);
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
    const { userId: ojsUserId, hasActive } = findAndVerifyOjsUser(EMAIL);
    expect(ojsUserId).not.toBeNull();
    expect(hasActive).toBe(true);
  });
});

test.describe('Error recovery: transient failure with sync log verification', () => {
  const RETRY_EMAIL = `e2e_retry_${TS}@test.invalid`;
  const RETRY_LOGIN = `e2e_retry_${TS}`;
  let retryWpUserId: number;
  let retrySubId: number;
  let retryProductId: number;
  let retryOriginalUrl: string;

  test.beforeAll(() => {
    retryProductId = getSubscriptionProductId();
    retryOriginalUrl = getOjsSetting();
  });

  test.afterAll(() => {
    // Always restore the correct OJS URL (critical — other tests break if left bad)
    setOjsSetting(retryOriginalUrl);
    cleanupWpUser({ subIds: [retrySubId], wpUserId: retryWpUserId });
    deleteOjsUser(RETRY_EMAIL);
  });

  test('retries after transient OJS failure and eventually succeeds', () => {
    // 1. Break the OJS URL to simulate network failure
    setOjsSetting('http://localhost:19999');

    // 2. Create user + subscription (queues sync action)
    ({ wpUserId: retryWpUserId, subId: retrySubId } =
      createUserWithSubscription(RETRY_LOGIN, RETRY_EMAIL, retryProductId));

    // 3. Process queue — should fail
    waitForSync();

    // 4. Verify sync log shows a failure
    const failCount = wpEval(`
      global $wpdb;
      $log = $wpdb->prefix . 'wpojs_sync_log';
      echo (int) $wpdb->get_var($wpdb->prepare(
        "SELECT COUNT(*) FROM {$log} WHERE email = %s AND status = 'fail'",
        '${RETRY_EMAIL}'
      ));
    `);
    expect(parseInt(failCount, 10)).toBeGreaterThanOrEqual(1);

    // 5. OJS user should NOT exist yet
    expect(findOjsUser(RETRY_EMAIL)).toBeNull();

    // 6. Restore the correct OJS URL
    setOjsSetting(retryOriginalUrl);

    // 7. Schedule a fresh retry action (simulating reconciliation or manual retry)
    wpEval(`
      as_schedule_single_action(time(), 'wpojs_sync_activate', [['wp_user_id' => ${retryWpUserId}]], 'wpojs-sync');
    `);

    // 8. Process queue — should succeed now
    waitForSync();

    // 9. Verify OJS user exists with active subscription
    const { userId: ojsUserId, hasActive } = findAndVerifyOjsUser(RETRY_EMAIL);
    expect(ojsUserId).not.toBeNull();
    expect(hasActive).toBe(true);

    // 10. Verify sync log shows a success after the failure
    const successCount = wpEval(`
      global $wpdb;
      $log = $wpdb->prefix . 'wpojs_sync_log';
      echo (int) $wpdb->get_var($wpdb->prepare(
        "SELECT COUNT(*) FROM {$log} WHERE email = %s AND status = 'success'",
        '${RETRY_EMAIL}'
      ));
    `);
    expect(parseInt(successCount, 10)).toBeGreaterThanOrEqual(1);
  });
});
