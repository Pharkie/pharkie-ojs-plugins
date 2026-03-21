import { test, expect } from '@playwright/test';
import { ojsQuery, findOjsUser, setOjsPassword, deleteOjsUser } from '../../helpers/ojs';

const OJS_BASE = 'http://localhost:8081';
const OJS_PASSWORD = 'TestPass123!';

/**
 * Find a paywalled article that has galley links with prices.
 */
function findPaywalledArticleWithGalleys(): number | null {
  const out = ojsQuery(`
    SELECT s.submission_id
    FROM submissions s
    JOIN publications p ON p.publication_id = s.current_publication_id
    JOIN publication_galleys g ON g.publication_id = p.publication_id
    WHERE p.access_status != 1
      AND p.status = 3
    LIMIT 1
  `);
  const id = parseInt(out.trim(), 10);
  return isNaN(id) ? null : id;
}

/** Log in to OJS via the browser using email + password. */
async function ojsLogin(page: import('@playwright/test').Page, email: string, password: string) {
  await page.goto(`${OJS_BASE}/index.php/ea/login`);
  await page.locator('#username').fill(email);
  await page.locator('#password').fill(password);
  await page.locator('button[type="submit"], input[type="submit"]').first().click();
  await page.waitForURL((url) => !url.pathname.includes('/login'), { timeout: 15_000 });
}

test.describe('Article purchase flow', () => {
  const EMAIL = `e2e_purchase_${Date.now()}@test.invalid`;
  const LOGIN = `e2e_purchase_${Date.now()}`;
  let ojsUserId: number | null;
  let articleId: number | null;

  test.beforeAll(() => {
    articleId = findPaywalledArticleWithGalleys();

    // Create a non-subscriber OJS user directly (no WP, no subscription)
    ojsQuery(`
      INSERT INTO users (username, password, email, date_registered)
      VALUES ('${LOGIN}', '', '${EMAIL}', NOW())
    `);
    ojsUserId = findOjsUser(EMAIL);
    if (ojsUserId) {
      setOjsPassword(ojsUserId, LOGIN, OJS_PASSWORD);
    }
  });

  test.afterAll(() => {
    deleteOjsUser(EMAIL);
  });

  test('clicking galley purchase link starts payment flow', async ({ page }) => {
    test.skip(!articleId, 'No paywalled article found');
    test.skip(!ojsUserId, 'OJS test user not created');

    // Log in as the non-subscriber
    await ojsLogin(page, EMAIL, OJS_PASSWORD);

    // Navigate to the paywalled article
    await page.goto(`${OJS_BASE}/index.php/ea/article/view/${articleId}`);
    await page.waitForLoadState('domcontentloaded');

    // Should see galley links with prices (e.g., "PDF (GBP 3)")
    const galleyLink = page.locator('.obj_galley_link').first();
    await expect(galleyLink).toBeVisible({ timeout: 5_000 });

    // The galley link text should include a price
    const linkText = await galleyLink.textContent();
    expect(linkText).toMatch(/GBP|£/);

    // Record the current URL before clicking
    const articleUrl = page.url();

    // Click the galley link — should start a payment flow
    await galleyLink.click();
    await page.waitForLoadState('domcontentloaded');

    const currentUrl = page.url();

    // Should NOT redirect back to login (user is already logged in)
    expect(currentUrl, 'Should not redirect to login').not.toContain('/login');

    // Should NOT just reload the same article page
    expect(
      currentUrl,
      'Clicking galley purchase link should navigate away from article page to a payment flow, not just reload',
    ).not.toContain(`/article/view/${articleId}`);

    // Should arrive at a payment page — either PayPal redirect or OJS payment form
    // OJS payment URLs look like /ea/payment/plugin/paypal or similar
    const isPaymentPage = currentUrl.includes('/payment')
      || currentUrl.includes('paypal')
      || currentUrl.includes('sandbox.paypal');
    expect(isPaymentPage, `Expected payment page, got: ${currentUrl}`).toBe(true);
  });
});
