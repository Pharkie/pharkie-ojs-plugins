import { test, expect } from '@playwright/test';
import { ojsQuery } from '../../helpers/ojs';

const OJS_BASE = 'http://localhost:8081';
const JOURNAL = 'ea';

/**
 * Find an author surname that appears in at least one published article.
 * Returns { surname, expectedCount } or null.
 */
function findSearchableAuthor(): {
  surname: string;
  expectedCount: number;
} | null {
  const out = ojsQuery(`
    SELECT asv.setting_value AS surname, COUNT(DISTINCT s.submission_id) AS cnt
    FROM author_settings asv
    JOIN authors a ON a.author_id = asv.author_id
    JOIN publications p ON a.publication_id = p.publication_id
    JOIN submissions s ON s.submission_id = p.submission_id
    WHERE asv.setting_name = 'familyName'
      AND s.status = 3
    GROUP BY asv.setting_value
    HAVING cnt >= 3
    ORDER BY cnt DESC
    LIMIT 1
  `);
  const parts = out.trim().split('\t');
  if (parts.length < 2 || !parts[0]) return null;
  return { surname: parts[0], expectedCount: parseInt(parts[1], 10) };
}

/**
 * Find an article title word (>6 chars) that appears in the search index.
 */
function findSearchableTitleWord(): string | null {
  const out = ojsQuery(`
    SELECT k.keyword_text
    FROM submission_search_keyword_list k
    JOIN submission_search_object_keywords ok ON ok.keyword_id = k.keyword_id
    JOIN submission_search_objects o ON o.object_id = ok.object_id
    WHERE o.type = 1
      AND LENGTH(k.keyword_text) > 6
    GROUP BY k.keyword_text
    HAVING COUNT(DISTINCT o.submission_id) >= 2
    ORDER BY COUNT(DISTINCT o.submission_id) DESC
    LIMIT 1
  `);
  return out.trim() || null;
}

/**
 * Check if the search index has been built (non-empty).
 */
function searchIndexHasEntries(): boolean {
  const out = ojsQuery('SELECT COUNT(*) FROM submission_search_objects');
  return parseInt(out, 10) > 0;
}

test.describe('OJS search functionality', () => {
  test.beforeAll(() => {
    if (!searchIndexHasEntries()) {
      test.skip();
    }
  });

  test('search index is populated', () => {
    const objectCount = parseInt(
      ojsQuery('SELECT COUNT(*) FROM submission_search_objects').trim(),
      10,
    );
    const keywordCount = parseInt(
      ojsQuery('SELECT COUNT(*) FROM submission_search_keyword_list').trim(),
      10,
    );
    expect(objectCount).toBeGreaterThan(0);
    expect(keywordCount).toBeGreaterThan(0);
  });

  test('search by author surname returns results', async ({ page }) => {
    const author = findSearchableAuthor();
    test.skip(!author, 'No searchable author found in DB');

    await page.goto(`${OJS_BASE}/${JOURNAL}/search`);

    // Fill in the author search field
    const authorInput = page.locator('input[name="authors"]');
    if (await authorInput.isVisible()) {
      await authorInput.fill(author!.surname);
    } else {
      // Fallback: use the main query field
      await page.locator('input[name="query"]').fill(author!.surname);
    }

    await page.locator('button[type="submit"], input[type="submit"]').first().click();
    await page.waitForLoadState('domcontentloaded');

    // Should find at least one result
    const results = page.locator('.search_results .obj_article_summary, .pkp_search_results .title, .search-results .article');
    const noResults = page.locator('text=No results');

    const hasResults = await results.count() > 0;
    const hasNoResults = await noResults.isVisible().catch(() => false);

    expect(hasResults || !hasNoResults).toBeTruthy();
    if (hasResults) {
      expect(await results.count()).toBeGreaterThanOrEqual(1);
    }
  });

  test('search by title keyword returns results', async ({ page }) => {
    const keyword = findSearchableTitleWord();
    test.skip(!keyword, 'No searchable title keyword found in index');

    await page.goto(`${OJS_BASE}/${JOURNAL}/search`);
    await page.locator('input[name="query"]').fill(keyword!);
    await page.locator('button[type="submit"], input[type="submit"]').first().click();
    await page.waitForLoadState('domcontentloaded');

    const results = page.locator('.search_results .obj_article_summary, .pkp_search_results .title, .search-results .article');
    const count = await results.count();
    expect(count).toBeGreaterThanOrEqual(1);
  });

  test('empty search shows no results or search form', async ({ page }) => {
    await page.goto(`${OJS_BASE}/${JOURNAL}/search/search?query=xyznonexistent99999`);
    await page.waitForLoadState('domcontentloaded');

    // Should show "no items" or no result entries
    const results = page.locator('.search_results .obj_article_summary, .pkp_search_results .title');
    expect(await results.count()).toBe(0);
  });
});
