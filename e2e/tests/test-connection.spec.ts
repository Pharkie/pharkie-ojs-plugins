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

test.describe('Settings page UX', () => {
  test('shows OJS subscription type names in mapping UI', async ({ page }) => {
    await wpLogin(page, WP_ADMIN_USER, getAdminPassword());
    await page.goto(SETTINGS_PAGE);

    // The type mapping section should show OJS type names fetched from the API.
    // Dev environment has "SEA UK Membership" as type 1.
    const mappingSection = page.locator('#wpojs-type-mapping');
    await expect(mappingSection).toContainText('SEA UK Membership');

    // The "Available OJS types" reference line should list type names.
    const availableTypes = page.locator('.description', { hasText: 'Available OJS types' });
    await expect(availableTypes).toBeVisible();
    await expect(availableTypes).toContainText('SEA UK Membership');
  });
});
