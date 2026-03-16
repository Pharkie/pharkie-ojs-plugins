import { test, expect } from '@playwright/test';
import { ojsQuery } from '../../helpers/ojs';

const OJS_BASE = 'http://localhost:8081';

/**
 * Find an open-access editorial that has an HTML galley labeled "Full Text".
 * Returns { submissionId, issueId } or null if none found.
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
 * Returns submissionId or null.
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

test.describe('Inline HTML Galley plugin', () => {
  let editorial: { submissionId: number; issueId: number } | null;
  let paywalledId: number | null;

  test.beforeAll(() => {
    editorial = findOpenAccessEditorialWithHtmlGalley();
    paywalledId = findPaywalledArticle();
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

  test('"Full Text" link hidden on issue TOC', async ({ page }) => {
    test.skip(!editorial, 'No open-access editorial with HTML galley found');

    await page.goto(
      `${OJS_BASE}/index.php/ea/issue/view/${editorial!.issueId}`,
    );
    await page.waitForLoadState('domcontentloaded');

    // "Full Text" galley links should be hidden on issue page too
    const fullTextLinks = page.locator('.obj_galley_link').filter({
      hasText: 'Full Text',
    });
    const count = await fullTextLinks.count();
    for (let i = 0; i < count; i++) {
      await expect(fullTextLinks.nth(i)).toBeHidden();
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
});
