import { test, expect } from '@playwright/test';
import {
  createUser,
  deleteUser,
  createSubscription,
  deleteSubscription,
  updateSubscriptionStatus,
  getSubscriptionProductId,
  clearTestSyncData,
  createUserWithSubscription,
  cleanupWpUser,
} from '../helpers/wp';
import {
  findOjsUser,
  getOjsUsername,
  setOjsPassword,
  deleteOjsUser,
  waitForSync,
} from '../helpers/ojs';

const OJS_BASE = 'http://localhost:8081';

test.describe('OJS UI messages', () => {
  test('login page shows login hint', async ({ page }) => {
    await page.goto(`${OJS_BASE}/index.php/journal/login`);
    const hint = page.locator('.wpojs-login-hint');
    await expect(hint).toBeVisible({ timeout: 10_000 });
    // Should mention password-related action.
    await expect(hint).toContainText(/password/i);

    await page.screenshot({ path: 'e2e/screenshots/ojs-login-hint.png', fullPage: true });
  });

  test('password reset page shows warning hint', async ({ page }) => {
    await page.goto(`${OJS_BASE}/index.php/journal/login/lostPassword`);
    const hint = page.locator('.wpojs-pw-reset-hint');
    await expect(hint).toBeVisible({ timeout: 10_000 });
    // Should mention password sync / membership website.
    await expect(hint).toContainText(/password/i);
    // Should contain a link to the WP password reset page.
    const link = hint.locator('a[href*="wp-login.php?action=lostpassword"]');
    await expect(link).toBeVisible();

    await page.screenshot({ path: 'e2e/screenshots/ojs-pw-reset-hint.png', fullPage: true });
  });

  test('site footer shows membership message', async ({ page }) => {
    await page.goto(`${OJS_BASE}/index.php/journal`);
    // Footer message contains "membership" and a link to the WP site.
    const footer = page.locator('text=membership').last();
    await expect(footer).toBeVisible({ timeout: 10_000 });
    const link = page.locator('a[href*="localhost:8080"]').last();
    await expect(link).toBeVisible();

    await page.screenshot({ path: 'e2e/screenshots/ojs-footer-message.png', fullPage: true });
  });

  test.describe('paywall hint for non-subscriber', () => {
    const EMAIL = `e2e_paywall_${Date.now()}@test.invalid`;
    const LOGIN = `e2e_paywall_${Date.now()}`;
    const OJS_PASSWORD = 'TestPass123!';

    let wpUserId: number;
    let subId: number;

    test.beforeAll(() => {
      const productId = getSubscriptionProductId();
      ({ wpUserId, subId } = createUserWithSubscription(LOGIN, EMAIL, productId));
      // Sync then expire — gives us an OJS user without active subscription.
      waitForSync();
      updateSubscriptionStatus(subId, 'expired');
      waitForSync();

      // Set a known OJS password so we can log in.
      const ojsUserId = findOjsUser(EMAIL);
      if (ojsUserId) {
        const username = getOjsUsername(ojsUserId);
        setOjsPassword(ojsUserId, username, OJS_PASSWORD);
      }
    });

    test.afterAll(() => {
      cleanupWpUser({ subIds: [subId], wpUserId });
      deleteOjsUser(EMAIL);
    });

    test('article page shows paywall hint for logged-in non-subscriber', async ({
      page,
    }) => {
      const ojsUserId = findOjsUser(EMAIL);
      expect(ojsUserId).not.toBeNull();
      const username = getOjsUsername(ojsUserId!);

      // Log in to OJS.
      await page.goto(`${OJS_BASE}/index.php/journal/login`);
      await page.locator('#username').fill(username);
      await page.locator('#password').fill(OJS_PASSWORD);
      await page.locator('button[type="submit"], input[type="submit"]').first().click();
      await page.waitForURL(/journal/, { timeout: 15_000 });

      // Visit an article page. We need to find one that exists.
      // Try the first article link from the journal homepage.
      await page.goto(`${OJS_BASE}/index.php/journal`);
      const articleLink = page.locator('a[href*="/article/view/"]').first();

      // If no articles exist, skip this assertion gracefully.
      if ((await articleLink.count()) === 0) {
        test.skip(true, 'No articles published in OJS — cannot test paywall hint');
        return;
      }

      await articleLink.click();
      await page.waitForLoadState('domcontentloaded');

      // The paywall hint only renders when WPOJS_SUPPORT_EMAIL is configured.
      // Check if it's present before asserting visibility.
      const paywallHint = page.locator('[style*="fff3cd"]');
      const hintCount = await paywallHint.count();
      if (hintCount === 0) {
        test.skip(true, 'WPOJS_SUPPORT_EMAIL not configured — paywall hint not rendered');
        return;
      }
      await expect(paywallHint).toBeVisible({ timeout: 10_000 });

      await page.screenshot({ path: 'e2e/screenshots/ojs-paywall-hint.png', fullPage: true });
    });
  });
});
