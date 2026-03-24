import { test, expect } from '@playwright/test';
import { ojsQuery, findOjsUser, setOjsPassword, getOjsUsername, deleteOjsUser, waitForSync, hasActiveSubscription } from '../../helpers/ojs';
import {
  createUserWithSubscription,
  getSubscriptionProductId,
  cleanupWpUser,
  createUser,
  setUserPassword,
} from '../../helpers/wp';

const OJS_BASE = 'http://localhost:8081';
const OJS_PASSWORD = process.env.QA_SUB_PASSWORD!;

/**
 * Find a paywalled article (section = "Articles") that has an HTML galley.
 */
function findPaywalledArticleWithHtmlGalley(): {
  submissionId: number;
} | null {
  const out = ojsQuery(`
    SELECT s.submission_id
    FROM submissions s
    JOIN publications p ON p.publication_id = s.current_publication_id
    JOIN publication_galleys g ON g.publication_id = p.publication_id
    JOIN sections sec ON sec.section_id = p.section_id
    JOIN section_settings ss ON ss.section_id = sec.section_id
      AND ss.setting_name = 'title' AND ss.setting_value = 'Articles'
    WHERE p.access_status != 1
      AND p.status = 3
      AND g.label = 'Full Text'
    LIMIT 1
  `);
  const id = parseInt(out.trim(), 10);
  return isNaN(id) ? null : { submissionId: id };
}

/**
 * Find an open-access article (editorial/book review) with an HTML galley.
 */
function findOpenAccessEditorialWithHtmlGalley(): {
  submissionId: number;
} | null {
  const out = ojsQuery(`
    SELECT s.submission_id
    FROM submissions s
    JOIN publications p ON p.publication_id = s.current_publication_id
    JOIN publication_galleys g ON g.publication_id = p.publication_id
    WHERE p.access_status = 1
      AND g.label = 'Full Text'
    LIMIT 1
  `);
  const id = parseInt(out.trim(), 10);
  return isNaN(id) ? null : { submissionId: id };
}

/** Log in to OJS via the browser using email + password. */
async function ojsLogin(page: import('@playwright/test').Page, email: string, password: string) {
  await page.goto(`${OJS_BASE}/index.php/ea/login`);
  // OJS login form uses email field (customized from default username)
  await page.locator('#username').fill(email);
  await page.locator('#password').fill(password);
  await page.locator('button[type="submit"], input[type="submit"]').first().click();

  // Wait for navigation away from the login page (successful login redirects)
  await page.waitForURL((url) => !url.pathname.includes('/login'), { timeout: 15_000 });
}

