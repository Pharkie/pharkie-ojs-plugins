import { test, expect } from '@playwright/test';
import { wpLogin, WP_ADMIN_USER, getAdminPassword } from '../helpers/wp';

const SETTINGS_PAGE = '/wp/wp-admin/admin.php?page=wpojs-sync';

test.describe('Settings page', () => {
  test('shows OJS connection status and type dropdowns', async ({ page }) => {
    await wpLogin(page, WP_ADMIN_USER, getAdminPassword());
    await page.goto(SETTINGS_PAGE, { waitUntil: 'networkidle' });

    // Connection status should show success with type count.
    await expect(page.locator('text=Connected to OJS')).toBeVisible();

    // Product mapping dropdowns should show OJS type names fetched from the API.
    // Dev environment has "Membership (all tiers)" as type 1.
    const mappingSection = page.locator('#wpojs-type-mapping');
    await expect(mappingSection.locator('select').first()).toContainText('Membership (all tiers)');

    // Role-based access OJS Type dropdown should also have the type.
    const defaultTypeSelect = page.locator('select[name="wpojs_default_type_id"]');
    await expect(defaultTypeSelect).toContainText('Membership (all tiers)');

    await page.screenshot({ path: 'e2e/screenshots/wp-test-connection-success.png', fullPage: true });
  });
});
