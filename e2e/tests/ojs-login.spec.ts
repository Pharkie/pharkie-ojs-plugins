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
  getOjsUsername,
  setOjsPassword,
  deleteOjsUser,
  waitForSync,
} from '../helpers/ojs';

const EMAIL = `e2e_ojslogin_${Date.now()}@test.invalid`;
const LOGIN = `e2e_ojslogin_${Date.now()}`;
const OJS_PASSWORD = 'TestPass123!';

let wpUserId: number;
let subId: number;

test.describe('Synced user logs in to OJS', () => {
  test.beforeAll(() => {
    const productId = getSubscriptionProductId();
    wpUserId = createUser(LOGIN, EMAIL);
    subId = createSubscription(wpUserId, productId, 'active');
    waitForSync();
  });

  test.afterAll(() => {
    try { deleteSubscription(subId); } catch {}
    try { deleteUser(wpUserId); } catch {}
    deleteOjsUser(EMAIL);
    clearTestSyncData();
  });

  test('set password and log in to OJS', async ({ page }) => {
    const ojsUserId = findOjsUser(EMAIL);
    expect(ojsUserId).not.toBeNull();

    const username = getOjsUsername(ojsUserId!);
    expect(username).toBeTruthy();

    // Set a known password directly (avoids needing email/reset flow).
    setOjsPassword(ojsUserId!, username, OJS_PASSWORD);

    // Navigate to OJS login.
    await page.goto('http://localhost:8081/index.php/journal/login');
    await page.locator('#username').fill(username);
    await page.locator('#password').fill(OJS_PASSWORD);
    await page.locator('button[type="submit"], input[type="submit"]').first().click();

    // After login, the user navigation should be visible.
    await expect(
      page.locator('.pkp_navigation_user, .app__userNav, [class*="navigation_user"]').first(),
    ).toBeVisible({ timeout: 15_000 });

    await page.screenshot({ path: 'e2e/screenshots/ojs-login-success.png', fullPage: true });
  });
});
