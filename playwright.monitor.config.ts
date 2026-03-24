import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright config for read-only monitoring tests against live/staging.
 * No global setup/teardown — no Docker, no .env required.
 *
 * Usage:
 *   LIVE_WP_HOME=https://community.example.org LIVE_OJS_URL=https://journal.example.org \
 *     npx playwright test --config=playwright.monitor.config.ts
 *
 * Against dev (for testing the tests):
 *   LIVE_WP_HOME=http://localhost:8080 LIVE_OJS_URL=http://localhost:8081 \
 *     npx playwright test --config=playwright.monitor.config.ts
 */

const wpHome = process.env.LIVE_WP_HOME;
const ojsUrl = process.env.LIVE_OJS_URL;

if (!wpHome || !ojsUrl) {
  throw new Error(
    'LIVE_WP_HOME and LIVE_OJS_URL env vars are required.\n' +
    'Example: LIVE_WP_HOME=https://community.existentialanalysis.org.uk ' +
    'LIVE_OJS_URL=https://journal.existentialanalysis.org.uk',
  );
}

export default defineConfig({
  testDir: './e2e/tests/monitoring',
  fullyParallel: true,
  workers: 2,
  timeout: 30_000,
  retries: 1,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    screenshot: 'only-on-failure',
    trace: 'on-first-retry',
  },
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        // No baseURL — tests use wpHome/ojsUrl directly
      },
    },
  ],
});
