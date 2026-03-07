import { test, expect } from '@playwright/test';
import {
  createUser,
  deleteUser,
  createSubscription,
  deleteSubscription,
  getSubscriptionProductId,
  clearTestSyncData,
  wpCli,
  wpEval,
} from '../helpers/wp';
import {
  findOjsUser,
  hasActiveSubscription,
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
      wpUserId = createUser(login, email);
      subId = createSubscription(wpUserId, productId, 'active');
      // Don't waitForSync — let the CLI command do the sync.

      const output = wpCli(`ojs-sync sync --member=${email}`);
      expect(output).toContain('Success');

      // Verify user was synced to OJS.
      const ojsUserId = findOjsUser(email);
      expect(ojsUserId).not.toBeNull();
      expect(hasActiveSubscription(ojsUserId!)).toBe(true);
    } finally {
      try { deleteSubscription(subId!); } catch { /* ok */ }
      try { deleteUser(wpUserId!); } catch { /* ok */ }
      deleteOjsUser(email);
      clearTestSyncData();
    }
  });

  test('sync --member=email --dry-run — no changes made', () => {
    const email = `${PREFIX}_dryrun@test.invalid`;
    const login = `${PREFIX}_dryrun`;
    let wpUserId: number;
    let subId: number;
    const productId = getSubscriptionProductId();

    try {
      wpUserId = createUser(login, email);
      subId = createSubscription(wpUserId, productId, 'active');

      const output = wpCli(`ojs-sync sync --member=${email} --dry-run`);
      expect(output.toLowerCase()).toContain('would sync');

      // OJS user should NOT have been created.
      const ojsUserId = findOjsUser(email);
      expect(ojsUserId).toBeNull();
    } finally {
      try { deleteSubscription(subId!); } catch { /* ok */ }
      try { deleteUser(wpUserId!); } catch { /* ok */ }
      deleteOjsUser(email);
      clearTestSyncData();
    }
  });

  test('sync --dry-run --yes — bulk dry run', () => {
    // Bulk sync requires --yes to skip confirmation prompt in non-interactive mode.
    const output = wpCli('ojs-sync sync --dry-run --yes');

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

  test('send-welcome-emails --dry-run — reports count', () => {
    // Sync one user first so there's at least one with _wpojs_user_id meta.
    const email = `${PREFIX}_welcome@test.invalid`;
    const login = `${PREFIX}_welcome`;
    const wpUserId = createUser(login, email);
    const subId = createSubscription(wpUserId, getSubscriptionProductId());
    try {
      wpCli(`ojs-sync sync --member=${email} --yes`);
      waitForSync(email);

      const output = wpCli('ojs-sync send-welcome-emails --dry-run');

      expect(output).toContain('synced users');
      expect(output.toLowerCase()).toContain('dry run');
    } finally {
      deleteSubscription(subId);
      deleteUser(wpUserId);
      const ojsUser = findOjsUser(email);
      if (ojsUser) deleteOjsUser(ojsUser.id);
    }
  });
});
