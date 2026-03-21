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
    // Clean up: user + any completed payments
    if (ojsUserId) {
      ojsQuery(`DELETE FROM completed_payments WHERE user_id = ${ojsUserId}`);
    }
    deleteOjsUser(EMAIL);
  });

  test('anonymous user sees paywall with prices', async ({ page }) => {
    test.skip(!articleId, 'No paywalled article found');

    await page.goto(`${OJS_BASE}/index.php/ea/article/view/${articleId}`);
    await page.waitForLoadState('domcontentloaded');

    // Should see galley links with prices
    const galleyLink = page.locator('.obj_galley_link').first();
    await expect(galleyLink).toBeVisible({ timeout: 5_000 });
    const linkText = await galleyLink.textContent();
    expect(linkText).toMatch(/GBP|£/);
  });

  test('clicking galley redirects to Stripe Checkout', async ({ page }) => {
    test.skip(!articleId, 'No paywalled article found');
    test.skip(!ojsUserId, 'OJS test user not created');

    await ojsLogin(page, EMAIL, OJS_PASSWORD);
    await page.goto(`${OJS_BASE}/index.php/ea/article/view/${articleId}`);
    await page.waitForLoadState('domcontentloaded');

    const galleyLink = page.locator('.obj_galley_link').first();
    await expect(galleyLink).toBeVisible({ timeout: 5_000 });
    await galleyLink.click();

    // Wait for navigation — should redirect to Stripe
    await page.waitForLoadState('domcontentloaded');
    const currentUrl = page.url();

    // Should NOT redirect back to login
    expect(currentUrl, 'Should not redirect to login').not.toContain('/login');

    // Should NOT land on the homepage (broken payment config)
    expect(currentUrl, 'Should not redirect to homepage').not.toBe(`${OJS_BASE}/ea/index`);

    // Should be at Stripe Checkout
    const isStripeRedirect = currentUrl.includes('checkout.stripe.com');
    const isPaypalRedirect = currentUrl.includes('paypal');
    const isOjsPaymentUrl = currentUrl.includes('/payment');

    expect(
      isStripeRedirect || isPaypalRedirect || isOjsPaymentUrl,
      `Expected payment page, got: ${currentUrl}`,
    ).toBe(true);
  });

  test('successful Stripe payment grants article access', async ({ page }) => {
    test.skip(!articleId, 'No paywalled article found');
    test.skip(!ojsUserId, 'OJS test user not created');

    await ojsLogin(page, EMAIL, OJS_PASSWORD);
    await page.goto(`${OJS_BASE}/index.php/ea/article/view/${articleId}`);
    await page.waitForLoadState('domcontentloaded');

    // Click galley to start payment
    const galleyLink = page.locator('.obj_galley_link').first();
    await galleyLink.click();
    await page.waitForLoadState('domcontentloaded');

    // Should be on Stripe Checkout
    const stripeUrl = page.url();
    if (!stripeUrl.includes('checkout.stripe.com')) {
      test.skip(true, 'Not redirected to Stripe — payment plugin may not be Stripe');
    }

    // Fill Stripe Checkout form
    await page.locator('input[name="cardNumber"], #cardNumber').fill('4242424242424242');
    await page.locator('input[name="cardExpiry"], #cardExpiry').fill('12/30');
    await page.locator('input[name="cardCvc"], #cardCvc').fill('123');
    await page.locator('input[name="billingName"], #billingName').fill('E2E Test');

    // Fill email if present
    const emailField = page.locator('input[name="email"], #email');
    if (await emailField.isVisible({ timeout: 1000 }).catch(() => false)) {
      await emailField.fill(EMAIL);
    }

    // Fill postal code if present (required for UK cards)
    const postalCode = page.locator('input[name="billingPostalCode"], input[placeholder*="Postal code"], input[placeholder*="postal code"], input[autocomplete="postal-code"]');
    if (await postalCode.isVisible({ timeout: 1000 }).catch(() => false)) {
      await postalCode.fill('SW1A 1AA');
    }

    // Submit payment
    await page.locator('button[type="submit"], .SubmitButton').click();

    // Wait for redirect back to OJS (Stripe redirects after successful payment)
    await page.waitForURL((url) => url.hostname === 'localhost', { timeout: 30_000 });

    // Should have been redirected back to the article
    const returnUrl = page.url();
    expect(returnUrl).toContain('localhost:8081');

    // Verify article is now accessible — galley links should not show prices
    await page.goto(`${OJS_BASE}/index.php/ea/article/view/${articleId}`);
    await page.waitForLoadState('domcontentloaded');

    const galleyAfter = page.locator('.obj_galley_link').first();
    await expect(galleyAfter).toBeVisible({ timeout: 5_000 });
    const linkTextAfter = await galleyAfter.textContent();
    // After purchase, should show just "PDF" without price
    expect(linkTextAfter).not.toMatch(/GBP|£/);
  });

  test('declined card shows error', async ({ page }) => {
    test.skip(!articleId, 'No paywalled article found');
    test.skip(!ojsUserId, 'OJS test user not created');

    await ojsLogin(page, EMAIL, OJS_PASSWORD);

    // Find a different article not yet purchased
    const otherArticleOut = ojsQuery(`
      SELECT s.submission_id
      FROM submissions s
      JOIN publications p ON p.publication_id = s.current_publication_id
      JOIN publication_galleys g ON g.publication_id = p.publication_id
      LEFT JOIN completed_payments cp ON cp.assoc_id = s.submission_id AND cp.user_id = ${ojsUserId} AND cp.payment_type = 3
      WHERE p.access_status != 1 AND p.status = 3 AND cp.completed_payment_id IS NULL
      LIMIT 1
    `);
    const otherArticleId = parseInt(otherArticleOut.trim(), 10);
    test.skip(isNaN(otherArticleId), 'No unpurchased paywalled article found');

    await page.goto(`${OJS_BASE}/index.php/ea/article/view/${otherArticleId}`);
    await page.waitForLoadState('domcontentloaded');

    const galleyLink = page.locator('.obj_galley_link').first();
    await galleyLink.click();
    await page.waitForLoadState('domcontentloaded');

    if (!page.url().includes('checkout.stripe.com')) {
      test.skip(true, 'Not redirected to Stripe');
    }

    // Use Stripe's always-declined test card
    await page.locator('input[name="cardNumber"], #cardNumber').fill('4000000000000002');
    await page.locator('input[name="cardExpiry"], #cardExpiry').fill('12/30');
    await page.locator('input[name="cardCvc"], #cardCvc').fill('123');
    await page.locator('input[name="billingName"], #billingName').fill('E2E Decline');

    const emailField = page.locator('input[name="email"], #email');
    if (await emailField.isVisible({ timeout: 1000 }).catch(() => false)) {
      await emailField.fill(EMAIL);
    }

    await page.locator('button[type="submit"], .SubmitButton').click();

    // Stripe shows decline error on their page — should stay on Stripe
    await page.waitForTimeout(5000);
    expect(page.url()).toContain('checkout.stripe.com');

    // Should see an error message
    const bodyText = await page.textContent('body');
    expect(bodyText).toMatch(/declined|denied|failed|error/i);
  });

  test('cancel returns to article page', async ({ page }) => {
    test.skip(!articleId, 'No paywalled article found');
    test.skip(!ojsUserId, 'OJS test user not created');

    await ojsLogin(page, EMAIL, OJS_PASSWORD);

    // Find an unpurchased article
    const unpurchasedOut = ojsQuery(`
      SELECT s.submission_id
      FROM submissions s
      JOIN publications p ON p.publication_id = s.current_publication_id
      JOIN publication_galleys g ON g.publication_id = p.publication_id
      LEFT JOIN completed_payments cp ON cp.assoc_id = s.submission_id AND cp.user_id = ${ojsUserId} AND cp.payment_type = 3
      WHERE p.access_status != 1 AND p.status = 3 AND cp.completed_payment_id IS NULL
      LIMIT 1
    `);
    const unpurchasedId = parseInt(unpurchasedOut.trim(), 10);
    test.skip(isNaN(unpurchasedId), 'No unpurchased article found');

    await page.goto(`${OJS_BASE}/index.php/ea/article/view/${unpurchasedId}`);
    await page.waitForLoadState('domcontentloaded');

    const galleyLink = page.locator('.obj_galley_link').first();
    await galleyLink.click();
    await page.waitForLoadState('domcontentloaded');

    if (!page.url().includes('checkout.stripe.com')) {
      test.skip(true, 'Not redirected to Stripe');
    }

    // Click the back/cancel link on Stripe Checkout
    const cancelLink = page.locator('a[href*="cancel"], a:has-text("Back"), a:has-text("back")').first();
    if (await cancelLink.isVisible({ timeout: 3000 }).catch(() => false)) {
      await cancelLink.click();
      await page.waitForURL((url) => url.hostname === 'localhost', { timeout: 15_000 });

      // Should be back on OJS (article page)
      expect(page.url()).toContain('localhost:8081');
    }
  });
});
