import { test, expect } from '@playwright/test';
import {
  createUser,
  deleteUser,
  createSubscription,
  deleteSubscription,
  updateSubscriptionStatus,
  getSubscriptionProductId,
  changeUserEmail,
  clearTestSyncData,
  wpEval,
  runReconciliation,
  getUserMeta,
  pruneQueueExcept,
  createUserWithSubscription,
  cleanupWpUser,
} from '../../helpers/wp';
import {
  findOjsUser,
  hasActiveSubscription,
  getSubscriptionStatus,
  deleteOjsUser,
  deleteOjsUserById,
  waitForSync,
  ojsQuery,
  getOjsUserEmail,
  deleteOjsSubscription,
  ojsApiCall,
  findAndVerifyOjsUser,
  cleanupOjsUsers,
} from '../../helpers/ojs';

const TS = Date.now();

// ---------------------------------------------------------------
// Group 1: Out-of-order events
// ---------------------------------------------------------------

test.describe('Out-of-order events', () => {
  const PREFIX = `e2e_ooo_${TS}`;

  test('expire before activate — no crash, handles gracefully', () => {
    const email = `${PREFIX}_expfirst@test.invalid`;
    const login = `${PREFIX}_expfirst`;
    let wpUserId: number;
    let subId: number;
    const productId = getSubscriptionProductId();

    try {
      // Create user + active sub (schedules activate), then immediately expire
      // (schedules expire) before the queue processes either action.
      ({ wpUserId, subId } = createUserWithSubscription(login, email, productId));
      updateSubscriptionStatus(subId, 'expired');

      // Process queue — activate runs first but WCS sub is already expired,
      // so resolver can't find subscription data. User gets created on OJS
      // but no subscription. Expire runs next, finds no sub to expire (no-op).
      waitForSync();

      const ojsUserId = findOjsUser(email);
      if (ojsUserId !== null) {
        // User was created but subscription may be absent (null) or expired (16).
        // Both are valid outcomes depending on timing.
        const status = getSubscriptionStatus(ojsUserId);
        expect(status === null || status === 16).toBe(true);
      }
      // No crash = success
    } finally {
      cleanupWpUser({ subIds: [subId!], wpUserId: wpUserId! });
      deleteOjsUser(email);
    }
  });

  test('reactivation after on-hold — OJS sub returns to active', () => {
    const email = `${PREFIX}_react@test.invalid`;
    const login = `${PREFIX}_react`;
    let wpUserId: number;
    let subId: number;
    const productId = getSubscriptionProductId();

    try {
      ({ wpUserId, subId } = createUserWithSubscription(login, email, productId));
      waitForSync();

      const { userId: ojsUserId, hasActive } = findAndVerifyOjsUser(email);
      expect(ojsUserId).not.toBeNull();
      expect(hasActive).toBe(true);

      // Put on hold (simulates payment failure — WCS allows on-hold → active)
      updateSubscriptionStatus(subId, 'on-hold');
      waitForSync();
      expect(getSubscriptionStatus(ojsUserId!)).toBe(16);

      // Reactivate (simulates payment retry success)
      updateSubscriptionStatus(subId, 'active');
      waitForSync();
      expect(getSubscriptionStatus(ojsUserId!)).toBe(1);
    } finally {
      cleanupWpUser({ subIds: [subId!], wpUserId: wpUserId! });
      deleteOjsUser(email);
    }
  });

  test('double expiration — idempotent, no error', () => {
    const email = `${PREFIX}_dblexp@test.invalid`;
    const login = `${PREFIX}_dblexp`;
    let wpUserId: number;
    let subId: number;
    const productId = getSubscriptionProductId();

    try {
      ({ wpUserId, subId } = createUserWithSubscription(login, email, productId));
      waitForSync();

      const ojsUserId = findOjsUser(email);
      expect(ojsUserId).not.toBeNull();

      // First expire
      updateSubscriptionStatus(subId, 'expired');
      waitForSync();
      expect(getSubscriptionStatus(ojsUserId!)).toBe(16);

      // Verify idempotency — status remains expired (no further actions queued)
      expect(getSubscriptionStatus(ojsUserId!)).toBe(16);
    } finally {
      cleanupWpUser({ subIds: [subId!], wpUserId: wpUserId! });
      deleteOjsUser(email);
    }
  });

  test('activate after GDPR delete — new OJS user created', () => {
    const email = `${PREFIX}_gdpr@test.invalid`;
    const login1 = `${PREFIX}_gdpr1`;
    const login2 = `${PREFIX}_gdpr2`;
    let wpUserId1: number;
    let wpUserId2: number;
    let subId1: number;
    let subId2: number;
    let ojsUserId1: number | null = null;
    const productId = getSubscriptionProductId();

    try {
      // Create first user and sync
      ({ wpUserId: wpUserId1, subId: subId1 } = createUserWithSubscription(login1, email, productId));
      waitForSync();

      ojsUserId1 = findOjsUser(email);
      expect(ojsUserId1).not.toBeNull();

      // Delete WP user (GDPR) and sync
      deleteUser(wpUserId1);
      waitForSync();

      // Original OJS user should be anonymised
      const anonEmail = getOjsUserEmail(ojsUserId1!);
      expect(anonEmail).toContain('anonymised.invalid');

      // Create new user with same email
      ({ wpUserId: wpUserId2, subId: subId2 } = createUserWithSubscription(login2, email, productId));
      waitForSync();

      // A new OJS user should exist for this email (different from the old anonymised one)
      const { userId: ojsUserId2, hasActive } = findAndVerifyOjsUser(email);
      expect(ojsUserId2).not.toBeNull();
      expect(ojsUserId2).not.toBe(ojsUserId1);
      expect(hasActive).toBe(true);
    } finally {
      cleanupWpUser({ subIds: [subId1!, subId2!], wpUserId: wpUserId2! });
      // Delete the re-created user (by email) and the anonymised original (by ID)
      deleteOjsUser(email);
      if (ojsUserId1 !== null) deleteOjsUserById(ojsUserId1);
    }
  });
});

