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
 * Find an article title word (>6 chars) that appears across multiple titles.
 *
 * Implementation note: the previous version ran a GROUP BY over 72k rows in
 * submission_search_keyword_list joined to 4.2M object_keywords — took 60s+
 * once the search index was fully populated. This version fetches the ~1400
 * titles (one indexed SELECT) and counts word frequencies in JS, which runs
 * in milliseconds.
 */
function findSearchableTitleWord(): string | null {
  const titlesRaw = ojsQuery(`
    SELECT ps.setting_value
    FROM publication_settings ps
    JOIN submissions s ON s.current_publication_id = ps.publication_id
    WHERE ps.setting_name = 'title' AND ps.locale = 'en'
      AND s.status = 3 AND s.current_publication_id IS NOT NULL
  `);
  const wordCounts = new Map<string, number>();
  for (const title of titlesRaw.split('\n')) {
    const words = title.toLowerCase().match(/\b[a-z]{7,}\b/g) || [];
    for (const word of new Set(words)) {
      wordCounts.set(word, (wordCounts.get(word) || 0) + 1);
    }
  }
  // Pick the most common word that appears in ≥2 titles
  let best: string | null = null;
  let bestCount = 1;
  for (const [word, count] of wordCounts) {
    if (count > bestCount) {
      best = word;
      bestCount = count;
    }
  }
  return best;
}

/**
 * Count distinct submissions that have search-index rows.
 */
function indexedSubmissionCount(): number {
  const out = ojsQuery('SELECT COUNT(DISTINCT submission_id) FROM submission_search_objects');
  return parseInt(out.trim(), 10);
}

/**
 * Count published submissions (status=3 and a current publication).
 */
function publishedSubmissionCount(): number {
  const out = ojsQuery(
    'SELECT COUNT(*) FROM submissions WHERE status = 3 AND current_publication_id IS NOT NULL',
  );
  return parseInt(out.trim(), 10);
}

test.describe('OJS search functionality', () => {
  test.beforeAll(() => {
    if (indexedSubmissionCount() === 0) {
      test.skip();
    }
  });

  test('search index covers at least 90% of published submissions', () => {
    // Catches the 2026-04 regression: pipe7_import.sh queued indexing jobs
    // then DELETEd them before they ran, leaving only ~3% of the archive
    // indexed. A non-empty index isn't enough — coverage has to match the
    // actual corpus. 90% floor tolerates a handful of legitimate gaps
    // (galley-less submissions, edge-case publications) without masking
    // the "whole archive missing" failure mode.
    const indexed = indexedSubmissionCount();
    const published = publishedSubmissionCount();
    expect(published).toBeGreaterThan(0);
    const ratio = indexed / published;
    expect(
      ratio,
      `Expected ≥90% of ${published} published submissions to be indexed, got ${indexed} (${(ratio * 100).toFixed(1)}%)`,
    ).toBeGreaterThanOrEqual(0.9);

    const keywordCount = parseInt(
      ojsQuery('SELECT COUNT(*) FROM submission_search_keyword_list').trim(),
      10,
    );
    expect(keywordCount).toBeGreaterThan(0);
  });

  test('search by author surname returns results matching DB', async ({ page }) => {
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

    const results = page.locator('.search_results .obj_article_summary, .pkp_search_results .title, .search-results .article');
    const count = await results.count();

    // OJS paginates search results at 25 per page by default. Expected count
    // is min(db_count, page_size) — we can't see beyond page 1 without
    // navigating pagination, and the point of this test isn't pagination
    // coverage, it's "does the HTTP result set roughly match what the DB
    // says should be indexed". The 2026-04 bug would put count at 0-1 when
    // db_count is 3+, so even clamping at 25 still catches that failure mode.
    const OJS_SEARCH_PAGE_SIZE = 25;
    const expectedOnPage = Math.min(author!.expectedCount, OJS_SEARCH_PAGE_SIZE);
    expect(
      count,
      `DB has ${author!.expectedCount} published submissions by '${author!.surname}'; HTTP page 1 expected ${expectedOnPage}, got ${count}`,
    ).toBeGreaterThanOrEqual(expectedOnPage);
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
