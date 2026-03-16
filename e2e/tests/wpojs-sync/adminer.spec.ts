import { test, expect } from '@playwright/test';

const ADMINER_URL = 'http://localhost:8082';

function getDbPassword(envVar: string): string {
  const p = process.env[envVar];
  if (!p) throw new Error(`${envVar} env var not set`);
  return p;
}

async function adminerLogin(
  page: import('@playwright/test').Page,
  server: string,
  username: string,
  password: string,
  database: string,
) {
  await page.goto(ADMINER_URL);
  await page.locator('input[name="auth[server]"]').fill(server);
  await page.locator('input[name="auth[username]"]').fill(username);
  await page.locator('input[name="auth[password]"]').fill(password);
  await page.locator('input[name="auth[db]"]').fill(database);
  await page.getByRole('button', { name: 'Login' }).click();
  await page.waitForLoadState('networkidle');
}

test.describe('Adminer', () => {
  test('can browse WordPress database tables', async ({ page }) => {
    await adminerLogin(page, 'wp-db', 'wordpress', getDbPassword('DB_PASSWORD'), 'wordpress');
    await expect(page.locator('#Table-wp_posts')).toBeVisible();
  });

  test('can browse OJS database tables', async ({ page }) => {
    await adminerLogin(page, 'ojs-db', 'ojs', getDbPassword('OJS_DB_PASSWORD'), 'ojs');
    await expect(page.locator('#Table-users')).toBeVisible();
  });
});
