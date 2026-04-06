import { readFileSync, existsSync } from 'fs';
import { resolve } from 'path';
import { defineConfig, devices } from '@playwright/test';

// Guard: this config must be loaded from the project root (where .env lives).
// Running `npx playwright test` from e2e/ skips this config entirely, causing
// silent mass failures (no baseURL, no env vars, wrong worker count).
const projectRoot = resolve(__dirname);
if (!existsSync(resolve(projectRoot, '.env'))) {
  throw new Error(
    `\n\nCannot find .env in ${projectRoot}\n` +
    'Run Playwright from the project root:\n' +
    '  cd /workspaces/pharkie-ojs-plugins && npx playwright test\n',
  );
}

// Load select env vars from .env so tests use the same values as setup scripts.
// Only loads vars that aren't already set in the environment.
for (const line of readFileSync(resolve(projectRoot, '.env'), 'utf-8').split('\n')) {
  const m = line.match(/^(WP_ADMIN_PASSWORD|OJS_ADMIN_PASSWORD|OJS_ADMIN_USER|DB_PASSWORD|OJS_DB_PASSWORD|QA_SUB_PASSWORD|QA_NOSUB_PASSWORD)=["']?(.+?)["']?$/);
  if (m && !process.env[m[1]]) process.env[m[1]] = m[2];
}

const isCI = !!process.env.CI;
const isSafari = !!process.env.SAFARI;

export default defineConfig({
  globalSetup: './e2e/global-setup.ts',
  globalTeardown: './e2e/global-teardown.ts',
  testDir: './e2e/tests',
  testIgnore: ['**/monitoring/**'],
  fullyParallel: false,
  workers: 1,
  timeout: 60_000,
  maxFailures: isCI ? 0 : 3,
  retries: isCI ? 1 : 0,
  outputDir: './e2e/test-results',
  reporter: [['list'], ['html', { outputFolder: './e2e/playwright-report' }]],
  use: {
    baseURL: 'http://localhost:8080',
    screenshot: 'only-on-failure',
    trace: isCI ? 'on-first-retry' : 'off',
  },
  projects: isSafari
    ? [
        {
          name: 'webkit',
          use: { ...devices['Desktop Safari'], deviceScaleFactor: 2 },
        },
        {
          name: 'ipad',
          use: { ...devices['iPad Pro 11'] },
        },
        {
          name: 'iphone',
          use: { ...devices['iPhone 14'] },
        },
      ]
    : [
        {
          name: 'chromium',
          use: { ...devices['Desktop Chrome'], deviceScaleFactor: 2 },
        },
      ],
});
