import { test, expect, Page } from '@playwright/test';
import { ojsQuery } from '../../helpers/ojs';

const OJS_BASE = 'http://localhost:8081';
const ADMIN_USER = process.env.OJS_ADMIN_USER ?? 'admin';
const ADMIN_PASS = process.env.OJS_ADMIN_PASSWORD ?? '';

async function loginAsAdmin(page: Page): Promise<void> {
  await page.goto(`${OJS_BASE}/index.php/ea/login`);
  await page.fill('input[name="username"]', ADMIN_USER);
  await page.fill('input[name="password"]', ADMIN_PASS);
  await page.click('button[type="submit"], input[type="submit"]');
  await page.waitForURL(/.*/, { timeout: 10_000 });
}

/**
 * Find an open-access editorial that has an HTML galley labeled "Full Text".
 */
function findOpenAccessEditorialWithHtmlGalley(): {
  submissionId: number;
  issueId: number;
} | null {
  const out = ojsQuery(`
    SELECT s.submission_id, p.issue_id
    FROM submissions s
    JOIN publications p ON p.publication_id = s.current_publication_id
    JOIN publication_galleys g ON g.publication_id = p.publication_id
    WHERE p.access_status = 1
      AND g.label = 'Full Text'
    LIMIT 1
  `);
  const parts = out.trim().split('\t');
  if (parts.length < 2 || !parts[0]) return null;
  return {
    submissionId: parseInt(parts[0], 10),
    issueId: parseInt(parts[1], 10),
  };
}

/**
 * Find a paywalled (non-open-access) article.
 */
function findPaywalledArticle(): number | null {
  const out = ojsQuery(`
    SELECT s.submission_id
    FROM submissions s
    JOIN publications p ON p.publication_id = s.current_publication_id
    WHERE p.access_status != 1
      AND p.status = 3
    LIMIT 1
  `);
  const id = parseInt(out.trim(), 10);
  return isNaN(id) ? null : id;
}

/**
 * Find an article with jats-bios div in its HTML galley.
 */
function findArticleWithBio(): number | null {
  const out = ojsQuery(`
    SELECT s.submission_id
    FROM submissions s
    JOIN publications p ON p.publication_id = s.current_publication_id
    JOIN publication_galleys g ON g.publication_id = p.publication_id
    JOIN submission_files sf ON g.submission_file_id = sf.submission_file_id
    JOIN files f ON sf.file_id = f.file_id
    WHERE f.mimetype = 'text/html' AND p.access_status = 1
    LIMIT 1
  `);
  const id = parseInt(out.trim(), 10);
  return isNaN(id) ? null : id;
}

/**
 * Find an article with citations in the DB.
 */
function findArticleWithCitations(): number | null {
  const out = ojsQuery(`
    SELECT s.submission_id
    FROM submissions s
    JOIN publications p ON p.publication_id = s.current_publication_id
    JOIN citations c ON c.publication_id = p.publication_id
    WHERE p.access_status = 1
    GROUP BY s.submission_id
    HAVING COUNT(c.citation_id) > 0
    LIMIT 1
  `);
  const id = parseInt(out.trim(), 10);
  return isNaN(id) ? null : id;
}

