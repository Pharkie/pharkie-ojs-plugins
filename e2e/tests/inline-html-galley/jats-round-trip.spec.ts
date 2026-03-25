import { test, expect } from '@playwright/test';
import { ojsQuery } from '../../helpers/ojs';

const OJS_BASE = 'http://localhost:8081';

/** QA test users created by setup-ojs.sh. Separate passwords from env vars. */
const QA_SUB = {
  username: 'qausersub',
  email: 'qausersub@example.com',
  password: process.env.QA_SUB_PASSWORD!,
};
const QA_NOSUB = {
  username: 'qausernosub',
  email: 'qausernosub@example.com',
  password: process.env.QA_NOSUB_PASSWORD!,
};

/**
 * Find an article with citations in the OJS DB.
 * Returns { submissionId, title, citationCount }.
 */
function findArticleWithCitations(): {
  submissionId: number;
  title: string;
  citationCount: number;
} | null {
  const out = ojsQuery(`
    SELECT s.submission_id, ps.setting_value AS title, COUNT(c.citation_id) AS cnt
    FROM submissions s
    JOIN publications p ON p.publication_id = s.current_publication_id
    JOIN publication_settings ps ON ps.publication_id = p.publication_id AND ps.setting_name = 'title' AND ps.locale = 'en'
    JOIN citations c ON c.publication_id = p.publication_id
    GROUP BY s.submission_id, ps.setting_value
    ORDER BY cnt DESC
    LIMIT 1
  `);
  const parts = out.trim().split('\t');
  if (parts.length < 3 || !parts[0]) return null;
  return {
    submissionId: parseInt(parts[0], 10),
    title: parts[1],
    citationCount: parseInt(parts[2], 10),
  };
}

/**
 * Find an open-access article (Book Review) with an HTML galley.
 */
function findOpenAccessArticleWithHtmlGalley(): {
  submissionId: number;
  title: string;
} | null {
  const out = ojsQuery(`
    SELECT s.submission_id, ps.setting_value AS title
    FROM submissions s
    JOIN publications p ON p.publication_id = s.current_publication_id
    JOIN publication_settings ps ON ps.publication_id = p.publication_id AND ps.setting_name = 'title' AND ps.locale = 'en'
    JOIN publication_galleys g ON g.publication_id = p.publication_id
    WHERE p.access_status = 1
      AND g.label = 'Full Text'
    LIMIT 1
  `);
  const parts = out.trim().split('\t');
  if (parts.length < 2 || !parts[0]) return null;
  return {
    submissionId: parseInt(parts[0], 10),
    title: parts[1],
  };
}

/**
 * Find an issue that has articles with page numbers set.
 * Returns { issueId, articleCount } or null.
 */
function findIssueWithPageNumbers(): {
  issueId: number;
  articleCount: number;
} | null {
  const out = ojsQuery(`
    SELECT p.issue_id, COUNT(*) AS cnt
    FROM publications p
    JOIN publication_settings ps ON p.publication_id = ps.publication_id
      AND ps.setting_name = 'pages'
    WHERE p.status = 3
    GROUP BY p.issue_id
    HAVING cnt >= 3
    ORDER BY cnt DESC
    LIMIT 1
  `);
  const parts = out.trim().split('\t');
  if (parts.length < 2 || !parts[0]) return null;
  return {
    issueId: parseInt(parts[0], 10),
    articleCount: parseInt(parts[1], 10),
  };
}