test.describe('Membership/access messaging', () => {
  let paywalledArticle: { submissionId: number } | null;
  let openAccessEditorial: { submissionId: number } | null;

  // Synced subscriber setup
  const SUB_EMAIL = `e2e_msg_sub_${Date.now()}@test.invalid`;
  const SUB_LOGIN = `e2e_msg_sub_${Date.now()}`;
  let subWpUserId: number;
  let subId: number;

  // Non-subscriber OJS user (created directly in OJS, no subscription)
  const NONSUB_EMAIL = `e2e_msg_nonsub_${Date.now()}@test.invalid`;
  const NONSUB_LOGIN = `e2e_msg_nonsub_${Date.now()}`;
  let nonsubWpUserId: number;

  test.beforeAll(() => {
    paywalledArticle = findPaywalledArticleWithHtmlGalley();
    openAccessEditorial = findOpenAccessEditorialWithHtmlGalley();

    // Create synced subscriber via WP → sync to OJS
    const productId = getSubscriptionProductId();
    ({ wpUserId: subWpUserId, subId } = createUserWithSubscription(
      SUB_LOGIN, SUB_EMAIL, productId,
    ));
    waitForSync();

    // Create non-subscriber WP user (no subscription, so no OJS sync).
    // Create directly in OJS DB for login purposes.
    nonsubWpUserId = createUser(NONSUB_LOGIN, NONSUB_EMAIL);
    setUserPassword(nonsubWpUserId, OJS_PASSWORD);
    // Create a minimal OJS account for the non-subscriber (no subscription)
    ojsQuery(`
      INSERT INTO users (username, password, email, date_registered)
      VALUES ('${NONSUB_LOGIN}', '', '${NONSUB_EMAIL}', NOW())
    `);
    const nonsubOjsUserId = findOjsUser(NONSUB_EMAIL);
    if (nonsubOjsUserId) {
      setOjsPassword(nonsubOjsUserId, NONSUB_LOGIN, OJS_PASSWORD);
    }
  });

  test.afterAll(() => {
    cleanupWpUser({ subIds: [subId], wpUserId: subWpUserId });
    deleteOjsUser(SUB_EMAIL);
    cleanupWpUser({ wpUserId: nonsubWpUserId });
    deleteOjsUser(NONSUB_EMAIL);
  });

  test('anonymous on paywalled article sees CTA box', async ({ page }) => {
    test.skip(!paywalledArticle, 'No paywalled article with HTML galley found');

    await page.goto(`${OJS_BASE}/index.php/ea/article/view/${paywalledArticle!.submissionId}`);
    await page.waitForLoadState('domcontentloaded');

    // Should see the non-subscriber CTA
    const cta = page.locator('.inline-html-galley-cta');
    await expect(cta).toBeVisible({ timeout: 10_000 });
    await expect(cta).toContainText('Full text available');
    await expect(cta).toContainText('membership');

    // Should NOT see inline HTML content
    const inlineHtml = page.locator('.inline-html-galley');
    await expect(inlineHtml).toHaveCount(0);
  });

  test('synced subscriber on paywalled article sees member box + content', async ({ page }) => {
    test.skip(!paywalledArticle, 'No paywalled article with HTML galley found');

    const subOjsUserId = findOjsUser(SUB_EMAIL);
    test.skip(!subOjsUserId, 'Synced OJS user not created');
    test.skip(!hasActiveSubscription(subOjsUserId!), 'Synced OJS user has no active subscription');

    const subOjsUsername = getOjsUsername(subOjsUserId!);
    setOjsPassword(subOjsUserId!, subOjsUsername, OJS_PASSWORD);

    await ojsLogin(page, SUB_EMAIL, OJS_PASSWORD);

    await page.goto(`${OJS_BASE}/index.php/ea/article/view/${paywalledArticle!.submissionId}`);
    await page.waitForLoadState('domcontentloaded');

    // Should see the blue subscriber notice
    const subscriberBox = page.locator('text=linked to your SEA membership');
    await expect(subscriberBox).toBeVisible({ timeout: 10_000 });

    // Should see inline HTML content
    const inlineHtml = page.locator('.inline-html-galley');
    await expect(inlineHtml).toBeVisible();

    // Should see updated archive notice with PDF mention
    await expect(inlineHtml).toContainText('view the PDF version');

    // Should NOT see the CTA
    const cta = page.locator('.inline-html-galley-cta');
    await expect(cta).toHaveCount(0);
  });

  test('logged-in non-subscriber on paywalled article sees CTA box', async ({ page }) => {
    test.skip(!paywalledArticle, 'No paywalled article with HTML galley found');

    const nonsubOjsUserId = findOjsUser(NONSUB_EMAIL);
    test.skip(!nonsubOjsUserId, 'Non-subscriber OJS user not created');

    await ojsLogin(page, NONSUB_EMAIL, OJS_PASSWORD);

    await page.goto(`${OJS_BASE}/index.php/ea/article/view/${paywalledArticle!.submissionId}`);
    await page.waitForLoadState('domcontentloaded');

    // Should see the non-subscriber CTA
    const cta = page.locator('.inline-html-galley-cta');
    await expect(cta).toBeVisible({ timeout: 10_000 });
    await expect(cta).toContainText('Full text available');

    // Should NOT see inline HTML content
    const inlineHtml = page.locator('.inline-html-galley');
    await expect(inlineHtml).toHaveCount(0);
  });

  test('subscriber on open-access editorial sees content without subscriber box', async ({ page }) => {
    test.skip(!openAccessEditorial, 'No open-access editorial with HTML galley found');

    const subOjsUserId = findOjsUser(SUB_EMAIL);
    test.skip(!subOjsUserId, 'Synced OJS user not created');

    const subOjsUsername = getOjsUsername(subOjsUserId!);
    setOjsPassword(subOjsUserId!, subOjsUsername, OJS_PASSWORD);

    await ojsLogin(page, SUB_EMAIL, OJS_PASSWORD);

    await page.goto(`${OJS_BASE}/index.php/ea/article/view/${openAccessEditorial!.submissionId}`);
    await page.waitForLoadState('domcontentloaded');

    // Should see inline HTML content
    const inlineHtml = page.locator('.inline-html-galley');
    await expect(inlineHtml).toBeVisible({ timeout: 10_000 });

    // Should NOT see subscriber box (open-access section, not paywalled)
    const subscriberBox = page.locator('text=linked to your SEA membership');
    await expect(subscriberBox).toHaveCount(0);
    const subBox2 = page.locator('text=via your journal subscription');
    await expect(subBox2).toHaveCount(0);

    // Should see archive notice
    await expect(inlineHtml).toContainText('digitally restored from print');
  });

  test('anonymous on open-access editorial sees content without CTA', async ({ page }) => {
    test.skip(!openAccessEditorial, 'No open-access editorial with HTML galley found');

    await page.goto(`${OJS_BASE}/index.php/ea/article/view/${openAccessEditorial!.submissionId}`);
    await page.waitForLoadState('domcontentloaded');

    // Should see inline HTML content (open-access = no paywall)
    const inlineHtml = page.locator('.inline-html-galley');
    await expect(inlineHtml).toBeVisible({ timeout: 10_000 });

    // Should NOT see CTA (open-access, so no HTML galley gate)
    const cta = page.locator('.inline-html-galley-cta');
    await expect(cta).toHaveCount(0);

    // Should NOT see subscriber box
    const subscriberBox = page.locator('text=linked to your SEA membership');
    await expect(subscriberBox).toHaveCount(0);
  });
});
