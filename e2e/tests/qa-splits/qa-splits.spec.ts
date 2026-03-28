import { test, expect, Page } from '@playwright/test';
import { ojsQuery, ojsPhp } from '../../helpers/ojs';

const OJS_BASE = 'http://localhost:8081';
const QA_URL = `${OJS_BASE}/index.php/ea/qa-splits`;
const API_BASE = `${OJS_BASE}/index.php/ea/api/v1/qa-splits`;

// Admin credentials (from .env via playwright.config.ts)
const ADMIN_USER = process.env.OJS_ADMIN_USER ?? 'admin';
const ADMIN_PASS = process.env.OJS_ADMIN_PASSWORD ?? '';

/**
 * Log in to OJS as admin. Reusable across tests.
 */
async function loginAsAdmin(page: Page): Promise<void> {
  await page.goto(`${OJS_BASE}/index.php/ea/login`);
  await page.fill('input[name="username"]', ADMIN_USER);
  await page.fill('input[name="password"]', ADMIN_PASS);
  await page.click('button[type="submit"], input[type="submit"]');
  await page.waitForURL(/.*/,  { timeout: 10_000 });
}

/**
 * Check if the qa_split_reviews table exists in OJS DB.
 */
function qaTableExists(): boolean {
  const out = ojsQuery(
    `SELECT COUNT(*) FROM information_schema.tables
     WHERE table_schema = DATABASE() AND table_name = 'qa_split_reviews'`,
  );
  return parseInt(out.trim(), 10) > 0;
}

/**
 * Check if the QA Splits plugin is enabled.
 */
function pluginEnabled(): boolean {
  const out = ojsQuery(
    `SELECT setting_value FROM plugin_settings
     WHERE plugin_name = 'qasplitsplugin' AND setting_name = 'enabled'
     LIMIT 1`,
  );
  return out.trim() === '1';
}

/**
 * Find any article with both PDF and HTML galleys.
 */
function findArticleWithGalleys(): number | null {
  const out = ojsQuery(`
    SELECT s.submission_id
    FROM submissions s
    JOIN publications p ON p.publication_id = s.current_publication_id
    JOIN publication_galleys g1 ON g1.publication_id = p.publication_id
    JOIN submission_files sf1 ON g1.submission_file_id = sf1.submission_file_id
    JOIN files f1 ON sf1.file_id = f1.file_id AND f1.mimetype = 'application/pdf'
    JOIN publication_galleys g2 ON g2.publication_id = p.publication_id
    JOIN submission_files sf2 ON g2.submission_file_id = sf2.submission_file_id
    JOIN files f2 ON sf2.file_id = f2.file_id AND f2.mimetype = 'text/html'
    WHERE s.context_id = (SELECT journal_id FROM journals WHERE path = 'ea' LIMIT 1)
    LIMIT 1
  `);
  const id = parseInt(out.trim(), 10);
  return isNaN(id) ? null : id;
}

/**
 * Clean up any test reviews created during tests.
 */
function cleanupTestReviews(): void {
  if (qaTableExists()) {
    ojsQuery(`DELETE FROM qa_split_reviews WHERE comment LIKE 'e2e-test-%'`);
  }
}

// ─────────────────────────────────────────────────────────────────
// Tests
// ─────────────────────────────────────────────────────────────────