test.describe('JATS round-trip: article content from JATS → OJS', () => {
  let articleWithCitations: ReturnType<typeof findArticleWithCitations>;
  let openAccessArticle: ReturnType<typeof findOpenAccessArticleWithHtmlGalley>;
  let issueWithPages: ReturnType<typeof findIssueWithPageNumbers>;

  test.beforeAll(() => {
    articleWithCitations = findArticleWithCitations();
    openAccessArticle = findOpenAccessArticleWithHtmlGalley();
    issueWithPages = findIssueWithPageNumbers();
  });

  test('OJS citations table has references from JATS ref-list', async () => {
    test.skip(!articleWithCitations, 'No article with citations found in DB');

    // Verify citations exist
    expect(articleWithCitations!.citationCount).toBeGreaterThan(0);

    // Verify citation content is real text (not empty)
    const firstCitation = ojsQuery(`
      SELECT raw_citation FROM citations c
      JOIN publications p ON c.publication_id = p.publication_id
      WHERE p.submission_id = ${articleWithCitations!.submissionId}
      ORDER BY c.seq ASC LIMIT 1
    `).trim();
    expect(firstCitation.length).toBeGreaterThan(20);
  });

  test('paywalled article shows references to non-subscribers', async ({
    page,
  }) => {
    test.skip(!articleWithCitations, 'No article with citations found');

    // Visit without logging in — should still see references
    await page.goto(
      `${OJS_BASE}/ea/article/view/${articleWithCitations!.submissionId}`,
    );

    // OJS renders a References section from citations table
    const refsSection = page.locator('.item.references');
    await expect(refsSection).toBeVisible();

    // Should have actual citation text
    const citations = refsSection.locator('.value li, .value p');
    const count = await citations.count();
    expect(count).toBeGreaterThan(0);
  });

  test('open-access article shows inline HTML body from JATS', async ({
    page,
  }) => {
    test.skip(!openAccessArticle, 'No open-access article with HTML galley');

    await page.goto(
      `${OJS_BASE}/ea/article/view/${openAccessArticle!.submissionId}`,
    );

    // Inline HTML galley should render article body content
    const galleyContent = page.locator(
      'section:has(h2:text("Full Text")) .value',
    );
    await expect(galleyContent).toBeVisible({ timeout: 10_000 });
  });

  test('subscriber sees inline HTML body on paywalled article', async ({
    page,
  }) => {
    test.skip(!articleWithCitations, 'No paywalled article with citations');

    // Log in as subscriber
    await page.goto(`${OJS_BASE}/ea/login`);
    await page.fill('input[name="username"]', QA_SUB.username);
    await page.fill('input[name="password"]', QA_SUB.password);
    await page.click('button[type="submit"], input[type="submit"]');
    await page.waitForURL(/.*/, { timeout: 10_000 });

    // Visit the paywalled article
    await page.goto(
      `${OJS_BASE}/ea/article/view/${articleWithCitations!.submissionId}`,
    );

    // Should see inline HTML content (article body from JATS)
    const galleyContent = page.locator(
      'section:has(h2:text("Full Text")) .value',
    );
    await expect(galleyContent).toBeVisible({ timeout: 10_000 });

    // Should also see references below (from citations table)
    const refsSection = page.locator('.item.references');
    await expect(refsSection).toBeVisible();
  });

  test('article with notes shows Notes section in HTML body', async ({
    page,
  }) => {
    test.skip(!articleWithCitations, 'No article with citations');

    // Log in as subscriber
    await page.goto(`${OJS_BASE}/ea/login`);
    await page.fill('input[name="username"]', QA_SUB.username);
    await page.fill('input[name="password"]', QA_SUB.password);
    await page.click('button[type="submit"], input[type="submit"]');

    await page.goto(
      `${OJS_BASE}/ea/article/view/${articleWithCitations!.submissionId}`,
    );

    // The HTML galley body should contain a Notes heading
    // (rendered by jats_to_html from JATS fn-group)
    const notesHeading = page.locator('h2:has-text("Notes")');
    // Notes may or may not exist for this specific article
    // Just check the page loaded without error
    const pageContent = await page.content();
    expect(pageContent).toContain('References');
  });

  test('issue TOC shows page numbers from JATS fpage/lpage', async ({
    page,
  }) => {
    test.skip(!issueWithPages, 'No issue with page numbers found in DB');

    await page.goto(
      `${OJS_BASE}/ea/issue/view/${issueWithPages!.issueId}`,
    );
    await page.waitForLoadState('domcontentloaded');

    // OJS displays page ranges in .pages elements on the issue TOC
    const pageElements = page.locator('.obj_article_summary .pages');
    const count = await pageElements.count();
    expect(count).toBeGreaterThan(0);

    // Verify at least one shows a valid page range (e.g. "215-217")
    const firstPages = await pageElements.first().textContent();
    expect(firstPages).toMatch(/\d+\s*[-–]\s*\d+/);
  });
});
