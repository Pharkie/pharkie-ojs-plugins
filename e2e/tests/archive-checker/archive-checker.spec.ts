import { test, expect, Page } from '@playwright/test';
import { ojsQuery, ojsPhp } from '../../helpers/ojs';

const OJS_BASE = 'http://localhost:8081';
const QA_URL = `${OJS_BASE}/index.php/ea/archive-checker`;
const API_BASE = `${OJS_BASE}/index.php/ea/api/v1/archive-checker`;

const ADMIN_USER = process.env.OJS_ADMIN_USER ?? 'admin';
const ADMIN_PASS = process.env.OJS_ADMIN_PASSWORD ?? '';

async function loginAsAdmin(page: Page): Promise<void> {
  await page.goto(`${OJS_BASE}/index.php/ea/login`);
  await page.fill('input[name="username"]', ADMIN_USER);
  await page.fill('input[name="password"]', ADMIN_PASS);
  await page.click('button[type="submit"], input[type="submit"]');
  await page.waitForURL((url) => !url.pathname.includes('/login'), { timeout: 15_000 });
}

function qaTableExists(): boolean {
  const out = ojsQuery(
    `SELECT COUNT(*) FROM information_schema.tables
     WHERE table_schema = DATABASE() AND table_name = 'archive_checker_reviews'`,
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

function findArticleWithCitationDois(): number | null {
  const out = ojsQuery(`
    SELECT s.submission_id
    FROM citations c
    JOIN citation_settings cs ON c.citation_id = cs.citation_id
      AND cs.setting_name = 'crossref::doi'
    JOIN publications p ON c.publication_id = p.publication_id
    JOIN submissions s ON s.current_publication_id = p.publication_id
    JOIN publication_galleys g1 ON g1.publication_id = p.publication_id
    JOIN submission_files sf1 ON g1.submission_file_id = sf1.submission_file_id
    JOIN files f1 ON sf1.file_id = f1.file_id AND f1.mimetype = 'text/html'
    WHERE s.context_id = (SELECT journal_id FROM journals WHERE path = 'ea' LIMIT 1)
    GROUP BY s.submission_id
    HAVING COUNT(cs.citation_id) >= 2
    LIMIT 1
  `);
  const id = parseInt(out.trim(), 10);
  return isNaN(id) ? null : id;
}

function cleanupTestReviews(): void {
  if (qaTableExists()) {
    ojsQuery(`DELETE FROM archive_checker_reviews WHERE comment LIKE 'e2e-test-%'`);
  }
}

// ─────────────────────────────────────────────────────────────────
// Tests
// ─────────────────────────────────────────────────────────────────

test.describe('Archive Checker plugin', () => {
  let articleId: number | null;

  test.beforeAll(() => {
    articleId = findArticleWithGalleys();
  });

  // Suppress first-visit help overlay in tests
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('ac-help-seen', '1');
    });
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
    await expect(page.locator('.ac-layout')).toBeVisible();
    await expect(page.locator('.ac-drawer')).toBeVisible();
    await expect(page.locator('.ac-left')).toBeVisible();
    await expect(page.locator('.ac-right')).toBeVisible();

    // No OJS navigation chrome
    await expect(page.locator('.pkp_navigation')).not.toBeVisible();
  });

  test('displays article metadata and status badge', async ({ page }) => {
    test.skip(!articleId, 'No article with galleys found');

    await loginAsAdmin(page);
    await page.goto(QA_URL);

    // Wait for Alpine to populate article metadata
    const metaTitle = page.locator('.ac-meta-title');
    await expect(metaTitle).not.toBeEmpty({ timeout: 15_000 });

    const titleText = await metaTitle.textContent();
    expect(titleText).toBeTruthy();

    // Status badge visible in bottom bar
    await expect(page.locator('.ac-badge')).toBeVisible();
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

  test('PDF text layer renders for selection', async ({ page }) => {
    test.skip(!articleId, 'No article with galleys found');

    await loginAsAdmin(page);
    await page.goto(`${QA_URL}?id=${articleId}`);

    // Wait for text layer to render (pdf.js v5 TextLayer class)
    const textSpan = page.locator('#pdf-container .textLayer span').first();
    await expect(textSpan).toBeAttached({ timeout: 60_000 });

    // Text layer should have multiple spans with content
    const spanCount = await page.locator('#pdf-container .textLayer span').count();
    expect(spanCount).toBeGreaterThan(0);
  });

  test('PDF search finds and highlights text', async ({ page }) => {
    test.skip(!articleId, 'No article with galleys found');

    await loginAsAdmin(page);
    await page.goto(`${QA_URL}?id=${articleId}`);

    // Wait for PDF text layer
    await expect(page.locator('#pdf-container .textLayer span').first()).toBeAttached({ timeout: 60_000 });

    // Open search via the toggle button
    await page.click('.ac-pdf-search-toggle');
    const searchInput = page.locator('#pdf-search-input');
    await expect(searchInput).toBeVisible();

    // Search for a common word — "the" should appear in most articles
    await searchInput.fill('the');
    await page.waitForTimeout(500); // debounce

    // Should have highlights (official pdf.js .highlight class inside text layer)
    const highlights = page.locator('.textLayer .highlight');
    await expect(highlights.first()).toBeAttached({ timeout: 5000 });
    const highlightCount = await highlights.count();
    expect(highlightCount).toBeGreaterThan(0);

    // Search info should show match count
    const info = await page.locator('.ac-pdf-search-info').textContent();
    expect(info).toMatch(/\d+ \/ \d+/);

    // Escape should close search and clear highlights
    await searchInput.press('Escape');
    await expect(searchInput).not.toBeVisible();
    await expect(page.locator('.textLayer .highlight')).toHaveCount(0);
  });

  test('PDF search navigates between matches', async ({ page }) => {
    test.skip(!articleId, 'No article with galleys found');

    await loginAsAdmin(page);
    await page.goto(`${QA_URL}?id=${articleId}`);
    await expect(page.locator('#pdf-container .textLayer span').first()).toBeAttached({ timeout: 60_000 });

    await page.click('.ac-pdf-search-toggle');
    const searchInput = page.locator('#pdf-search-input');
    await searchInput.fill('the');
    await page.waitForTimeout(1500);

    // Should show "1 / N" initially
    const info1 = await page.locator('.ac-pdf-search-info').textContent();
    expect(info1).toMatch(/^1 \/ \d+$/);

    // Should have one selected highlight
    await expect(page.locator('.highlight.selected')).toHaveCount(1);

    // Enter advances to next match
    await searchInput.press('Enter');
    await page.waitForTimeout(500);
    const info2 = await page.locator('.ac-pdf-search-info').textContent();
    expect(info2).toMatch(/^2 \/ \d+$/);

    // Shift+Enter goes back
    await searchInput.press('Shift+Enter');
    await page.waitForTimeout(500);
    const info3 = await page.locator('.ac-pdf-search-info').textContent();
    expect(info3).toMatch(/^1 \/ \d+$/);
  });

  // ── HTML galley ──

  test('renders HTML galley in right pane', async ({ page }) => {
    test.skip(!articleId, 'No article with galleys found');

    await loginAsAdmin(page);
    await page.goto(QA_URL);

    const htmlPane = page.locator('.ac-right');
    await expect(htmlPane).toBeVisible();

    // Wait for HTML loading to complete
    await expect(page.locator('.ac-html-content .ac-loading')).not.toBeVisible({
      timeout: 15_000,
    });

    // HTML content should have paragraphs
    const pCount = await page.locator('.ac-html-content p').count();
    expect(pCount).toBeGreaterThan(0);
  });

  // ── Article metadata header ──

  test('displays article metadata above HTML galley', async ({ page }) => {
    test.skip(!articleId, 'No article with galleys found');

    await loginAsAdmin(page);
    await page.goto(QA_URL);
    await expect(page.locator('.ac-meta-title')).not.toBeEmpty({ timeout: 15_000 });

    // Metadata header should be visible
    await expect(page.locator('.ac-article-meta')).toBeVisible();
    await expect(page.locator('.ac-meta-title')).toBeVisible();
    await expect(page.locator('.ac-meta-issue')).toBeVisible();

    // Article ID should be visible
    const metaId = page.locator('.ac-meta-id');
    await expect(metaId).toBeVisible();
    await expect(metaId).toContainText('Article #');
  });

  // ── End-matter classification ──

  test('displays classification from OJS data', async ({ page }) => {
    test.skip(!articleId, 'No article with galleys found');

    await loginAsAdmin(page);
    await page.goto(QA_URL);
    await expect(page.locator('.ac-meta-title')).not.toBeEmpty({ timeout: 15_000 });

    // Wait for classification to load
    await page.waitForTimeout(3000);

    // Classification panel may or may not be visible depending on article
    const endmatter = page.locator('.ac-endmatter');
    if (await endmatter.isVisible()) {
      // Should have classification heading
      await expect(page.locator('.ac-endmatter-heading')).toHaveText(
        'End-Matter Classification',
      );

      // Pills should show counts
      const pills = page.locator('.ac-pill');
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
    await expect(page.locator('.ac-meta-title')).not.toBeEmpty({ timeout: 15_000 });

    // Sidebar article list should be populated
    const items = page.locator('.ac-drawer-item');
    await expect(items.first()).toBeVisible({ timeout: 10_000 });
    const itemCount = await items.count();
    expect(itemCount).toBeGreaterThan(0);

    // Search input should be present
    await expect(page.locator('.ac-drawer-search')).toBeVisible();

    // Filter pills should be present
    const pills = page.locator('.ac-drawer-pill');
    expect(await pills.count()).toBeGreaterThan(0);
  });

  test('sidebar search filters articles', async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(QA_URL);
    await expect(page.locator('.ac-meta-title')).not.toBeEmpty({ timeout: 15_000 });

    const initialCount = await page.locator('.ac-drawer-item').count();

    // Type a search query
    await page.fill('.ac-drawer-search', 'editorial');
    await page.waitForTimeout(500);

    const filteredCount = await page.locator('.ac-drawer-item').count();
    expect(filteredCount).toBeLessThan(initialCount);
    expect(filteredCount).toBeGreaterThan(0);

    // Clear search
    await page.click('.ac-search-clear');
    await page.waitForTimeout(500);
    const resetCount = await page.locator('.ac-drawer-item').count();
    expect(resetCount).toBe(initialCount);
  });

  test('sidebar search by article ID finds the article', async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(QA_URL);
    await expect(page.locator('.ac-meta-title')).not.toBeEmpty({ timeout: 15_000 });

    // Search by submission ID — numeric queries must match exact ID only
    await page.fill('.ac-drawer-search', '8994');
    await page.waitForTimeout(500);

    const items = page.locator('.ac-drawer-item');
    await expect(items).toHaveCount(1);
    await expect(items.first()).toContainText('[id: 8994]');

    // Verify numeric search doesn't match text content in other articles
    await page.fill('.ac-drawer-search', '99999');
    await page.waitForTimeout(500);
    await expect(page.locator('.ac-drawer-item')).toHaveCount(0);
  });

  // ── Reviewer pills ──

  test('reviewer pills appear and filter the sidebar', async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(QA_URL);
    await expect(page.locator('.ac-meta-title')).not.toBeEmpty({ timeout: 15_000 });

    const totalCount = await page.locator('.ac-drawer-item').count();

    // Reviewer pills should exist (at least one of "By me" / "By others")
    const reviewerPills = page.locator('.ac-drawer-pill-reviewer');
    const pillCount = await reviewerPills.count();
    expect(pillCount).toBeGreaterThan(0);

    // Each pill should show a label and count
    for (let i = 0; i < pillCount; i++) {
      const text = await reviewerPills.nth(i).textContent();
      expect(text).toMatch(/\(\d+\)/);
    }

    // Click the first reviewer pill — should filter the sidebar
    await reviewerPills.first().click();
    await page.waitForTimeout(300);
    const filteredCount = await page.locator('.ac-drawer-item').count();
    expect(filteredCount).toBeLessThan(totalCount);
    expect(filteredCount).toBeGreaterThan(0);

    // Pill should be active
    await expect(reviewerPills.first()).toHaveClass(/active/);
  });

  test('reviewer pills combine with status pills', async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(QA_URL);
    await expect(page.locator('.ac-meta-title')).not.toBeEmpty({ timeout: 15_000 });

    // Click "Approved" status pill
    const approvedPill = page.locator('.ac-drawer-pill-status', { hasText: 'Approved' });
    if (await approvedPill.count() > 0) {
      await approvedPill.click();
      await page.waitForTimeout(300);
      const afterStatusFilter = await page.locator('.ac-drawer-item').count();

      // Now also click a reviewer pill — should further narrow results
      const reviewerPills = page.locator('.ac-drawer-pill-reviewer');
      if (await reviewerPills.count() > 0) {
        await reviewerPills.first().click();
        await page.waitForTimeout(300);
        const afterBothFilters = await page.locator('.ac-drawer-item').count();
        expect(afterBothFilters).toBeLessThanOrEqual(afterStatusFilter);
      }
    }
  });

  test('clear filters resets reviewer pills', async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(QA_URL);
    await expect(page.locator('.ac-meta-title')).not.toBeEmpty({ timeout: 15_000 });

    const totalCount = await page.locator('.ac-drawer-item').count();

    // Click a reviewer pill to filter
    const reviewerPills = page.locator('.ac-drawer-pill-reviewer');
    if (await reviewerPills.count() > 0) {
      await reviewerPills.first().click();
      await page.waitForTimeout(300);

      // Click "Clear all filters"
      await page.click('.ac-drawer-clear');
      await page.waitForTimeout(300);

      const resetCount = await page.locator('.ac-drawer-item').count();
      expect(resetCount).toBe(totalCount);

      // Pill should no longer be active
      await expect(reviewerPills.first()).not.toHaveClass(/active/);
    }
  });

  // ── Filter consistency ──

  test('status pill counts sum to total articles', async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(QA_URL);
    await expect(page.locator('.ac-meta-title')).not.toBeEmpty({ timeout: 15_000 });

    const sidebarCount = await page.locator('.ac-drawer-item').count();

    const statusPills = page.locator('.ac-drawer-pill-status');
    let pillSum = 0;
    for (let i = 0; i < await statusPills.count(); i++) {
      const text = await statusPills.nth(i).textContent();
      const match = text?.match(/\((\d+)\)/);
      if (match) pillSum += parseInt(match[1], 10);
    }
    expect(pillSum).toBe(sidebarCount);
  });

  test('section pill counts sum to total articles', async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(QA_URL);
    await expect(page.locator('.ac-meta-title')).not.toBeEmpty({ timeout: 15_000 });

    const sidebarCount = await page.locator('.ac-drawer-item').count();

    const sectionPills = page.locator('.ac-drawer-pill-section');
    let pillSum = 0;
    for (let i = 0; i < await sectionPills.count(); i++) {
      const text = await sectionPills.nth(i).textContent();
      const match = text?.match(/\((\d+)\)/);
      if (match) pillSum += parseInt(match[1], 10);
    }
    expect(pillSum).toBe(sidebarCount);
  });

  test('reviewer pill counts plus unchecked equal total articles', async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(QA_URL);
    await expect(page.locator('.ac-meta-title')).not.toBeEmpty({ timeout: 15_000 });

    const sidebarCount = await page.locator('.ac-drawer-item').count();

    // Sum reviewer pill counts (By me + By others)
    const reviewerPills = page.locator('.ac-drawer-pill-reviewer');
    let reviewedSum = 0;
    for (let i = 0; i < await reviewerPills.count(); i++) {
      const text = await reviewerPills.nth(i).textContent();
      const match = text?.match(/\((\d+)\)/);
      if (match) reviewedSum += parseInt(match[1], 10);
    }

    // Unchecked count from the Unchecked status pill
    const uncheckedPill = page.locator('.ac-drawer-pill-status', { hasText: 'Unchecked' });
    let uncheckedCount = 0;
    if (await uncheckedPill.count() > 0) {
      const text = await uncheckedPill.textContent();
      const match = text?.match(/\((\d+)\)/);
      if (match) uncheckedCount = parseInt(match[1], 10);
    }

    expect(reviewedSum + uncheckedCount).toBe(sidebarCount);
  });

  test('progress bar counts match API totals', async ({ page }) => {
    await loginAsAdmin(page);

    const response = await page.request.get(`${API_BASE}/articles`);
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    const counts = data.counts;

    // Server-side counts must sum to total
    const statusSum = (counts.approved || 0) + (counts.needs_fix || 0)
      + (counts.recheck || 0) + (counts.deferred || 0)
      + (counts.unreviewed || 0) + (counts.invalidated || 0);
    expect(statusSum).toBe(counts.total);

    // Article count must match total
    expect(data.articles.length).toBe(counts.total);

    // Count statuses from article data — must match server counts
    const fromArticles: Record<string, number> = {};
    for (const a of data.articles) {
      fromArticles[a.status] = (fromArticles[a.status] || 0) + 1;
    }
    expect(fromArticles['approved'] || 0).toBe(counts.approved || 0);
    expect(fromArticles['needs_fix'] || 0).toBe(counts.needs_fix || 0);
    expect(fromArticles['recheck'] || 0).toBe(counts.recheck || 0);
    expect(fromArticles['deferred'] || 0).toBe(counts.deferred || 0);
    expect(fromArticles['unreviewed'] || 0).toBe(counts.unreviewed || 0);

    // Every reviewed article must have a reviewer, every unreviewed must not
    for (const a of data.articles) {
      if (a.status === 'unreviewed') {
        expect(a.reviewer).toBeFalsy();
      } else {
        expect(a.reviewer).toBeTruthy();
      }
    }

    // Section counts must sum to total
    const sectionCounts: Record<string, number> = {};
    for (const a of data.articles) {
      sectionCounts[a.section] = (sectionCounts[a.section] || 0) + 1;
    }
    const sectionSum = Object.values(sectionCounts).reduce((s, v) => s + v, 0);
    expect(sectionSum).toBe(counts.total);
  });

  test('reviewer pill count matches sidebar after click', async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(QA_URL);
    await expect(page.locator('.ac-meta-title')).not.toBeEmpty({ timeout: 15_000 });

    const reviewerPills = page.locator('.ac-drawer-pill-reviewer');
    if (await reviewerPills.count() > 0) {
      const pillText = await reviewerPills.first().textContent();
      const match = pillText?.match(/\((\d+)\)/);
      const expectedCount = match ? parseInt(match[1], 10) : 0;

      await reviewerPills.first().click();
      await page.waitForTimeout(300);

      const sidebarCount = await page.locator('.ac-drawer-item').count();
      expect(sidebarCount).toBe(expectedCount);
    }
  });

  test('reported pill filters to reported articles', async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(QA_URL);
    await expect(page.locator('.ac-meta-title')).not.toBeEmpty({ timeout: 15_000 });

    const reportedPill = page.locator('.ac-drawer-pill-status', { hasText: 'Reported' });
    if (await reportedPill.count() > 0) {
      const pillText = await reportedPill.textContent();
      const match = pillText?.match(/\((\d+)\)/);
      const expectedCount = match ? parseInt(match[1], 10) : 0;

      await reportedPill.click();
      await page.waitForTimeout(300);

      const sidebarCount = await page.locator('.ac-drawer-item').count();
      expect(sidebarCount).toBe(expectedCount);
      expect(sidebarCount).toBeGreaterThan(0);
    }
  });

  test('content filtered pill shows only filtered articles', async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(QA_URL);
    await expect(page.locator('.ac-meta-title')).not.toBeEmpty({ timeout: 15_000 });

    const countBefore = await page.locator('.ac-drawer-item').count();

    // Click content filtered pill to show ONLY content-filtered articles
    const cfPill = page.locator('.ac-drawer-pill-filtered');
    if (await cfPill.count() > 0) {
      await cfPill.click();
      await page.waitForTimeout(300);

      const countAfter = await page.locator('.ac-drawer-item').count();
      // Should show fewer articles (only the ~40 content-filtered ones)
      expect(countAfter).toBeLessThan(countBefore);
      expect(countAfter).toBeGreaterThan(0);

      // Click again to deactivate — should restore full list
      await cfPill.click();
      await page.waitForTimeout(300);
      const countRestored = await page.locator('.ac-drawer-item').count();
      expect(countRestored).toBe(countBefore);
    }
  });

  // ── Navigation ──

  test('navigation by clicking sidebar items', async ({ page }) => {
    test.skip(!articleId, 'No article with galleys found');

    await loginAsAdmin(page);
    await page.goto(QA_URL);
    await expect(page.locator('.ac-meta-title')).not.toBeEmpty({ timeout: 15_000 });

    const firstTitle = await page.locator('.ac-meta-title').textContent();

    // Click second article in sidebar
    const secondItem = page.locator('.ac-drawer-item').nth(1);
    if (await secondItem.count() > 0) {
      await secondItem.click();
      await expect(page.locator('.ac-meta-title')).not.toHaveText(firstTitle!, { timeout: 10_000 });
    }
  });

  test('keyboard navigation with arrow keys', async ({ page }) => {
    test.skip(!articleId, 'No article with galleys found');

    await loginAsAdmin(page);
    await page.goto(QA_URL);
    await expect(page.locator('.ac-meta-title')).not.toBeEmpty({ timeout: 15_000 });

    const firstTitle = await page.locator('.ac-meta-title').textContent();

    await page.keyboard.press('ArrowRight');
    await expect(page.locator('.ac-meta-title')).not.toHaveText(firstTitle!, { timeout: 10_000 });

    await page.keyboard.press('ArrowLeft');
    await expect(page.locator('.ac-meta-title')).toHaveText(firstTitle!, { timeout: 10_000 });
  });

  // ── Reviews ──

  test('approve button submits review', async ({ page }) => {
    test.skip(!articleId, 'No article with galleys found');

    await loginAsAdmin(page);
    await page.goto(`${QA_URL}?id=${articleId}`);
    await expect(page.locator('.ac-meta-title')).not.toBeEmpty({ timeout: 15_000 });

    // Click approve
    await page.click('.ac-btn-approve');

    // Should auto-advance (title changes) or badge updates
    await page.waitForTimeout(2000);
  });

  test('report problem opens form and submits', async ({ page }) => {
    test.skip(!articleId, 'No article with galleys found');

    await loginAsAdmin(page);
    await page.goto(`${QA_URL}?id=${articleId}`);
    await expect(page.locator('.ac-meta-title')).not.toBeEmpty({ timeout: 15_000 });

    // Click Report Problem
    await page.click('.ac-btn-reject');

    // Textarea should appear
    const textarea = page.locator('.ac-textarea');
    await expect(textarea).toBeVisible();

    // Type a comment and submit with Ctrl+Enter
    await textarea.fill('e2e-test-fix-request');
    await textarea.press('Control+Enter');

    // Should show "Saved" confirmation
    await page.waitForTimeout(2000);

    // Restore article to approved state so the test doesn't leave it as reported
    await page.goto(`${QA_URL}?id=${articleId}`);
    await expect(page.locator('.ac-meta-title')).not.toBeEmpty({ timeout: 15_000 });
    await page.click('.ac-btn-approve');
    await page.waitForTimeout(1000);
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

  // ── Citation DOIs ──

  test('classification API includes doi field in references', async ({ page }) => {
    const doiArticleId = findArticleWithCitationDois();
    test.skip(!doiArticleId, 'No article with citation DOIs found');

    await loginAsAdmin(page);

    const response = await page.request.get(
      `${API_BASE}/articles/${doiArticleId}/classification`,
    );
    expect(response.ok()).toBeTruthy();

    const data = await response.json();
    expect(data.references.length).toBeGreaterThan(0);

    // Each reference should have text and doi fields
    for (const ref of data.references) {
      expect(ref).toHaveProperty('text');
      expect(ref).toHaveProperty('doi');
    }

    // At least some should have a non-null DOI
    const withDoi = data.references.filter((r: any) => r.doi);
    expect(withDoi.length).toBeGreaterThan(0);

    // DOIs should look like DOIs
    for (const ref of withDoi) {
      expect(ref.doi).toMatch(/^10\./);
    }
  });

  test('reference DOI links render in endmatter', async ({ page }) => {
    const doiArticleId = findArticleWithCitationDois();
    test.skip(!doiArticleId, 'No article with citation DOIs found');

    await loginAsAdmin(page);
    await page.goto(`${QA_URL}?id=${doiArticleId}`);
    await expect(page.locator('.ac-meta-title')).not.toBeEmpty({ timeout: 15_000 });

    // Wait for classification to load
    const endmatter = page.locator('.ac-endmatter');
    await expect(endmatter).toBeVisible({ timeout: 10_000 });

    // DOI links should render
    const doiLinks = page.locator('.ac-endmatter-doi');
    await expect(doiLinks.first()).toBeVisible({ timeout: 5_000 });

    const linkCount = await doiLinks.count();
    expect(linkCount).toBeGreaterThan(0);

    // Each DOI link should have correct href and text
    for (let i = 0; i < linkCount; i++) {
      const link = doiLinks.nth(i);
      const href = await link.getAttribute('href');
      const text = await link.textContent();
      expect(href).toMatch(/^https:\/\/doi\.org\/10\./);
      expect(text).toMatch(/^doi:10\./);
      expect(await link.getAttribute('target')).toBe('_blank');
    }
  });

  test('references pill shows DOI count', async ({ page }) => {
    const doiArticleId = findArticleWithCitationDois();
    test.skip(!doiArticleId, 'No article with citation DOIs found');

    await loginAsAdmin(page);
    await page.goto(`${QA_URL}?id=${doiArticleId}`);
    await expect(page.locator('.ac-meta-title')).not.toBeEmpty({ timeout: 15_000 });

    // Wait for classification
    await expect(page.locator('.ac-endmatter')).toBeVisible({ timeout: 10_000 });

    // Pill should show DOI count like "References (12, 5 DOIs)"
    const pill = page.locator('.ac-pill.ac-pill-reference');
    await expect(pill).toBeVisible();
    const pillText = await pill.textContent();
    expect(pillText).toMatch(/References \(\d+, \d+ DOIs\)/);
  });

  test('references without DOI show no doi link', async ({ page }) => {
    const doiArticleId = findArticleWithCitationDois();
    test.skip(!doiArticleId, 'No article with citation DOIs found');

    await loginAsAdmin(page);
    await page.goto(`${QA_URL}?id=${doiArticleId}`);
    await expect(page.locator('.ac-meta-title')).not.toBeEmpty({ timeout: 15_000 });
    await expect(page.locator('.ac-endmatter')).toBeVisible({ timeout: 10_000 });

    // Total references should exceed DOI links (not all refs have DOIs)
    const totalRefs = await page.locator('.ac-endmatter-item').count();
    const doiLinks = await page.locator('.ac-endmatter-doi').count();
    expect(totalRefs).toBeGreaterThan(doiLinks);
  });

  // ── Article page review CTA ──

  test('review CTA hidden for unauthenticated users', async ({ page }) => {
    test.skip(!articleId, 'No article with galleys found');

    await page.goto(`${OJS_BASE}/index.php/ea/article/view/${articleId}`);
    await expect(page.locator('.ac-review-cta')).not.toBeVisible();
  });

  test('review CTA visible for logged-in users on article pages', async ({ page }) => {
    test.skip(!articleId, 'No article with galleys found');

    await loginAsAdmin(page);
    await page.goto(`${OJS_BASE}/index.php/ea/article/view/${articleId}`);

    const cta = page.locator('.ac-review-cta');
    await expect(cta).toBeVisible({ timeout: 10_000 });

    // Should show progress text
    const text = await cta.textContent();
    expect(text).toMatch(/\d+ down, \d+ to go/i);

    // Should have a link to Archive Checker with random mode
    const link = cta.locator('a[href*="archive-checker"]');
    await expect(link).toBeVisible();
    expect(await link.getAttribute('href')).toContain('mode=random');
    expect(await link.textContent()).toMatch(/review articles/i);
  });
});
