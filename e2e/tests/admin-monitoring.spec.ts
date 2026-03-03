import { test, expect } from '@playwright/test';
import {
  wpLogin,
  setUserPassword,
  createUser,
  deleteUser,
  insertLogEntry,
  deleteLogEntries,
  clearTestSyncData,
  WP_ADMIN_USER,
  getAdminPassword,
} from '../helpers/wp';
const LOG_PAGE = '/wp/wp-admin/admin.php?page=wpojs-sync-log';

test.describe('Admin monitoring: Sync Log page', () => {
  test.beforeAll(() => {
    clearTestSyncData();
  });

  test('stats cards visible with correct labels', async ({ page }) => {
    await wpLogin(page, WP_ADMIN_USER, getAdminPassword());
    await page.goto(LOG_PAGE);

    const cards = page.locator('.wpojs-stats-cards');
    await expect(cards).toBeVisible();

    // 5 stat cards
    const children = cards.locator('> div');
    await expect(children).toHaveCount(5);

    // Check all expected labels are present
    const labels = [
      'Members Synced',
      'Failures (24h)',
      'Failures (7d)',
      'Success Rate (7d)',
      'Queue',
    ];
    for (const label of labels) {
      await expect(cards).toContainText(label);
    }

    await page.screenshot({ path: 'e2e/screenshots/wp-admin-sync-log.png', fullPage: true });
  });

  test('nonce field present for bulk actions', async ({ page }) => {
    await wpLogin(page, WP_ADMIN_USER, getAdminPassword());
    await page.goto(LOG_PAGE);

    await expect(page.locator('input[name="_wpojs_nonce"]')).toBeAttached();
  });

  test.describe('retry actions', () => {
    const TS = Date.now();
    const FAIL_EMAIL_1 = `e2e_retry1_${TS}@test.invalid`;
    const FAIL_EMAIL_2 = `e2e_retry2_${TS}@test.invalid`;

    test.afterAll(() => {
      deleteLogEntries(FAIL_EMAIL_1);
      deleteLogEntries(FAIL_EMAIL_2);
    });

    test('single retry link appears on failed entry and queues retry', async ({
      page,
    }) => {
      insertLogEntry(FAIL_EMAIL_1, 'activate', 'fail');

      await wpLogin(page, WP_ADMIN_USER, getAdminPassword());
      await page.goto(LOG_PAGE + '&status=fail');

      // Find the row with our test email
      const row = page.locator('tr', { hasText: FAIL_EMAIL_1 });
      await expect(row).toBeVisible();

      const retryLink = row.locator('.wpojs-retry-link');
      await expect(retryLink).toBeVisible();

      // Click retry — the JS replaces the link with "Queued"
      retryLink.click();
      await expect(row.locator('text=Queued')).toBeVisible({ timeout: 10_000 });

      await page.screenshot({ path: 'e2e/screenshots/wp-admin-retry-queued.png', fullPage: true });
    });

    test('bulk retry with checkboxes shows success alert', async ({
      page,
    }) => {
      // Clean up any entries from previous tests to avoid duplicate rows
      deleteLogEntries(FAIL_EMAIL_1);
      deleteLogEntries(FAIL_EMAIL_2);
      insertLogEntry(FAIL_EMAIL_1, 'activate', 'fail');
      insertLogEntry(FAIL_EMAIL_2, 'activate', 'fail');

      await wpLogin(page, WP_ADMIN_USER, getAdminPassword());
      await page.goto(LOG_PAGE + '&status=fail');

      // Check both rows
      const row1 = page.locator('tr', { hasText: FAIL_EMAIL_1 });
      const row2 = page.locator('tr', { hasText: FAIL_EMAIL_2 });
      await row1.locator('input[name="log_ids[]"]').check();
      await row2.locator('input[name="log_ids[]"]').check();

      // Select "Retry Selected" from bulk action dropdown and apply
      await page.locator('select[name="action"]').selectOption('retry_selected');

      // Listen for the dialog (alert) before clicking
      const dialogPromise = page.waitForEvent('dialog');
      await page.locator('#doaction').click();

      const dialog = await dialogPromise;
      expect(dialog.message()).toContain('queued for retry');
      await dialog.accept();
    });
  });
});