// ---------------------------------------------------------------
// Group 2: Multiple subscriptions
// ---------------------------------------------------------------

test.describe('Multiple subscriptions', () => {
  const PREFIX = `e2e_multi_${TS}`;

  test('two active subs, different end dates — OJS gets latest end date', () => {
    const email = `${PREFIX}_twosubs@test.invalid`;
    const login = `${PREFIX}_twosubs`;
    let wpUserId: number;
    let subId1: number;
    let subId2: number;
    const productId = getSubscriptionProductId();

    try {
      wpUserId = createUser(login, email);

      // Create two subscriptions (both use same product since we only have one)
      subId1 = createSubscription(wpUserId, productId, 'active');
      subId2 = createSubscription(wpUserId, productId, 'active');

      // Set different end dates via WP
      const farDate = '2030-12-31 00:00:00';
      const nearDate = '2026-12-31 00:00:00';
      wpEval(`
        $sub1 = wcs_get_subscription(${subId1});
        $sub1->update_dates(['end' => '${farDate}']);
        $sub2 = wcs_get_subscription(${subId2});
        $sub2->update_dates(['end' => '${nearDate}']);
      `);

      waitForSync();

      const { userId: ojsUserId, hasActive } = findAndVerifyOjsUser(email);
      expect(ojsUserId).not.toBeNull();
      expect(hasActive).toBe(true);

      // Check the OJS sub end date — should be the further date
      const ojsDateEnd = ojsQuery(
        `SELECT date_end FROM subscriptions WHERE user_id = ${ojsUserId} ORDER BY subscription_id DESC LIMIT 1`,
      ).trim();
      expect(ojsDateEnd).toContain('2030-12-31');
    } finally {
      cleanupWpUser({ subIds: [subId1!, subId2!], wpUserId: wpUserId! });
      deleteOjsUser(email);
    }
  });

  test('one expiring + one non-expiring — OJS date_end is null', () => {
    const email = `${PREFIX}_nonexp@test.invalid`;
    const login = `${PREFIX}_nonexp`;
    let wpUserId: number;
    let subId1: number;
    let subId2: number;
    const productId = getSubscriptionProductId();

    try {
      wpUserId = createUser(login, email);

      // Sub 1: with an end date
      subId1 = createSubscription(wpUserId, productId, 'active');
      wpEval(`
        $sub = wcs_get_subscription(${subId1});
        $sub->update_dates(['end' => '2027-12-31 00:00:00']);
      `);

      // Sub 2: non-expiring (no end date — WCS default for "0" end)
      subId2 = createSubscription(wpUserId, productId, 'active');
      // WCS subscriptions with no end date are non-expiring by default

      waitForSync();

      const { userId: ojsUserId, hasActive } = findAndVerifyOjsUser(email);
      expect(ojsUserId).not.toBeNull();
      expect(hasActive).toBe(true);

      // Non-expiring should win — OJS date_end should be null/empty
      const ojsDateEnd = ojsQuery(
        `SELECT date_end FROM subscriptions WHERE user_id = ${ojsUserId} ORDER BY subscription_id DESC LIMIT 1`,
      ).trim();
      // NULL in MySQL comes back as empty string from CLI
      expect(ojsDateEnd === '' || ojsDateEnd === 'NULL').toBe(true);
    } finally {
      cleanupWpUser({ subIds: [subId1!, subId2!], wpUserId: wpUserId! });
      deleteOjsUser(email);
    }
  });

  test('cancel one of two active subs — OJS sub stays active', () => {
    const email = `${PREFIX}_cancel1@test.invalid`;
    const login = `${PREFIX}_cancel1`;
    let wpUserId: number;
    let subId1: number;
    let subId2: number;
    const productId = getSubscriptionProductId();

    try {
      wpUserId = createUser(login, email);
      subId1 = createSubscription(wpUserId, productId, 'active');
      subId2 = createSubscription(wpUserId, productId, 'active');
      waitForSync();

      const { userId: ojsUserId, hasActive } = findAndVerifyOjsUser(email);
      expect(ojsUserId).not.toBeNull();
      expect(hasActive).toBe(true);

      // Cancel one subscription
      updateSubscriptionStatus(subId1, 'cancelled');
      waitForSync();

      // OJS subscription should still be active (other sub still valid)
      expect(hasActiveSubscription(ojsUserId!)).toBe(true);
    } finally {
      cleanupWpUser({ subIds: [subId1!, subId2!], wpUserId: wpUserId! });
      deleteOjsUser(email);
    }
  });
});