test.describe('Inline HTML Galley plugin', () => {
  let editorial: { submissionId: number; issueId: number } | null;
  let paywalledId: number | null;
  let bioArticleId: number | null;
  let citationArticleId: number | null;

  test.beforeAll(() => {
    editorial = findOpenAccessEditorialWithHtmlGalley();
    paywalledId = findPaywalledArticle();
    bioArticleId = findArticleWithBio();
    citationArticleId = findArticleWithCitations();
  });

  test('editorial shows inline HTML content', async ({ page }) => {
    test.skip(!editorial, 'No open-access editorial with HTML galley found');

    await page.goto(
      `${OJS_BASE}/index.php/ea/article/view/${editorial!.submissionId}`,
    );
    await page.waitForLoadState('domcontentloaded');

    const section = page.locator('.inline-html-galley');
    await expect(section).toBeVisible({ timeout: 10_000 });

    // Should contain actual paragraph content
    const paragraphs = section.locator('p');
    await expect(paragraphs.first()).toBeVisible();
  });

  test('"Full Text" link hidden on article page', async ({ page }) => {
    test.skip(!editorial, 'No open-access editorial with HTML galley found');

    await page.goto(
      `${OJS_BASE}/index.php/ea/article/view/${editorial!.submissionId}`,
    );
    await page.waitForLoadState('domcontentloaded');

    // "Full Text" galley links should be hidden by JS
    const fullTextLinks = page.locator('.obj_galley_link').filter({
      hasText: 'Full Text',
    });
    // Either no links or all hidden
    const count = await fullTextLinks.count();
    for (let i = 0; i < count; i++) {
      await expect(fullTextLinks.nth(i)).toBeHidden();
    }
  });

  test('all galley links hidden on issue TOC', async ({ page }) => {
    test.skip(!editorial, 'No open-access editorial with HTML galley found');

    await page.goto(
      `${OJS_BASE}/index.php/ea/issue/view/${editorial!.issueId}`,
    );
    await page.waitForLoadState('domcontentloaded');

    // Per-article galley links (PDF, HTML, Full Text) should be hidden on the TOC.
    // Readers click the article title to reach the landing page instead.
    // Issue-level PDF galleys remain visible — they're the purchase entry point.
    const articleGalleyLinks = page.locator('.obj_article_summary .obj_galley_link');
    const count = await articleGalleyLinks.count();
    for (let i = 0; i < count; i++) {
      await expect(articleGalleyLinks.nth(i)).toBeHidden();
    }
  });

  test('PDF galley link remains visible on editorial', async ({ page }) => {
    test.skip(!editorial, 'No open-access editorial with HTML galley found');

    await page.goto(
      `${OJS_BASE}/index.php/ea/article/view/${editorial!.submissionId}`,
    );
    await page.waitForLoadState('domcontentloaded');

    // PDF link should still be visible
    const pdfLinks = page.locator('.obj_galley_link').filter({
      hasText: 'PDF',
    });
    const count = await pdfLinks.count();
    if (count > 0) {
      await expect(pdfLinks.first()).toBeVisible();
    }
    // If no PDF galley exists, that's fine — we just verify "Full Text" is hidden
  });

  test('paywalled article has no inline HTML', async ({ page }) => {
    test.skip(!paywalledId, 'No paywalled article found');

    await page.goto(
      `${OJS_BASE}/index.php/ea/article/view/${paywalledId}`,
    );
    await page.waitForLoadState('domcontentloaded');

    const section = page.locator('.inline-html-galley');
    await expect(section).toHaveCount(0);
  });

  // ── Archive notice ──

  test('archive notice shown on open-access article', async ({ page }) => {
    test.skip(!editorial, 'No open-access editorial found');

    await page.goto(
      `${OJS_BASE}/index.php/ea/article/view/${editorial!.submissionId}`,
    );

    // Archive notice should be visible
    const notice = page.locator('text=digitally restored from an archive');
    await expect(notice).toBeVisible({ timeout: 10_000 });
  });

  test('logged-in user sees "request a fix" link in archive notice', async ({ page }) => {
    test.skip(!editorial, 'No open-access editorial found');

    await loginAsAdmin(page);
    await page.goto(
      `${OJS_BASE}/index.php/ea/article/view/${editorial!.submissionId}`,
    );

    const link = page.locator('.ihg-report-link');
    await expect(link).toBeVisible();
    await expect(link).toHaveText('request a fix');
  });

  test('clicking "request a fix" opens form without hiding link text', async ({ page }) => {
    test.skip(!editorial, 'No open-access editorial found');

    await loginAsAdmin(page);
    await page.goto(
      `${OJS_BASE}/index.php/ea/article/view/${editorial!.submissionId}`,
    );

    const link = page.locator('.ihg-report-link');
    await link.click();

    // Form should appear
    const form = page.locator('#ihg-report-form');
    await expect(form).toBeVisible();

    // Link text should still be in the sentence (not display:none)
    const sentence = page.locator('text=request a fix');
    await expect(sentence).toBeVisible();

    // Textarea should be visible
    await expect(page.locator('#ihg-report-text')).toBeVisible();
  });

  // ── Pipeline-extracted back-matter labels ──

  test('jats-bios div has "Author bio" CSS label', async ({ page }) => {
    test.skip(!bioArticleId, 'No open-access article with bio found');

    await loginAsAdmin(page);
    await page.goto(
      `${OJS_BASE}/index.php/ea/article/view/${bioArticleId}`,
    );

    const biosDiv = page.locator('.inline-html-galley .jats-bios');
    // Bio div may or may not exist for this specific article
    const count = await biosDiv.count();
    if (count > 0) {
      await expect(biosDiv.first()).toBeVisible();

      // Check the ::before label renders
      const label = await biosDiv.first().evaluate(el => {
        return window.getComputedStyle(el, '::before').content;
      });
      expect(label).toContain('Author bio');

      // Should have left border styling
      const borderLeft = await biosDiv.first().evaluate(el => {
        return window.getComputedStyle(el).borderLeftStyle;
      });
      expect(borderLeft).toBe('solid');
    }
  });

  test('jats-notes div has "Notes" CSS label', async ({ page }) => {
    test.skip(!bioArticleId, 'No open-access article found');

    await loginAsAdmin(page);
    await page.goto(
      `${OJS_BASE}/index.php/ea/article/view/${bioArticleId}`,
    );

    const notesDiv = page.locator('.inline-html-galley .jats-notes');
    const count = await notesDiv.count();
    if (count > 0) {
      const label = await notesDiv.first().evaluate(el => {
        return window.getComputedStyle(el, '::before').content;
      });
      expect(label).toContain('Notes');
    }
  });
});
