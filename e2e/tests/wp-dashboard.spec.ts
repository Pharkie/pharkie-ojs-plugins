import { test, expect } from '@playwright/test';
import {
  createUser,
  deleteUser,
  createSubscription,
  deleteSubscription,
  getSubscriptionProductId,
  setUserPassword,
  wpLogin,
  clearTestSyncData,
} from '../helpers/wp';
import { deleteOjsUser, waitForSync } from '../helpers/ojs';

const WP_PASSWORD = 'TestPass123!';

test.describe('WP My Account journal access widget', () => {
  let productId: number;

  test.beforeAll(() => {
    productId = getSubscriptionProductId();
  });

  test.describe('active member', () => {
    const EMAIL = `e2e_wpdash_active_${Date.now()}@test.invalid`;
    const LOGIN = `e2e_wpdash_active_${Date.now()}`;
    let wpUserId: number;
    let subId: number;

    test.beforeAll(() => {
      wpUserId = createUser(LOGIN, EMAIL);
      setUserPassword(wpUserId, WP_PASSWORD);
      subId = createSubscription(wpUserId, productId, 'active');
      waitForSync(60_000);
    });

    test.afterAll(() => {
      try { deleteSubscription(subId); } catch {}
      try { deleteUser(wpUserId); } catch {}
      deleteOjsUser(EMAIL);
      clearTestSyncData();
    });

    test('shows active journal access card', async ({ page }) => {
      await wpLogin(page, LOGIN, WP_PASSWORD);
      await page.goto('/my-account/');

      const card = page.locator('.wpojs-journal-access');
      await expect(card).toBeVisible();
      await expect(card.locator('.wpojs-status--active')).toBeVisible();
      // Should have a link to the journal.
      await expect(card.locator('a[href*="journal"]')).toBeVisible();

      await page.screenshot({ path: 'e2e/screenshots/wp-dashboard-active-member.png', fullPage: true });
    });
  });

  test.describe('non-member', () => {
    const EMAIL = `e2e_wpdash_none_${Date.now()}@test.invalid`;
    const LOGIN = `e2e_wpdash_none_${Date.now()}`;
    let wpUserId: number;

    test.beforeAll(() => {
      wpUserId = createUser(LOGIN, EMAIL);
      setUserPassword(wpUserId, WP_PASSWORD);
    });

    test.afterAll(() => {
      try { deleteUser(wpUserId); } catch {}
      deleteOjsUser(EMAIL);
    });

    test('shows inactive journal access card', async ({ page }) => {
      await wpLogin(page, LOGIN, WP_PASSWORD);
      await page.goto('/my-account/');

      const card = page.locator('.wpojs-journal-access');
      await expect(card).toBeVisible();
      await expect(card.locator('.wpojs-status--inactive')).toBeVisible();
      await expect(card).toContainText(/no active access/i);

      await page.screenshot({ path: 'e2e/screenshots/wp-dashboard-non-member.png', fullPage: true });
    });
  });
});