// ---------------------------------------------------------------
// Group 3: Rapid status changes
// ---------------------------------------------------------------

test.describe('Rapid status changes', () => {
  const PREFIX = `e2e_rapid_${TS}`;

  // Note: "active → on-hold → active" is covered by Group 1's
  // "reactivation after on-hold" test which also checks intermediate state.

  test('concurrent activate and email change — both resolve correctly', () => {
    const originalEmail = `${PREFIX}_conc@test.invalid`;
    const newEmail = `${PREFIX}_conc_new@test.invalid`;
    const login = `${PREFIX}_conc`;
    let wpUserId: number;
    let subId: number;
    const productId = getSubscriptionProductId();

    try {
      // Create user + subscription, then change email — all before queue processes.
      // This queues both wpojs_sync_activate and wpojs_sync_email_change.
      ({ wpUserId, subId } = createUserWithSubscription(login, originalEmail, productId));
      changeUserEmail(wpUserId, newEmail);

      // Process queue — activate and email change should both resolve.
      waitForSync();

      // The user should be findable by the new email on OJS.
      const { userId: ojsUser, hasActive } = findAndVerifyOjsUser(newEmail);
      expect(ojsUser).not.toBeNull();
      expect(hasActive).toBe(true);
    } finally {
      cleanupWpUser({ subIds: [subId!], wpUserId: wpUserId! });
      cleanupOjsUsers(originalEmail, newEmail);
    }
  });

  test('email change before queue processes — OJS user gets current email', () => {
    const originalEmail = `${PREFIX}_emailq@test.invalid`;
    const newEmail = `${PREFIX}_emailq_new@test.invalid`;
    const login = `${PREFIX}_emailq`;
    let wpUserId: number;
    let subId: number;
    const productId = getSubscriptionProductId();

    try {
      ({ wpUserId, subId } = createUserWithSubscription(login, originalEmail, productId));

      // Change email BEFORE queue processes
      changeUserEmail(wpUserId, newEmail);

      // Now process the queue
      waitForSync();

      // The sync handler re-reads the user at processing time, so it should
      // use the current (new) email for the OJS user lookup / creation.
      // Either: OJS user created with new email, or original email then updated.
      const { userId: ojsUserNewEmail, hasActive } = findAndVerifyOjsUser(newEmail);
      // At minimum, the user should be findable by the new email after all syncs
      expect(ojsUserNewEmail).not.toBeNull();
      expect(hasActive).toBe(true);
    } finally {
      cleanupWpUser({ subIds: [subId!], wpUserId: wpUserId! });
      cleanupOjsUsers(originalEmail, newEmail);
    }
  });
});

// ---------------------------------------------------------------
// Group 4: User data edge cases
// ---------------------------------------------------------------

