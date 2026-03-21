import { test, expect } from '@playwright/test';
const OJS_BASE = 'http://localhost:8081';

test.describe('OJS UI messages', () => {
  test('login page shows login hint', async ({ page }) => {
    await page.goto(`${OJS_BASE}/index.php/ea/login`);
    const hint = page.locator('.wpojs-login-hint');
    await expect(hint).toBeVisible({ timeout: 10_000 });
    // Should mention password-related action.
    await expect(hint).toContainText(/password/i);

    await page.screenshot({ path: 'e2e/screenshots/ojs-login-hint.png', fullPage: true });
  });

  test('password reset page shows warning hint', async ({ page }) => {
    await page.goto(`${OJS_BASE}/index.php/ea/login/lostPassword`);
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
    await page.goto(`${OJS_BASE}/index.php/ea`);
    // Footer message contains "membership" and a link to the WP site.
    const footer = page.locator('text=membership').last();
    await expect(footer).toBeVisible({ timeout: 10_000 });
    const link = page.locator('a[href*="localhost:8080"]').last();
    await expect(link).toBeVisible();

    await page.screenshot({ path: 'e2e/screenshots/ojs-footer-message.png', fullPage: true });
  });

});
