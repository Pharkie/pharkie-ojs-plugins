/**
 * Compare live OJS vs dev OJS — captures screenshots of key pages side-by-side.
 *
 * Usage:
 *   npx playwright test --config scripts/compare-branding.config.ts
 *
 * Output: screenshots in docs/images/branding-comparison/
 *
 * In devcontainer (DinD), OJS base_url is "http://localhost" but Playwright can't
 * reach that. We access OJS via its container hostname ("ojs") and route all
 * sub-requests for "http://localhost/..." back to the OJS container.
 */
import { test } from '@playwright/test';
import { mkdirSync } from 'fs';
import { join } from 'path';

const LIVE_BASE = 'https://journal.existentialanalysis.org.uk/index.php/t1';

// Dev OJS — reachable via Docker network hostname or explicit host.
const DEV_OJS_ORIGIN = process.env.DEV_OJS_ORIGIN || 'http://ojs:80';
const DEV_BASE = `${DEV_OJS_ORIGIN}/index.php/ea`;

const OUT_DIR = join(__dirname, '..', 'docs', 'images', 'branding-comparison');

// Live OJS 3.4 uses /about/editorialTeam; dev OJS 3.5 uses /about/editorialMasthead.
// We handle this with separate live/dev paths where they differ.
const PAGES = [
  { name: 'homepage', path: '' },
  { name: 'archives', path: '/issue/archive' },
  { name: 'about', path: '/about' },
  { name: 'editorial-team', path: '/about/editorialTeam', devPath: '/about/editorialMasthead' },
  { name: 'submissions', path: '/about/submissions' },
  { name: 'contact', path: '/about/contact' },
  { name: 'login', path: '/login' },
];

const VIEWPORT = { width: 1280, height: 900 };

mkdirSync(OUT_DIR, { recursive: true });

for (const pg of PAGES) {
  test(`${pg.name} — live vs dev`, async ({ browser }) => {
    // --- Live ---
    const liveCtx = await browser.newContext({ viewport: VIEWPORT, ignoreHTTPSErrors: true });
    const liveTab = await liveCtx.newPage();
    await liveTab.goto(`${LIVE_BASE}${pg.path}`, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await liveTab.waitForTimeout(1000);
    await liveTab.screenshot({ path: join(OUT_DIR, `${pg.name}-live.png`), fullPage: true });
    await liveCtx.close();

    // --- Dev ---
    // OJS generates asset URLs using its configured base_url (http://localhost).
    // Playwright can't reach localhost:80 in DinD, so we intercept those requests
    // and rewrite them to the actual OJS container origin.
    const devCtx = await browser.newContext({ viewport: VIEWPORT });
    const devTab = await devCtx.newPage();

    // Route http://localhost/** → DEV_OJS_ORIGIN/**
    await devTab.route('http://localhost/**', async (route) => {
      const url = route.request().url().replace('http://localhost', DEV_OJS_ORIGIN);
      const response = await route.fetch({ url });
      await route.fulfill({ response });
    });

    const devPath = ('devPath' in pg && pg.devPath) ? pg.devPath : pg.path;
    await devTab.goto(`${DEV_BASE}${devPath}`, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await devTab.waitForTimeout(1000);
    await devTab.screenshot({ path: join(OUT_DIR, `${pg.name}-dev.png`), fullPage: true });
    await devCtx.close();
  });
}
