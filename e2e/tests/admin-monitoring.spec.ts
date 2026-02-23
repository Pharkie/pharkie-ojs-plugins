import { test, expect } from '@playwright/test';
import {
  wpLogin,
  setUserPassword,
  createUser,
  deleteUser,
  insertLogEntry,
  deleteLogEntries,
} from '../helpers/wp';

const ADMIN_USER = 'admin';
const ADMIN_PASS = 'admin';
const LOG_PAGE = '/wp/wp-admin/admin.php?page=wpojs-sync-log';

test.describe('Admin monitoring: Sync Log page', () => {
  test('stats cards visible with correct labels', async ({ page }) => {
    await wpLogin(page, ADMIN_USER, ADMIN_PASS);
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
  });

  test('nonce field present for bulk actions', async ({ page }) => {
    await wpLogin(page, ADMIN_USER, ADMIN_PASS);
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

      await wpLogin(page, ADMIN_USER, ADMIN_PASS);
      await page.goto(LOG_PAGE + '&status=fail');

      // Find the row with our test email
      const row = page.locator('tr', { hasText: FAIL_EMAIL_1 });
      await expect(row).toBeVisible();

      const retryLink = row.locator('.wpojs-retry-link');
      await expect(retryLink).toBeVisible();

      // Click retry — the JS replaces the link with "Queued"
      retryLink.click();
      await expect(row.locator('text=Queued')).toBeVisible({ timeout: 10_000 });
    });

    test('bulk retry with checkboxes shows success alert', async ({
      page,
    }) => {
      insertLogEntry(FAIL_EMAIL_1, 'activate', 'fail');
      insertLogEntry(FAIL_EMAIL_2, 'activate', 'fail');

      await wpLogin(page, ADMIN_USER, ADMIN_PASS);
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
