import { test, expect } from '@playwright/test';
import {
  createUser,
  deleteUser,
  createSubscription,
  deleteSubscription,
  getSubscriptionProductId,
  clearTestSyncData,
} from '../helpers/wp';
import {
  findOjsUser,
  hasActiveSubscription,
  isOjsUserDisabled,
  getOjsUserEmail,
  getSubscriptionStatus,
  deleteOjsUserById,
  waitForSync,
} from '../helpers/ojs';

const TS = Date.now();
const EMAIL = `e2e_delete_${TS}@test.invalid`;
const LOGIN = `e2e_delete_${TS}`;

let wpUserId: number;
let subId: number;
let productId: number;
let ojsUserId: number | null;

test.describe('User deletion / GDPR erasure', () => {
  test.beforeAll(() => {
    productId = getSubscriptionProductId();
    wpUserId = createUser(LOGIN, EMAIL);
    subId = createSubscription(wpUserId, productId, 'active');
    waitForSync();
    ojsUserId = findOjsUser(EMAIL);
  });

  test.afterAll(() => {
    // Delete by ID — email may have been anonymised by the test
    if (ojsUserId !== null) deleteOjsUserById(ojsUserId);
    clearTestSyncData();
  });

  test('delete WP user → OJS user anonymised and disabled', () => {
    // Verify initial sync
    const ojsUserId = findOjsUser(EMAIL);
    expect(ojsUserId).not.toBeNull();
    expect(hasActiveSubscription(ojsUserId!)).toBe(true);

    // Delete the WP user (fires delete_user + deleted_user hooks)
    deleteUser(wpUserId);
    waitForSync();

    // The OJS user should now be disabled
    expect(isOjsUserDisabled(ojsUserId!)).toBe(true);

    // Email should be anonymised
    const anonymisedEmail = getOjsUserEmail(ojsUserId!);
    expect(anonymisedEmail).toContain('anonymised.invalid');

    // Subscription should be expired (status 16 = OTHER)
    expect(getSubscriptionStatus(ojsUserId!)).toBe(16);
  });
});
