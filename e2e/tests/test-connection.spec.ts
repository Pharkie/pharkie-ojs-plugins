import { test, expect } from '@playwright/test';
import { wpLogin, WP_ADMIN_USER, getAdminPassword } from '../helpers/wp';

const SETTINGS_PAGE = '/wp/wp-admin/admin.php?page=wpojs-sync';

test.describe('Test Connection button', () => {
  test('shows success when OJS is reachable and configured', async ({ page }) => {
    await wpLogin(page, WP_ADMIN_USER, getAdminPassword());
    await page.goto(SETTINGS_PAGE);

    const btn = page.locator('#wpojs-test-connection');
    await expect(btn).toBeVisible();

    const result = page.locator('#wpojs-test-result');

    // Click and wait for the AJAX result
    await btn.click();
    await expect(result).toContainText('Connection successful', { timeout: 15_000 });

    await page.screenshot({ path: 'e2e/screenshots/wp-test-connection-success.png', fullPage: true });
  });
});