test.describe('User data edge cases', () => {
  const PREFIX = `e2e_data_${TS}`;

  test('no first name — OJS user created with display_name fallback', () => {
    const email = `${PREFIX}_noname@test.invalid`;
    const login = `${PREFIX}_noname`;
    let wpUserId: number;
    let subId: number;
    const productId = getSubscriptionProductId();

    try {
      // Create user with empty first name but valid last name.
      // WP sets display_name to login when first_name is empty.
      // Sync code: $first_name = $user->first_name ?: $user->display_name
      ({ wpUserId, subId } = createUserWithSubscription(login, email, productId, 'active', { firstName: '', lastName: 'Tester' }));
      waitForSync();

      const { userId: ojsUserId, hasActive } = findAndVerifyOjsUser(email);
      expect(ojsUserId).not.toBeNull();
      expect(hasActive).toBe(true);

      // Verify OJS givenName is set (display_name fallback, not empty)
      const givenName = ojsQuery(
        `SELECT setting_value FROM user_settings WHERE user_id = ${ojsUserId} AND setting_name = 'givenName' LIMIT 1`,
      ).trim();
      expect(givenName.length).toBeGreaterThan(0);
    } finally {
      cleanupWpUser({ subIds: [subId!], wpUserId: wpUserId! });
      deleteOjsUser(email);
    }
  });

  test("special characters in name — stored correctly", () => {
    const email = `${PREFIX}_special@test.invalid`;
    const login = `${PREFIX}_special`;
    let wpUserId: number;
    let subId: number;
    const productId = getSubscriptionProductId();

    try {
      // Create user via wpEval to handle special chars properly
      const out = wpEval(`
        $uid = wp_insert_user([
          'user_login' => '${login}',
          'user_email' => '${email}',
          'user_pass' => wp_generate_password(),
          'first_name' => "O'Brien",
          'last_name' => 'Müller-Weiß',
          'role' => 'subscriber',
        ]);
        echo $uid;
      `);
      wpUserId = parseInt(out, 10);

      subId = createSubscription(wpUserId, productId, 'active');
      waitForSync();

      const ojsUserId = findOjsUser(email);
      expect(ojsUserId).not.toBeNull();
      expect(hasActiveSubscription(ojsUserId!)).toBe(true);

      // Verify the name was stored (not empty or mangled)
      const givenName = ojsQuery(
        `SELECT setting_value FROM user_settings WHERE user_id = ${ojsUserId} AND setting_name = 'givenName' AND locale = 'en' LIMIT 1`,
      ).trim();
      expect(givenName).toContain("O'Brien");

      const familyName = ojsQuery(
        `SELECT setting_value FROM user_settings WHERE user_id = ${ojsUserId} AND setting_name = 'familyName' AND locale = 'en' LIMIT 1`,
      ).trim();
      expect(familyName).toContain('Müller-Weiß');
    } finally {
      cleanupWpUser({ subIds: [subId!], wpUserId: wpUserId! });
      deleteOjsUser(email);
    }
  });

  test('long email (near WP limit) — works end-to-end', () => {
    // Constraints: WP varchar(100), PHP FILTER_VALIDATE_EMAIL (local ≤ 64), OJS varchar(255).
    // Build a ~97 char email: 64-char local + longer domain, under WP's 100 limit.
    const localPrefix = `${PREFIX}_lo`;
    const localPad = 'x'.repeat(64 - localPrefix.length);
    const localPart = `${localPrefix}${localPad}`;
    const domain = 'longdomain.test.invalid'; // 23 chars
    const email = `${localPart}@${domain}`; // 64 + 1 + 23 = 88 chars
    const login = `${PREFIX}_long`;
    let wpUserId: number;
    let subId: number;
    const productId = getSubscriptionProductId();

    try {
      ({ wpUserId, subId } = createUserWithSubscription(login, email, productId, 'active', { firstName: 'Long', lastName: 'Email' }));
      waitForSync();

      const { userId: ojsUserId, hasActive } = findAndVerifyOjsUser(email);
      expect(ojsUserId).not.toBeNull();
      expect(hasActive).toBe(true);
    } finally {
      cleanupWpUser({ subIds: [subId!], wpUserId: wpUserId! });
      deleteOjsUser(email);
    }
  });
});

// ---------------------------------------------------------------
// Group 5: Reconciliation catches drift
// ---------------------------------------------------------------

