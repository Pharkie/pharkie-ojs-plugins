import { test, expect } from '@playwright/test';
import {
  createUser,
  createMembershipPlan,
  createUserMembership,
  setUserMembershipStatus,
  setMemberPlans,
  getMemberPlans,
  wpEval,
  wpCli,
  runReconciliation,
  pruneQueueExcept,
  cleanupWpUser,
  getUserMeta,
} from '../../helpers/wp';
import {
  findOjsUser,
  hasActiveSubscription,
  deleteOjsUser,
  waitForSync,
  ojsQuery,
  findAndVerifyOjsUser,
} from '../../helpers/ojs';

const TS = Date.now();

/**
 * Membership-plan-based access: a member whose entitlement lives in a
 * WooCommerce Memberships plan and NOT in a subscription.
 *
 * This is how the SEA's life members are held — an active "LIFE MEMBER" plan
 * with no end date and no subscription at all. Before the plan path existed the
 * sync could not see them: all six life members had no OJS account, and two of
 * them reported they could not reach the journal (2026-08-11).
 */
test.describe('Membership plan-based access', () => {
  const PREFIX = `e2e_plan_${TS}`;
  let planId: number;
  let otherPlanId: number;
  let previousPlans: number[];

  test.beforeAll(() => {
    previousPlans = getMemberPlans();
    planId = createMembershipPlan(`E2E Life Plan ${TS}`);
    otherPlanId = createMembershipPlan(`E2E Unticked Plan ${TS}`);
    setMemberPlans([planId]);
  });

  test.afterAll(() => {
    setMemberPlans(previousPlans);
    wpCli(`post delete ${planId} --force`);
    wpCli(`post delete ${otherPlanId} --force`);
  });

  test('life membership (no subscription, no end date) gets non-expiring OJS access', () => {
    const email = `${PREFIX}_life@test.invalid`;
    let wpUserId: number;
    let membershipId: number;

    try {
      wpUserId = createUser(`${PREFIX}_life`, email);
      // No end date — an unlimited membership, which is what "life member" is.
      membershipId = createUserMembership(wpUserId, planId);

      // No manual scheduling: granting the membership fires
      // wc_memberships_user_membership_created, which queues the activate.
      waitForSync();

      const { userId: ojsUserId, hasActive } = findAndVerifyOjsUser(email);
      expect(ojsUserId).not.toBeNull();
      expect(hasActive).toBe(true);

      const dateEnd = ojsQuery(
        `SELECT date_end FROM subscriptions WHERE user_id = ${ojsUserId} ORDER BY subscription_id DESC LIMIT 1`,
      ).trim();
      // NULL comes back from the MySQL CLI as an empty string.
      expect(dateEnd === '' || dateEnd === 'NULL').toBe(true);
    } finally {
      if (membershipId!) wpEval(`wp_delete_post(${membershipId}, true);`);
      cleanupWpUser({ wpUserId: wpUserId! });
      deleteOjsUser(email);
    }
  });

  test('reconciliation picks up a plan member who was never synced', () => {
    const email = `${PREFIX}_recon@test.invalid`;
    let wpUserId: number;
    let membershipId: number;

    try {
      wpUserId = createUser(`${PREFIX}_recon`, email);
      membershipId = createUserMembership(wpUserId, planId);
      waitForSync();
      deleteOjsUser(email);
      // Drop the cached OJS user id too, so this is genuinely "never synced" —
      // the state the six live life members were in.
      wpEval(`delete_user_meta(${wpUserId}, '_wpojs_user_id');`);
      expect(findOjsUser(email)).toBeNull();

      runReconciliation();
      // Reconciliation queues work for every member on the rig; keep only ours.
      pruneQueueExcept(wpUserId);
      waitForSync(120_000);

      const ojsUserId = findOjsUser(email);
      expect(ojsUserId).not.toBeNull();
      expect(hasActiveSubscription(ojsUserId!)).toBe(true);
      expect(getUserMeta(wpUserId, '_wpojs_user_id')).not.toBe('');
    } finally {
      if (membershipId!) wpEval(`wp_delete_post(${membershipId}, true);`);
      cleanupWpUser({ wpUserId: wpUserId! });
      deleteOjsUser(email);
    }
  });

  test('membership on an unticked plan grants nothing', () => {
    const email = `${PREFIX}_unticked@test.invalid`;
    let wpUserId: number;
    let membershipId: number;

    try {
      wpUserId = createUser(`${PREFIX}_unticked`, email);
      membershipId = createUserMembership(wpUserId, otherPlanId);
      waitForSync();
      runReconciliation();
      pruneQueueExcept(wpUserId);
      waitForSync(120_000);

      expect(findOjsUser(email)).toBeNull();
    } finally {
      if (membershipId!) wpEval(`wp_delete_post(${membershipId}, true);`);
      cleanupWpUser({ wpUserId: wpUserId! });
      deleteOjsUser(email);
    }
  });

  test('dated membership carries its end date; cancelling it expires OJS access', () => {
    const email = `${PREFIX}_dated@test.invalid`;
    let wpUserId: number;
    let membershipId: number;

    try {
      wpUserId = createUser(`${PREFIX}_dated`, email);
      membershipId = createUserMembership(wpUserId, planId, { endDate: '2030-06-30 00:00:00' });
      waitForSync();

      const ojsUserId = findOjsUser(email);
      expect(ojsUserId).not.toBeNull();
      const dateEnd = ojsQuery(
        `SELECT DATE(date_end) FROM subscriptions WHERE user_id = ${ojsUserId} ORDER BY subscription_id DESC LIMIT 1`,
      ).trim();
      expect(dateEnd).toBe('2030-06-30');

      // Cancelling is a status transition — the hook should expire OJS access.
      setUserMembershipStatus(membershipId, 'cancelled');
      waitForSync();

      expect(hasActiveSubscription(ojsUserId!)).toBe(false);
    } finally {
      if (membershipId!) wpEval(`wp_delete_post(${membershipId}, true);`);
      cleanupWpUser({ wpUserId: wpUserId! });
      deleteOjsUser(email);
    }
  });
});
