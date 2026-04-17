import { test, expect } from '@playwright/test';
import { ojsQuery } from '../../helpers/ojs';

const OJS_BASE = 'http://localhost:8081';
const JOURNAL = 'ea';

/**
 * Pick an article known to have cached neighbours. The smarter_similar_articles
 * table is the one source of truth — if this query returns nothing, the
 * cache is empty and the whole suite skips (plugin not installed, or
 * offline builder hasn't run).
 */
function findArticleWithCachedNeighbours(): {
  submissionId: number;
  expectedRanks: number;
} | null {
  const out = ojsQuery(`
    SELECT submission_id, COUNT(*) AS ranks
    FROM smarter_similar_articles
    WHERE rank = 1
    GROUP BY submission_id
    ORDER BY submission_id DESC
    LIMIT 1
  `);
  const parts = out.trim().split('\t');
  if (parts.length < 1 || !parts[0]) return null;
  const submissionId = parseInt(parts[0], 10);
  // Rank count for this submission specifically
  const rankOut = ojsQuery(
    `SELECT COUNT(*) FROM smarter_similar_articles WHERE submission_id = ${submissionId}`,
  );
  const expectedRanks = parseInt(rankOut.trim(), 10);
  if (!submissionId || !expectedRanks) return null;
  return { submissionId, expectedRanks };
}

function smarterSimilarArticlesTableExists(): boolean {
  try {
    const out = ojsQuery(
      "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'smarter_similar_articles'",
    );
    return parseInt(out.trim(), 10) > 0;
  } catch {
    return false;
  }
}

test.describe('smarterSimilarArticles plugin — sidebar render', () => {
  test.beforeAll(() => {
    if (!smarterSimilarArticlesTableExists()) {
      test.skip(true, 'smarter_similar_articles table not present — plugin not installed on this target');
    }
  });

  test('sidebar renders on an article with cached neighbours', async ({ page }) => {
    const article = findArticleWithCachedNeighbours();
    test.skip(!article, 'No articles have cache rows yet — run build_smarter_similar_articles.py');

    await page.goto(`${OJS_BASE}/${JOURNAL}/article/view/${article!.submissionId}`);
    await page.waitForLoadState('domcontentloaded');

    // The plugin renders inside a #smarterSimilarArticlesList section
    const sidebar = page.locator('#smarterSimilarArticlesList');
    await expect(
      sidebar,
      `Article ${article!.submissionId} has ${article!.expectedRanks} cache rows; sidebar should render`,
    ).toBeVisible();

    // Should contain the "Related articles" heading
    const heading = sidebar.locator('h2');
    await expect(heading).toContainText(/related articles/i);

    // Should contain the expected number of <li> entries (bounded by cache + access filter).
    // Permission filtering can drop entries — anonymous view of paywalled journal
    // may see fewer than the DB count. Assert at least one, at most the cache count.
    const items = sidebar.locator('ul > li');
    const count = await items.count();
    expect(count, 'sidebar should have at least one item').toBeGreaterThan(0);
    expect(count, 'sidebar should not exceed cache row count').toBeLessThanOrEqual(
      article!.expectedRanks,
    );

    // Each item should contain a link to another article
    for (let i = 0; i < count; i++) {
      const href = await items.nth(i).locator('a').first().getAttribute('href');
      expect(href, `item ${i} should link to an article`).toMatch(/\/article\/view\//);
    }
  });

  test('articles with no cached neighbours render without a sidebar', async ({ page }) => {
    // Find a submission that exists but has no smarter_similar_articles rows.
    const out = ojsQuery(`
      SELECT s.submission_id FROM submissions s
      LEFT JOIN smarter_similar_articles sa ON sa.submission_id = s.submission_id
      WHERE s.status = 3 AND s.current_publication_id IS NOT NULL
        AND sa.submission_id IS NULL
      LIMIT 1
    `);
    const submissionId = parseInt(out.trim(), 10);
    test.skip(!submissionId, 'All articles have cache rows — cannot test no-sidebar path');

    await page.goto(`${OJS_BASE}/${JOURNAL}/article/view/${submissionId}`);
    await page.waitForLoadState('domcontentloaded');

    // The sidebar section should NOT render for articles with no cache rows —
    // plugin deliberately renders nothing rather than showing filler.
    const sidebar = page.locator('#smarterSimilarArticlesList');
    await expect(sidebar).toHaveCount(0);
  });
});
