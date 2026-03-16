/**
 * Guard: tests must be run from the project root, not from e2e/.
 *
 * Running `npx playwright test` here skips the root playwright.config.ts,
 * which means no baseURL, no .env loading, and wrong worker count — causing
 * 30+ tests to fail with misleading errors.
 */
throw new Error(
  '\n\nPlaywright must be run from the project root, not from e2e/.\n' +
  'Run:  cd /workspaces/wp-ojs-sync && npx playwright test\n',
);
