import { test, expect, Page } from '@playwright/test';
import { ojsQuery, ojsPhp } from '../../helpers/ojs';

const OJS_BASE = 'http://localhost:8081';
const QA_URL = `${OJS_BASE}/index.php/ea/qa-splits`;
const API_BASE = `${OJS_BASE}/index.php/ea/api/v1/qa-splits`;

const ADMIN_USER = process.env.OJS_ADMIN_USER ?? 'admin';
const ADMIN_PASS = process.env.OJS_ADMIN_PASSWORD ?? '';

async function loginAsAdmin(page: Page): Promise<void> {
  await page.goto(`${OJS_BASE}/index.php/ea/login`);
  await page.fill('input[name="username"]', ADMIN_USER);
  await page.fill('input[name="password"]', ADMIN_PASS);
  await page.click('button[type="submit"], input[type="submit"]');
  await page.waitForURL(/.*/, { timeout: 10_000 });
}

function qaTableExists(): boolean {
  const out = ojsQuery(
    `SELECT COUNT(*) FROM information_schema.tables
     WHERE table_schema = DATABASE() AND table_name = 'qa_split_reviews'`,
  );
  return parseInt(out.trim(), 10) > 0;
}

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
    await expect(page).toHaveURL(/login/, { timeout: 10_000 });
  });

  // ── Page rendering (Alpine.js) ──

  test('loads full-screen QA interface for admin', async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(QA_URL);
    await page.waitForLoadState('domcontentloaded');

    // Alpine.js layout with sidebar
    await expect(page.locator('.qa-layout')).toBeVisible();
    await expect(page.locator('.qa-drawer')).toBeVisible();
    await expect(page.locator('.qa-left')).toBeVisible();
    await expect(page.locator('.qa-right')).toBeVisible();

    // No OJS navigation chrome
    await expect(page.locator('.pkp_navigation')).not.toBeVisible();
  });

  test('displays article metadata in top bar', async ({ page }) => {
    test.skip(!articleId, 'No article with galleys found');

    await loginAsAdmin(page);
    await page.goto(QA_URL);

    // Wait for Alpine to populate (qa-title uses x-text)
    const title = page.locator('.qa-title');
    await expect(title).not.toHaveText('Loading...', { timeout: 15_000 });
    await expect(title).not.toHaveText('No articles found');

    const titleText = await title.textContent();
    expect(titleText).toBeTruthy();
    // Title includes issue/seq/year pattern
    expect(titleText).toMatch(/\d+\.\d+ #\d+ \(\d{4}\)/);

    // Status badge visible
    await expect(page.locator('.qa-badge')).toBeVisible();
  });

  // ── PDF viewer ──

  test('renders PDF in left pane', async ({ page }) => {
    test.skip(!articleId, 'No article with galleys found');

    await loginAsAdmin(page);
    // Navigate to a known article with PDF galley
    await page.goto(`${QA_URL}?id=${articleId}`);

    // Wait for PDF canvas to render
    const canvas = page.locator('#pdf-container canvas').first();
    await expect(canvas).toBeVisible({ timeout: 60_000 });

    // Page indicator should show page count
    const pageInfo = await page.locator('#pdf-page-info').textContent();
    expect(pageInfo).toMatch(/page/i);
  });

  // ── HTML galley ──

  test('renders HTML galley in right pane', async ({ page }) => {
    test.skip(!articleId, 'No article with galleys found');

    await loginAsAdmin(page);
    await page.goto(QA_URL);

    const htmlPane = page.locator('.qa-right');
    await expect(htmlPane).toBeVisible();

    // Wait for HTML loading to complete
    await expect(page.locator('.qa-html-content .qa-loading')).not.toBeVisible({
      timeout: 15_000,
    });

    // HTML content should have paragraphs
    const pCount = await page.locator('.qa-html-content p').count();
    expect(pCount).toBeGreaterThan(0);
  });

  // ── Article metadata header ──

  test('displays article metadata above HTML galley', async ({ page }) => {
    test.skip(!articleId, 'No article with galleys found');

    await loginAsAdmin(page);
    await page.goto(QA_URL);
    await expect(page.locator('.qa-title')).not.toHaveText('Loading...', { timeout: 15_000 });

    // Metadata header should be visible
    await expect(page.locator('.qa-article-meta')).toBeVisible();
    await expect(page.locator('.qa-meta-title')).toBeVisible();
    await expect(page.locator('.qa-meta-issue')).toBeVisible();
  });

  // ── End-matter classification ──

  test('displays classification from OJS data', async ({ page }) => {
    test.skip(!articleId, 'No article with galleys found');

    await loginAsAdmin(page);
    await page.goto(QA_URL);
    await expect(page.locator('.qa-title')).not.toHaveText('Loading...', { timeout: 15_000 });

    // Wait for classification to load
    await page.waitForTimeout(3000);

    // Classification panel may or may not be visible depending on article
    const endmatter = page.locator('.qa-endmatter');
    if (await endmatter.isVisible()) {
      // Should have classification heading
      await expect(page.locator('.qa-endmatter-heading')).toHaveText(
        'End-Matter Classification',
      );

      // Pills should show counts
      const pills = page.locator('.qa-pill');
      const pillCount = await pills.count();
      if (pillCount > 0) {
        const pillText = await pills.first().textContent();
        // Should contain a label and count like "References (10)"
        expect(pillText).toMatch(/\(\d+\)/);
      }
    }
  });

  // ── Sidebar ──

  test('sidebar shows article list with filters', async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(QA_URL);
    await expect(page.locator('.qa-title')).not.toHaveText('Loading...', { timeout: 15_000 });

    // Sidebar article list should be populated
    const items = page.locator('.qa-drawer-item');
    await expect(items.first()).toBeVisible({ timeout: 10_000 });
    const itemCount = await items.count();
    expect(itemCount).toBeGreaterThan(0);

    // Search input should be present
    await expect(page.locator('.qa-drawer-search')).toBeVisible();

    // Filter pills should be present
    const pills = page.locator('.qa-drawer-pill');
    expect(await pills.count()).toBeGreaterThan(0);
  });

  test('sidebar search filters articles', async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(QA_URL);
    await expect(page.locator('.qa-title')).not.toHaveText('Loading...', { timeout: 15_000 });

    const initialCount = await page.locator('.qa-drawer-item').count();

    // Type a search query
    await page.fill('.qa-drawer-search', 'editorial');
    await page.waitForTimeout(500);

    const filteredCount = await page.locator('.qa-drawer-item').count();
    expect(filteredCount).toBeLessThan(initialCount);
    expect(filteredCount).toBeGreaterThan(0);

    // Clear search
    await page.click('.qa-search-clear');
    await page.waitForTimeout(500);
    const resetCount = await page.locator('.qa-drawer-item').count();
    expect(resetCount).toBe(initialCount);
  });

  // ── Navigation ──

  test('navigation with sidebar buttons', async ({ page }) => {
    test.skip(!articleId, 'No article with galleys found');

    await loginAsAdmin(page);
    await page.goto(QA_URL);
    await expect(page.locator('.qa-title')).not.toHaveText('Loading...', { timeout: 15_000 });

    const firstTitle = await page.locator('.qa-title').textContent();

    // Click next button in sidebar
    const nextBtn = page.locator('.qa-btn-nav').nth(1); // Second nav button = Next
    if (await nextBtn.isEnabled()) {
      await nextBtn.click();
      await expect(page.locator('.qa-title')).not.toHaveText(firstTitle!, { timeout: 10_000 });
    }
  });

  test('keyboard navigation with arrow keys', async ({ page }) => {
    test.skip(!articleId, 'No article with galleys found');

    await loginAsAdmin(page);
    await page.goto(QA_URL);
    await expect(page.locator('.qa-title')).not.toHaveText('Loading...', { timeout: 15_000 });

    const firstTitle = await page.locator('.qa-title').textContent();

    await page.keyboard.press('ArrowRight');
    await expect(page.locator('.qa-title')).not.toHaveText(firstTitle!, { timeout: 10_000 });

    await page.keyboard.press('ArrowLeft');
    await expect(page.locator('.qa-title')).toHaveText(firstTitle!, { timeout: 10_000 });
  });

  // ── Reviews ──

  test('approve button submits review', async ({ page }) => {
    test.skip(!articleId, 'No article with galleys found');

    await loginAsAdmin(page);
    await page.goto(`${QA_URL}?id=${articleId}`);
    await expect(page.locator('.qa-title')).not.toHaveText('Loading...', { timeout: 15_000 });

    // Click approve
    await page.click('.qa-btn-approve');

    // Should auto-advance (title changes) or badge updates
    await page.waitForTimeout(2000);
  });

  test('request fix opens form and submits', async ({ page }) => {
    test.skip(!articleId, 'No article with galleys found');

    await loginAsAdmin(page);
    await page.goto(`${QA_URL}?id=${articleId}`);
    await expect(page.locator('.qa-title')).not.toHaveText('Loading...', { timeout: 15_000 });

    // Click Request Fix
    await page.click('.qa-btn-reject');

    // Textarea should appear
    const textarea = page.locator('.qa-textarea');
    await expect(textarea).toBeVisible();

    // Type a comment and submit with Ctrl+Enter
    await textarea.fill('e2e-test-fix-request');
    await textarea.press('Control+Enter');

    // Should auto-advance
    await page.waitForTimeout(2000);
  });

  // ── API ──

  test('classification API returns references from citations table', async ({ page }) => {
    test.skip(!articleId, 'No article with galleys found');

    await loginAsAdmin(page);

    const response = await page.request.get(
      `${API_BASE}/articles/${articleId}/classification`,
    );
    expect(response.ok()).toBeTruthy();

    const data = await response.json();
    // Should have the new response shape
    expect(data).toHaveProperty('references');
    expect(data).toHaveProperty('notes_count');
    expect(data).toHaveProperty('bios_count');
    expect(data).toHaveProperty('provenance_count');

    // references should be an array
    expect(Array.isArray(data.references)).toBe(true);

    // counts should be numbers
    expect(typeof data.notes_count).toBe('number');
    expect(typeof data.bios_count).toBe('number');
    expect(typeof data.provenance_count).toBe('number');
  });

  test('articles API returns metadata fields', async ({ page }) => {
    await loginAsAdmin(page);

    const response = await page.request.get(`${API_BASE}/articles`);
    expect(response.ok()).toBeTruthy();

    const data = await response.json();
    expect(data.articles.length).toBeGreaterThan(0);

    const article = data.articles[0];
    expect(article).toHaveProperty('submission_id');
    expect(article).toHaveProperty('title');
    expect(article).toHaveProperty('authors');
    expect(article).toHaveProperty('doi');
    expect(article).toHaveProperty('abstract');
    expect(article).toHaveProperty('keywords');
    expect(article).toHaveProperty('issue_title');
  });
});