test.describe('QA Splits plugin', () => {
  let articleId: number | null;

  test.beforeAll(() => {
    articleId = findArticleWithGalleys();
  });

  test.afterAll(() => {
    cleanupTestReviews();
  });

  // ── Access control ──

  test('redirects unauthenticated users to login', async ({ page }) => {
    await page.goto(QA_URL, { waitUntil: 'networkidle' });
    // OJS redirects to login page — check we ended up there
    await expect(page).toHaveURL(/login/, { timeout: 10_000 });
  });

  // ── Page rendering ──

  test('loads full-screen QA interface for admin', async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(QA_URL);
    await page.waitForLoadState('domcontentloaded');

    // Should be a standalone page (no OJS header)
    await expect(page.locator('.qa-layout')).toBeVisible();
    await expect(page.locator('.qa-top')).toBeVisible();
    await expect(page.locator('.qa-left')).toBeVisible();
    await expect(page.locator('.qa-right')).toBeVisible();

    // No OJS navigation chrome
    await expect(page.locator('.pkp_navigation')).not.toBeVisible();

    // Take screenshot of initial state
    await page.screenshot({
      path: 'e2e/screenshots/qa-splits-initial.png',
      fullPage: false,
    });
  });

  test('displays article metadata in top pane', async ({ page }) => {
    test.skip(!articleId, 'No article with galleys found');

    await loginAsAdmin(page);
    await page.goto(QA_URL);

    // Wait for articles to load
    await expect(page.locator('#qa-title')).not.toHaveText('Loading...');
    await expect(page.locator('#qa-title')).not.toHaveText('No articles found');

    // Metadata should be populated
    const title = await page.locator('#qa-title').textContent();
    expect(title).toBeTruthy();
    expect(title).not.toBe('Loading...');

    // Title should include issue prefix (e.g. "37.1 #0 (2026) Article Title [editorial]")
    expect(title).toMatch(/\d+\.\d+ #\d+ \(\d{4}\) /);

    // Status badge should be visible
    await expect(page.locator('#qa-status')).toBeVisible();
  });

  // ── PDF viewer ──

  test('renders PDF in left pane', async ({ page }) => {
    test.skip(!articleId, 'No article with galleys found');

    await loginAsAdmin(page);
    await page.goto(QA_URL);
    await page.waitForLoadState('domcontentloaded');

    // Wait for PDF to render (canvas elements appear)
    const canvas = page.locator('#pdf-container canvas').first();
    await expect(canvas).toBeVisible({ timeout: 30_000 });

    // Page indicator should update
    const pageInfo = await page.locator('#pdf-page-info').textContent();
    expect(pageInfo).toMatch(/page/i);

    await page.screenshot({
      path: 'e2e/screenshots/qa-splits-with-content.png',
      fullPage: false,
    });
  });

  // ── HTML galley + classification ──

  test('renders HTML galley in right pane', async ({ page }) => {
    test.skip(!articleId, 'No article with galleys found');

    await loginAsAdmin(page);
    await page.goto(QA_URL);

    // Wait for HTML to load (either iframe or content div)
    const htmlPane = page.locator('.qa-right');
    await expect(htmlPane).toBeVisible();

    // Wait for loading to complete
    await expect(page.locator('#html-content .qa-loading')).not.toBeVisible({
      timeout: 15_000,
    });

    // HTML content or iframe should be present
    const hasIframe = await page.locator('.qa-html-iframe').count();
    const hasContent = await page.locator('#html-content p').count();
    expect(hasIframe + hasContent).toBeGreaterThan(0);
  });

  test('displays end-matter classification section', async ({ page }) => {
    test.skip(!articleId, 'No article with galleys found');

    await loginAsAdmin(page);
    await page.goto(QA_URL);
    await expect(page.locator('#qa-title')).not.toHaveText('Loading...');

    // End-matter heading should always be visible
    await expect(page.locator('.qa-endmatter')).toBeVisible();
    await expect(page.locator('.qa-endmatter-heading')).toHaveText(
      'End-Matter Classification',
    );

    // Wait for classification API to complete — section is either
    // visible with items or hidden entirely (no end-matter)
    await page.waitForTimeout(3000);

    // If pills exist, verify structure
    const pills = page.locator('.qa-pill');
    const pillCount = await pills.count();
    if (pillCount > 0) {
      const pillText = await pills.first().textContent();
      expect(['Reference', 'Note', 'Bio', 'Provenance']).toContain(pillText);

      await page.screenshot({
        path: 'e2e/screenshots/qa-splits-endmatter.png',
        fullPage: false,
      });
    }
  });

  // ── Navigation ──

  test('sequential navigation with buttons', async ({ page }) => {
    test.skip(!articleId, 'No article with galleys found');

    await loginAsAdmin(page);
    await page.goto(QA_URL);
    await expect(page.locator('#qa-title')).not.toHaveText('Loading...', { timeout: 10_000 });

    const firstTitle = await page.locator('#qa-title').textContent();

    const nextBtn = page.locator('#btn-next');
    if (await nextBtn.isEnabled()) {
      await nextBtn.click();
      await expect(page.locator('#qa-title')).not.toHaveText(firstTitle!, { timeout: 10_000 });

      const secondTitle = await page.locator('#qa-title').textContent();
      if (secondTitle !== firstTitle) {
        await page.locator('#btn-prev').click();
        await expect(page.locator('#qa-title')).toHaveText(firstTitle!, { timeout: 10_000 });
      }
    }
  });

  test('keyboard navigation with arrow keys', async ({ page }) => {
    test.skip(!articleId, 'No article with galleys found');

    await loginAsAdmin(page);
    await page.goto(QA_URL);
    await expect(page.locator('#qa-title')).not.toHaveText('Loading...', { timeout: 10_000 });

    const firstTitle = await page.locator('#qa-title').textContent();

    await page.keyboard.press('ArrowRight');
    await expect(page.locator('#qa-title')).not.toHaveText(firstTitle!, { timeout: 10_000 });

    await page.keyboard.press('ArrowLeft');
    await expect(page.locator('#qa-title')).toHaveText(firstTitle!, { timeout: 10_000 });
  });

  test('? key shows keyboard shortcuts help', async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(QA_URL);
    await page.waitForLoadState('domcontentloaded');

    // Press ?
    await page.keyboard.press('?');

    const helpOverlay = page.locator('.qa-help-overlay');
    await expect(helpOverlay).toBeVisible();

    // Should list shortcuts
    await expect(page.locator('.qa-help-box')).toContainText('Keyboard Shortcuts');
    await expect(page.locator('.qa-help-box')).toContainText('Approve');

    // Wait for overlay animation to complete before screenshot
    await page.waitForTimeout(400);
    await page.screenshot({
      path: 'e2e/screenshots/qa-splits-help.png',
      fullPage: false,
    });

    // Pressing any key dismisses
    await page.keyboard.press('Escape');
    await expect(helpOverlay).not.toBeVisible();
  });

  // ── Review actions ──

  test('approve an article with keyboard shortcut', async ({ page }) => {
    test.skip(!articleId, 'No article with galleys found');

    await loginAsAdmin(page);
    await page.goto(QA_URL);
    await expect(page.locator('#qa-title')).not.toHaveText('Loading...');

    const titleBefore = await page.locator('#qa-title').textContent();

    // Press A to approve — auto-advances to next article
    await page.keyboard.press('a');

    // Should advance to next article
    await expect(page.locator('#qa-title')).not.toHaveText(titleBefore!, { timeout: 10_000 });

    await page.screenshot({
      path: 'e2e/screenshots/qa-splits-approved.png',
      fullPage: false,
    });
  });

  test('reject an article with comment', async ({ page }) => {
    test.skip(!articleId, 'No article with galleys found');

    await loginAsAdmin(page);
    await page.goto(QA_URL);
    await expect(page.locator('#qa-title')).not.toHaveText('Loading...');

    // Navigate to second article (first may have been approved above)
    const currentTitle = await page.locator('#qa-title').textContent();
    await page.keyboard.press('ArrowRight');
    await expect(page.locator('#qa-title')).not.toHaveText(currentTitle!, { timeout: 10_000 });

    // Press R to open reject input
    await page.keyboard.press('r');
    const commentInput = page.locator('#reject-comment');
    await expect(commentInput).toBeVisible();

    // Type comment and submit
    const titleBefore = await page.locator('#qa-title').textContent();
    await commentInput.fill('e2e-test-rejection: bad split on page 3');
    await page.keyboard.press('Control+Enter');

    // Should auto-advance to next article
    await expect(page.locator('#qa-title')).not.toHaveText(titleBefore!, { timeout: 10_000 });

    await page.screenshot({
      path: 'e2e/screenshots/qa-splits-rejected.png',
      fullPage: false,
    });
  });

  test('reject without comment shows error', async ({ page }) => {
    test.skip(!articleId, 'No article with galleys found');

    await loginAsAdmin(page);
    await page.goto(QA_URL);
    await expect(page.locator('#qa-title')).not.toHaveText('Loading...');

    // Open reject input
    await page.locator('#btn-reject').click();
    const commentInput = page.locator('#reject-comment');
    await expect(commentInput).toBeVisible();

    // Try to submit empty
    await page.locator('#btn-submit-reject').click();

    // Error toast
    const toast = page.locator('.qa-toast-error');
    await expect(toast).toBeVisible({ timeout: 5000 });
    await expect(toast).toContainText('Comment required');
  });

  // ── Progress tracking ──

  test('progress counter updates after reviews', async ({ page }) => {
    test.skip(!articleId, 'No article with galleys found');

    await loginAsAdmin(page);
    await page.goto(QA_URL);
    await expect(page.locator('#qa-title')).not.toHaveText('Loading...');

    const progress = page.locator('#qa-progress');
    await expect(progress).toBeVisible();

    const text = await progress.textContent();
    expect(text).toMatch(/\d+ total/);
  });

  // ── API endpoints ──

  test('API requires authentication', async ({ page }) => {
    // Direct API call without login
    const response = await page.goto(`${API_BASE}/articles`);
    expect(response?.status()).toBe(401);
  });

  test('random-unreviewed navigation works', async ({ page }) => {
    test.skip(!articleId, 'No article with galleys found');

    await loginAsAdmin(page);
    await page.goto(QA_URL);
    await expect(page.locator('#qa-title')).not.toHaveText('Loading...', { timeout: 10_000 });

    const titleBefore = await page.locator('#qa-title').textContent();
    await page.locator('#btn-random').click();

    // Wait for either a title change or a toast (all reviewed)
    await expect(
      page.locator('#qa-title:not(:text("Loading...")), .qa-toast').first(),
    ).toBeVisible({ timeout: 10_000 });

    const titleAfter = await page.locator('#qa-title').textContent();
    expect(titleAfter).toBeTruthy();
  });

  test('problem-case navigation works', async ({ page }) => {
    test.skip(!articleId, 'No article with galleys found');

    await loginAsAdmin(page);
    await page.goto(QA_URL);
    await expect(page.locator('#qa-title')).not.toHaveText('Loading...', { timeout: 10_000 });

    await page.locator('#btn-problem').click();

    // Wait for either navigation or toast
    await expect(
      page.locator('.qa-toast').first(),
    ).toBeVisible({ timeout: 10_000 });

    const title = await page.locator('#qa-title').textContent();
    expect(title).toBeTruthy();
  });
});
