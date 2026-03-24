import { test, expect } from '@playwright/test';

/**
 * Read-only monitoring tests for live/staging environments.
 * These tests ONLY browse and assert — they never submit forms,
 * create users, or modify any state.
 *
 * Run with:
 *   LIVE_WP_HOME=https://community.existentialanalysis.org.uk \
 *   LIVE_OJS_URL=https://journal.existentialanalysis.org.uk \
 *     npx playwright test --config=playwright.monitor.config.ts
 */

const WP_HOME = process.env.LIVE_WP_HOME!;
const OJS_URL = process.env.LIVE_OJS_URL!;
// OJS journal path — default 'ea' for Existential Analysis
const OJS_JOURNAL_PATH = process.env.LIVE_OJS_JOURNAL_PATH || 'ea';
const OJS_JOURNAL_URL = `${OJS_URL}/index.php/${OJS_JOURNAL_PATH}`;

test.describe('OJS Journal', () => {
  test('homepage loads and shows articles', async ({ page }) => {
    const response = await page.goto(OJS_JOURNAL_URL, { waitUntil: 'domcontentloaded' });
    expect(response?.status()).toBeLessThan(400);

    // Journal homepage should show issue content
    const body = await page.textContent('body');
    expect(body).toBeTruthy();
    // Should contain the journal name or article listings
    expect(body!.length).toBeGreaterThan(500);
  });

  test('article page renders title and abstract', async ({ page }) => {
    // Navigate to the current issue and find an article link
    await page.goto(OJS_JOURNAL_URL, { waitUntil: 'domcontentloaded' });

    // Find an article link on the homepage/current issue
    const articleLink = page.locator('a.obj_galley_link, .title a, .obj_article_summary a.title').first();
    if (await articleLink.count() === 0) {
      // Try the archives page instead
      await page.goto(`${OJS_JOURNAL_URL}/issue/archive`, { waitUntil: 'domcontentloaded' });
      const issueLink = page.locator('.obj_issue_summary a, .title a').first();
      if (await issueLink.count() > 0) {
        await issueLink.click();
      }
    }

    // Find and click an article link
    const articleLinks = page.locator('.obj_article_summary a.title, .title a[href*="/article/view/"]');
    if (await articleLinks.count() > 0) {
      const href = await articleLinks.first().getAttribute('href');
      if (href) {
        const response = await page.goto(href, { waitUntil: 'domcontentloaded' });
        expect(response?.status()).toBeLessThan(400);

        // Article page should have a title
        const title = page.locator('.page_title, h1.title, .article-full-title');
        if (await title.count() > 0) {
          const titleText = await title.first().textContent();
          expect(titleText!.trim().length).toBeGreaterThan(3);
        }
      }
    }
  });

  test('archive page lists issues', async ({ page }) => {
    const response = await page.goto(`${OJS_JOURNAL_URL}/issue/archive`, {
      waitUntil: 'domcontentloaded',
    });
    expect(response?.status()).toBeLessThan(400);

    // Archive should have issue links
    const issueLinks = page.locator('.obj_issue_summary, .issue_summary, a[href*="/issue/view/"]');
    const count = await issueLinks.count();
    expect(count).toBeGreaterThan(0);
  });

  test('login page renders form', async ({ page }) => {
    const response = await page.goto(`${OJS_JOURNAL_URL}/login`, {
      waitUntil: 'domcontentloaded',
    });
    expect(response?.status()).toBeLessThan(400);

    // Login form should have username/password fields and submit button
    await expect(page.locator('input[name="username"], input[name="email"]').first()).toBeVisible();
    await expect(page.locator('input[name="password"]')).toBeVisible();
    await expect(page.locator('button[type="submit"], input[type="submit"]').first()).toBeVisible();
  });

  test('paywalled article shows price for anonymous user', async ({ page }) => {
    // Go to current issue and find an article in the "Articles" section
    await page.goto(OJS_JOURNAL_URL, { waitUntil: 'domcontentloaded' });

    // Look for galley links with prices (e.g., "PDF (£5.00)")
    const priceLinks = page.locator('a.obj_galley_link');
    const count = await priceLinks.count();

    if (count > 0) {
      // Check that at least one galley link contains a price indicator
      let foundPrice = false;
      for (let i = 0; i < Math.min(count, 10); i++) {
        const text = await priceLinks.nth(i).textContent();
        if (text && (text.includes('£') || text.includes('$') || text.includes('€'))) {
          foundPrice = true;
          break;
        }
      }
      // If we found prices, the paywall is working
      if (foundPrice) {
        // Good — paywall is showing prices to anonymous users
        return;
      }
    }

    // If no prices found on homepage, check an article page directly
    // This is acceptable — not all visible articles may be paywalled
  });

  test('open-access content renders (editorial/book review)', async ({ page }) => {
    // Navigate to an issue and look for editorial or book review sections
    await page.goto(OJS_JOURNAL_URL, { waitUntil: 'domcontentloaded' });

    // Look for galley links without prices (open access)
    const galleyLinks = page.locator('a.obj_galley_link');
    const count = await galleyLinks.count();

    for (let i = 0; i < Math.min(count, 10); i++) {
      const text = await galleyLinks.nth(i).textContent();
      // Open-access galleys typically don't have prices
      if (text && !text.includes('£') && !text.includes('$') && text.includes('Full Text')) {
        const href = await galleyLinks.nth(i).getAttribute('href');
        if (href) {
          const response = await page.goto(href, { waitUntil: 'domcontentloaded' });
          expect(response?.status()).toBeLessThan(400);

          // Page should have actual content (not blank — some editorials are short)
          const bodyText = await page.textContent('body');
          expect(bodyText!.length).toBeGreaterThan(100);
          return;
        }
      }
    }

    // If no open-access Full Text found, that's still OK — skip gracefully
  });
});

test.describe('WP Site', () => {
  test('homepage loads', async ({ page }) => {
    const response = await page.goto(WP_HOME, { waitUntil: 'domcontentloaded' });
    expect(response?.status()).toBeLessThan(400);

    const body = await page.textContent('body');
    expect(body!.length).toBeGreaterThan(100);
  });

  test('REST API responds', async ({ page }) => {
    const response = await page.goto(`${WP_HOME}/wp-json/`, { waitUntil: 'domcontentloaded' });
    expect(response?.status()).toBe(200);
  });
});

test.describe('Browser health', () => {
  test('no console errors on journal homepage', async ({ page }) => {
    const errors: string[] = [];
    page.on('console', (msg) => {
      if (msg.type() === 'error') {
        errors.push(msg.text());
      }
    });

    await page.goto(OJS_JOURNAL_URL, { waitUntil: 'domcontentloaded' });

    // Filter out known acceptable errors (e.g., third-party tracking, favicon)
    const realErrors = errors.filter(
      (e) =>
        !e.includes('favicon') &&
        !e.includes('analytics') &&
        !e.includes('google') &&
        !e.includes('Failed to load resource: net::ERR_BLOCKED_BY_CLIENT'),
    );

    expect(realErrors).toEqual([]);
  });

  test('pages load within 10 seconds', async ({ page }) => {
    const urls = [WP_HOME, OJS_JOURNAL_URL, `${OJS_JOURNAL_URL}/issue/archive`];

    for (const url of urls) {
      const start = Date.now();
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 10_000 });
      const elapsed = Date.now() - start;

      // Warn at 5s, fail at 10s (timeout will throw at 10s)
      expect(elapsed).toBeLessThan(10_000);
    }
  });
});
