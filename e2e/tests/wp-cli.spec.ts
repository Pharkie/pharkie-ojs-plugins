import { test, expect } from '@playwright/test';
import {
  createUser,
  deleteUser,
  createSubscription,
  deleteSubscription,
  getSubscriptionProductId,
  clearTestSyncData,
  wpCli,
  createUserWithSubscription,
  cleanupWpUser,
} from '../helpers/wp';
import {
  findOjsUser,
  hasActiveSubscription,
  findAndVerifyOjsUser,
  deleteOjsUser,
  waitForSync,
} from '../helpers/ojs';

const TS = Date.now();

/**
 * WP-CLI commands: verify all `wp ojs-sync *` commands work correctly.
 *
 * Commands are registered as `ojs-sync <subcommand>` so WP-CLI invocation
 * is `wp ojs-sync sync`, `wp ojs-sync status`, etc.
 */
test.describe('WP-CLI commands', () => {
  const PREFIX = `e2e_cli_${TS}`;

  // The reconcile test queues activate actions for all ~683 seeded members.
  // Clean them up so they don't leak into subsequent test files.
  test.afterAll(() => {
    clearTestSyncData();
  });

  test('sync --member=email — single user sync', () => {
    const email = `${PREFIX}_single@test.invalid`;
    const login = `${PREFIX}_single`;
    let wpUserId: number;
    let subId: number;
    const productId = getSubscriptionProductId();

    try {
      ({ wpUserId, subId } = createUserWithSubscription(login, email, productId));
      // Don't waitForSync — let the CLI command do the sync.

      const output = wpCli(`ojs-sync sync --member=${email}`);
      expect(output).toContain('Success');

      // Verify user was synced to OJS.
      const { userId: ojsUserId, hasActive } = findAndVerifyOjsUser(email);
      expect(ojsUserId).not.toBeNull();
      expect(hasActive).toBe(true);
    } finally {
      cleanupWpUser({ subIds: [subId!], wpUserId: wpUserId! });
      deleteOjsUser(email);
    }
  });

  test('sync --member=email --dry-run — no changes made', () => {
    const email = `${PREFIX}_dryrun@test.invalid`;
    const login = `${PREFIX}_dryrun`;
    let wpUserId: number;
    let subId: number;
    const productId = getSubscriptionProductId();

    try {
      ({ wpUserId, subId } = createUserWithSubscription(login, email, productId));

      const output = wpCli(`ojs-sync sync --member=${email} --dry-run`);
      expect(output.toLowerCase()).toContain('would sync');

      // OJS user should NOT have been created.
      const ojsUserId = findOjsUser(email);
      expect(ojsUserId).toBeNull();
    } finally {
      cleanupWpUser({ subIds: [subId!], wpUserId: wpUserId! });
      deleteOjsUser(email);
    }
  });

  test('sync --bulk --dry-run — bulk dry run', () => {
    // Bulk sync requires --bulk flag to prevent accidental full sync.
    const output = wpCli('ojs-sync sync --bulk --dry-run');

    // Should mention the member count and indicate dry run mode.
    expect(output).toContain('active members');
    expect(output.toLowerCase()).toContain('dry run');
  });

  test('status — outputs expected fields', () => {
    const output = wpCli('ojs-sync status');

    expect(output).toContain('Pending');
    expect(output).toContain('Active WP members');
    expect(output).toContain('Members synced');
    expect(output).toContain('Failures');
    expect(output).toContain('Cron Schedule');
  });

  test('test-connection — passes in dev environment', () => {
    const output = wpCli('ojs-sync test-connection');

    expect(output).toContain('OK');
    expect(output).toContain('Preflight');
    expect(output).toContain('Connection test passed');
  });

  test('reconcile — reports results', () => {
    const output = wpCli('ojs-sync reconcile');

    expect(output).toContain('Resolving');
    expect(output).toContain('Checking');
    expect(output).toContain('Reconciliation complete');
  });

});
