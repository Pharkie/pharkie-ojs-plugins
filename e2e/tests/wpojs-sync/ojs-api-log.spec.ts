import { test, expect } from '@playwright/test';
import {
  createUser,
  deleteUser,
  createSubscription,
  deleteSubscription,
  getSubscriptionProductId,
  clearTestSyncData,
  createUserWithSubscription,
  cleanupWpUser,
} from '../../helpers/wp';
import {
  findOjsUser,
  deleteOjsUser,
  waitForSync,
  getOjsApiLogCount,
  getOjsUserSetting,
  clearOjsApiLog,
  ojsQuery,
} from '../../helpers/ojs';

const TS = Date.now();
const EMAIL = `e2e_apilog_${TS}@test.invalid`;
const LOGIN = `e2e_apilog_${TS}`;

let wpUserId: number;
let subId: number;
let productId: number;

test.describe('OJS API request logging', () => {
  test.beforeAll(() => {
    productId = getSubscriptionProductId();
    clearOjsApiLog();

    ({ wpUserId, subId } = createUserWithSubscription(LOGIN, EMAIL, productId));
    waitForSync();
  });

  test.afterAll(() => {
    cleanupWpUser({ subIds: [subId], wpUserId });
    deleteOjsUser(EMAIL);
    clearOjsApiLog();
  });

  test('API requests logged after sync', () => {
    const count = getOjsApiLogCount();
    expect(count).toBeGreaterThan(0);

    // At least one successful request logged
    const successCount = parseInt(
      ojsQuery(
        "SELECT COUNT(*) FROM wpojs_api_log WHERE http_status = 200",
      ),
      10,
    );
    expect(successCount).toBeGreaterThan(0);
  });

  test('wpojs_created_by_sync flag set on new user', () => {
    const ojsUserId = findOjsUser(EMAIL);
    expect(ojsUserId).not.toBeNull();

    const createdBySync = getOjsUserSetting(EMAIL, 'wpojs_created_by_sync');
    expect(createdBySync).not.toBeNull();

    // Value should be a datetime-like string (e.g. "2026-02-23 12:34:56")
    expect(createdBySync).toMatch(/^\d{4}-\d{2}-\d{2}/);
  });
});
