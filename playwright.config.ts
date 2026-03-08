import { readFileSync } from 'fs';
import { defineConfig, devices } from '@playwright/test';

// Load select env vars from .env so tests use the same values as setup scripts.
// Only loads vars that aren't already set in the environment.
for (const line of readFileSync('.env', 'utf-8').split('\n')) {
  const m = line.match(/^(WP_ADMIN_PASSWORD)=["']?(.+?)["']?$/);
  if (m && !process.env[m[1]]) process.env[m[1]] = m[2];
}

const isCI = !!process.env.CI;

export default defineConfig({
  globalSetup: './e2e/global-setup.ts',
  testDir: './e2e/tests',
  fullyParallel: false,
  workers: 1,
  timeout: 60_000,
  maxFailures: isCI ? 0 : 3,
  retries: isCI ? 1 : 0,
  reporter: [['list'], ['html']],
  use: {
    baseURL: 'http://localhost:8080',
    screenshot: 'only-on-failure',
    trace: isCI ? 'on-first-retry' : 'off',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});
