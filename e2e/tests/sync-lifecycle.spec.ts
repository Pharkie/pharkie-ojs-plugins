import { test, expect } from '@playwright/test';
import {
  createUser,
  deleteUser,
  createSubscription,
  deleteSubscription,
  updateSubscriptionStatus,
  getSubscriptionProductId,
  clearTestSyncData,
} from '../helpers/wp';
import {
  findOjsUser,
  hasActiveSubscription,
  getSubscriptionStatus,
  deleteOjsUser,
  waitForSync,
} from '../helpers/ojs';

const EMAIL = `e2e_lifecycle_${Date.now()}@test.invalid`;
const LOGIN = `e2e_lifecycle_${Date.now()}`;

let wpUserId: number;
let subId: number;
let productId: number;

test.describe('WCS activate/expire → OJS subscription', () => {
  test.beforeAll(() => {
    productId = getSubscriptionProductId();
    wpUserId = createUser(LOGIN, EMAIL);
  });

  test.afterAll(() => {
    try { deleteSubscription(subId); } catch { /* may already be gone */ }
    try { deleteUser(wpUserId); } catch { /* may already be gone */ }
    deleteOjsUser(EMAIL);
    clearTestSyncData();
  });

  test('activate WCS subscription → OJS user + active subscription', () => {
    subId = createSubscription(wpUserId, productId, 'active');
    waitForSync();

    const ojsUserId = findOjsUser(EMAIL);
    expect(ojsUserId).not.toBeNull();
    expect(hasActiveSubscription(ojsUserId!)).toBe(true);
  });

  test('expire WCS subscription → OJS subscription expired (status 16)', () => {
    updateSubscriptionStatus(subId, 'expired');
    waitForSync();

    const ojsUserId = findOjsUser(EMAIL);
    expect(ojsUserId).not.toBeNull();
    expect(getSubscriptionStatus(ojsUserId!)).toBe(16);
  });
});