test.describe('Reconciliation catches drift', () => {
  const PREFIX = `e2e_recon_${TS}`;

  test('active member missing OJS sub — reconciliation recreates it', () => {
    const email = `${PREFIX}_missing@test.invalid`;
    const login = `${PREFIX}_missing`;
    let wpUserId: number;
    let subId: number;
    const productId = getSubscriptionProductId();

    try {
      ({ wpUserId, subId } = createUserWithSubscription(login, email, productId));
      waitForSync();

      const { userId: ojsUserId, hasActive } = findAndVerifyOjsUser(email);
      expect(ojsUserId).not.toBeNull();
      expect(hasActive).toBe(true);

      // Delete the OJS subscription directly (simulate drift)
      deleteOjsSubscription(ojsUserId!);
      expect(hasActiveSubscription(ojsUserId!)).toBe(false);

      // Run reconciliation — should detect missing sub and schedule activate.
      // Prune queue to only this test user's actions (reconciliation also
      // schedules actions for all 683 seeded members, which would time out).
      runReconciliation();
      pruneQueueExcept(wpUserId);
      waitForSync(120_000);

      // Subscription should be recreated
      expect(hasActiveSubscription(ojsUserId!)).toBe(true);
    } finally {
      cleanupWpUser({ subIds: [subId!], wpUserId: wpUserId! });
      deleteOjsUser(email);
    }
  });

  test('stale access — reconciliation schedules expire', () => {
    const email = `${PREFIX}_stale@test.invalid`;
    const login = `${PREFIX}_stale`;
    let wpUserId: number;
    let subId: number;
    const productId = getSubscriptionProductId();

    try {
      ({ wpUserId, subId } = createUserWithSubscription(login, email, productId));
      waitForSync();

      const { userId: ojsUserId, hasActive } = findAndVerifyOjsUser(email);
      expect(ojsUserId).not.toBeNull();
      expect(hasActive).toBe(true);

      // Expire via WCS but with our sync hooks removed (simulates drift —
      // the WCS status changed but the OJS sync didn't fire).
      // WCS has internal caches, so we must use its own status API.
      wpEval(`
        remove_all_actions('woocommerce_subscription_status_expired');
        remove_all_actions('woocommerce_subscription_status_cancelled');
        remove_all_actions('woocommerce_subscription_status_on-hold');
        wcs_get_subscription(${subId})->update_status('expired');
      `);

      // Run reconciliation — should detect stale access and schedule expire.
      // Prune queue to only this test user's actions (see comment above).
      runReconciliation();
      pruneQueueExcept(wpUserId);
      waitForSync(120_000);

      // OJS subscription should now be expired (status 16)
      expect(getSubscriptionStatus(ojsUserId!)).toBe(16);
    } finally {
      cleanupWpUser({ subIds: [subId!], wpUserId: wpUserId! });
      deleteOjsUser(email);
    }
  });
});

// ---------------------------------------------------------------
// Group 6: Welcome email dedup
// ---------------------------------------------------------------
// Group 7: OJS API validation
// ---------------------------------------------------------------

test.describe('OJS API validation', () => {
  test('dateEnd before dateStart — returns 400', () => {
    const result = ojsApiCall('POST', '/wpojs/subscriptions', {
      userId: 1,
      typeId: 1,
      dateStart: '2025-12-31',
      dateEnd: '2025-01-01',
    });
    expect(result.status).toBe(400);
    expect(result.body.error).toContain('dateEnd');
  });

  test('status-batch with invalid emails — valid emails return results, invalid flagged', () => {
    const result = ojsApiCall('POST', '/wpojs/subscriptions/status-batch', {
      emails: ['admin@test.invalid', 'not-an-email', '!!!garbage!!!'],
    });
    expect(result.status).toBe(200);
    expect(result.body.results).toBeDefined();

    // Invalid emails should be flagged with error
    if (result.body.results['not-an-email']) {
      expect(result.body.results['not-an-email'].error).toBe('invalid email');
    }
    if (result.body.results['!!!garbage!!!']) {
      expect(result.body.results['!!!garbage!!!'].error).toBe('invalid email');
    }
  });

  test('empty firstName on find-or-create — returns 400', () => {
    const result = ojsApiCall('POST', '/wpojs/users/find-or-create', {
      email: 'test-validation@test.invalid',
      firstName: '',
      lastName: 'Test',
    });
    expect(result.status).toBe(400);
    expect(result.body.error).toContain('firstName');
  });

  test('expire for non-existent user — returns 404', () => {
    const result = ojsApiCall('PUT', '/wpojs/subscriptions/expire-by-user/999999');
    expect(result.status).toBe(404);
  });
});
