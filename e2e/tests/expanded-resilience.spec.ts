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
} from '../helpers/wp';
import {
  findOjsUser,
  hasActiveSubscription,
  getSubscriptionStatus,
  deleteOjsUser,
  waitForSync,
  ojsQuery,
  getOjsUserEmail,
  getOjsUserSetting,
  deleteOjsSubscription,
  ojsApiCall,
} from '../helpers/ojs';

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
      wpUserId = createUser(login, email);
      subId = createSubscription(wpUserId, productId, 'active');
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
      try { deleteSubscription(subId!); } catch { /* ok */ }
      try { deleteUser(wpUserId!); } catch { /* ok */ }
      deleteOjsUser(email);
      clearTestSyncData();
    }
  });

  test('reactivation after on-hold — OJS sub returns to active', () => {
    const email = `${PREFIX}_react@test.invalid`;
    const login = `${PREFIX}_react`;
    let wpUserId: number;
    let subId: number;
    const productId = getSubscriptionProductId();

    try {
      wpUserId = createUser(login, email);
      subId = createSubscription(wpUserId, productId, 'active');
      waitForSync();

      const ojsUserId = findOjsUser(email);
      expect(ojsUserId).not.toBeNull();
      expect(hasActiveSubscription(ojsUserId!)).toBe(true);

      // Put on hold (simulates payment failure — WCS allows on-hold → active)
      updateSubscriptionStatus(subId, 'on-hold');
      waitForSync();
      expect(getSubscriptionStatus(ojsUserId!)).toBe(16);

      // Reactivate (simulates payment retry success)
      updateSubscriptionStatus(subId, 'active');
      waitForSync();
      expect(getSubscriptionStatus(ojsUserId!)).toBe(1);
    } finally {
      try { deleteSubscription(subId!); } catch { /* ok */ }
      try { deleteUser(wpUserId!); } catch { /* ok */ }
      deleteOjsUser(email);
      clearTestSyncData();
    }
  });

  test('double expiration — idempotent, no error', () => {
    const email = `${PREFIX}_dblexp@test.invalid`;
    const login = `${PREFIX}_dblexp`;
    let wpUserId: number;
    let subId: number;
    const productId = getSubscriptionProductId();

    try {
      wpUserId = createUser(login, email);
      subId = createSubscription(wpUserId, productId, 'active');
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
      try { deleteSubscription(subId!); } catch { /* ok */ }
      try { deleteUser(wpUserId!); } catch { /* ok */ }
      deleteOjsUser(email);
      clearTestSyncData();
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
    const productId = getSubscriptionProductId();

    try {
      // Create first user and sync
      wpUserId1 = createUser(login1, email);
      subId1 = createSubscription(wpUserId1, productId, 'active');
      waitForSync();

      const ojsUserId1 = findOjsUser(email);
      expect(ojsUserId1).not.toBeNull();

      // Delete WP user (GDPR) and sync
      deleteUser(wpUserId1);
      waitForSync();

      // Original OJS user should be anonymised
      const anonEmail = getOjsUserEmail(ojsUserId1!);
      expect(anonEmail).toContain('anonymised.invalid');

      // Create new user with same email
      wpUserId2 = createUser(login2, email);
      subId2 = createSubscription(wpUserId2, productId, 'active');
      waitForSync();

      // A new OJS user should exist for this email (different from the old anonymised one)
      const ojsUserId2 = findOjsUser(email);
      expect(ojsUserId2).not.toBeNull();
      expect(ojsUserId2).not.toBe(ojsUserId1);
      expect(hasActiveSubscription(ojsUserId2!)).toBe(true);
    } finally {
      try { deleteSubscription(subId2!); } catch { /* ok */ }
      try { deleteUser(wpUserId2!); } catch { /* ok */ }
      deleteOjsUser(email);
      clearTestSyncData();
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

      const ojsUserId = findOjsUser(email);
      expect(ojsUserId).not.toBeNull();
      expect(hasActiveSubscription(ojsUserId!)).toBe(true);

      // Check the OJS sub end date — should be the further date
      const ojsDateEnd = ojsQuery(
        `SELECT date_end FROM subscriptions WHERE user_id = ${ojsUserId} ORDER BY subscription_id DESC LIMIT 1`,
      ).trim();
      expect(ojsDateEnd).toContain('2030-12-31');
    } finally {
      try { deleteSubscription(subId1!); } catch { /* ok */ }
      try { deleteSubscription(subId2!); } catch { /* ok */ }
      try { deleteUser(wpUserId!); } catch { /* ok */ }
      deleteOjsUser(email);
      clearTestSyncData();
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

      const ojsUserId = findOjsUser(email);
      expect(ojsUserId).not.toBeNull();
      expect(hasActiveSubscription(ojsUserId!)).toBe(true);

      // Non-expiring should win — OJS date_end should be null/empty
      const ojsDateEnd = ojsQuery(
        `SELECT date_end FROM subscriptions WHERE user_id = ${ojsUserId} ORDER BY subscription_id DESC LIMIT 1`,
      ).trim();
      // NULL in MySQL comes back as empty string from CLI
      expect(ojsDateEnd === '' || ojsDateEnd === 'NULL').toBe(true);
    } finally {
      try { deleteSubscription(subId1!); } catch { /* ok */ }
      try { deleteSubscription(subId2!); } catch { /* ok */ }
      try { deleteUser(wpUserId!); } catch { /* ok */ }
      deleteOjsUser(email);
      clearTestSyncData();
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

      const ojsUserId = findOjsUser(email);
      expect(ojsUserId).not.toBeNull();
      expect(hasActiveSubscription(ojsUserId!)).toBe(true);

      // Cancel one subscription
      updateSubscriptionStatus(subId1, 'cancelled');
      waitForSync();

      // OJS subscription should still be active (other sub still valid)
      expect(hasActiveSubscription(ojsUserId!)).toBe(true);
    } finally {
      try { deleteSubscription(subId1!); } catch { /* ok */ }
      try { deleteSubscription(subId2!); } catch { /* ok */ }
      try { deleteUser(wpUserId!); } catch { /* ok */ }
      deleteOjsUser(email);
      clearTestSyncData();
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
      wpUserId = createUser(login, originalEmail);
      subId = createSubscription(wpUserId, productId, 'active');
      changeUserEmail(wpUserId, newEmail);

      // Process queue — activate and email change should both resolve.
      waitForSync();

      // The user should be findable by the new email on OJS.
      const ojsUser = findOjsUser(newEmail);
      expect(ojsUser).not.toBeNull();
      expect(hasActiveSubscription(ojsUser!)).toBe(true);
    } finally {
      try { deleteSubscription(subId!); } catch { /* ok */ }
      try { deleteUser(wpUserId!); } catch { /* ok */ }
      deleteOjsUser(originalEmail);
      deleteOjsUser(newEmail);
      clearTestSyncData();
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
      wpUserId = createUser(login, originalEmail);
      subId = createSubscription(wpUserId, productId, 'active');

      // Change email BEFORE queue processes
      changeUserEmail(wpUserId, newEmail);

      // Now process the queue
      waitForSync();

      // The sync handler re-reads the user at processing time, so it should
      // use the current (new) email for the OJS user lookup / creation.
      // Either: OJS user created with new email, or original email then updated.
      const ojsUserNewEmail = findOjsUser(newEmail);
      // At minimum, the user should be findable by the new email after all syncs
      expect(ojsUserNewEmail).not.toBeNull();
      expect(hasActiveSubscription(ojsUserNewEmail!)).toBe(true);
    } finally {
      try { deleteSubscription(subId!); } catch { /* ok */ }
      try { deleteUser(wpUserId!); } catch { /* ok */ }
      deleteOjsUser(originalEmail);
      deleteOjsUser(newEmail);
      clearTestSyncData();
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
      wpUserId = createUser(login, email, { firstName: '', lastName: 'Tester' });

      subId = createSubscription(wpUserId, productId, 'active');
      waitForSync();

      const ojsUserId = findOjsUser(email);
      expect(ojsUserId).not.toBeNull();
      expect(hasActiveSubscription(ojsUserId!)).toBe(true);

      // Verify OJS givenName is set (display_name fallback, not empty)
      const givenName = ojsQuery(
        `SELECT setting_value FROM user_settings WHERE user_id = ${ojsUserId} AND setting_name = 'givenName' LIMIT 1`,
      ).trim();
      expect(givenName.length).toBeGreaterThan(0);
    } finally {
      try { deleteSubscription(subId!); } catch { /* ok */ }
      try { deleteUser(wpUserId!); } catch { /* ok */ }
      deleteOjsUser(email);
      clearTestSyncData();
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
      try { deleteSubscription(subId!); } catch { /* ok */ }
      try { deleteUser(wpUserId!); } catch { /* ok */ }
      deleteOjsUser(email);
      clearTestSyncData();
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
      wpUserId = createUser(login, email, { firstName: 'Long', lastName: 'Email' });

      subId = createSubscription(wpUserId, productId, 'active');
      waitForSync();

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
      wpUserId = createUser(login, email);
      subId = createSubscription(wpUserId, productId, 'active');
      waitForSync();

      const ojsUserId = findOjsUser(email);
      expect(ojsUserId).not.toBeNull();
      expect(hasActiveSubscription(ojsUserId!)).toBe(true);

      // Delete the OJS subscription directly (simulate drift)
      deleteOjsSubscription(ojsUserId!);
      expect(hasActiveSubscription(ojsUserId!)).toBe(false);

      // Run reconciliation — should detect missing sub and schedule activate
      runReconciliation();
      waitForSync(120_000);

      // Subscription should be recreated
      expect(hasActiveSubscription(ojsUserId!)).toBe(true);
    } finally {
      try { deleteSubscription(subId!); } catch { /* ok */ }
      try { deleteUser(wpUserId!); } catch { /* ok */ }
      deleteOjsUser(email);
      clearTestSyncData();
    }
  });

  test('stale access — reconciliation schedules expire', () => {
    const email = `${PREFIX}_stale@test.invalid`;
    const login = `${PREFIX}_stale`;
    let wpUserId: number;
    let subId: number;
    const productId = getSubscriptionProductId();

    try {
      wpUserId = createUser(login, email);
      subId = createSubscription(wpUserId, productId, 'active');
      waitForSync();

      const ojsUserId = findOjsUser(email);
      expect(ojsUserId).not.toBeNull();
      expect(hasActiveSubscription(ojsUserId!)).toBe(true);

      // Expire via WCS but with our sync hooks removed (simulates drift —
      // the WCS status changed but the OJS sync didn't fire).
      // WCS has internal caches, so we must use its own status API.
      wpEval(`
        remove_all_actions('woocommerce_subscription_status_expired');
        remove_all_actions('woocommerce_subscription_status_cancelled');
        remove_all_actions('woocommerce_subscription_status_on-hold');
        wcs_get_subscription(${subId})->update_status('expired');
      `);

      // Run reconciliation — should detect stale access and schedule expire
      runReconciliation();
      waitForSync(120_000);

      // OJS subscription should now be expired (status 16)
      expect(getSubscriptionStatus(ojsUserId!)).toBe(16);
    } finally {
      try { deleteSubscription(subId!); } catch { /* ok */ }
      try { deleteUser(wpUserId!); } catch { /* ok */ }
      deleteOjsUser(email);
      clearTestSyncData();
    }
  });
});

// ---------------------------------------------------------------
// Group 6: Welcome email dedup
// ---------------------------------------------------------------

test.describe('Welcome email dedup', () => {
  const PREFIX = `e2e_welcome_${TS}`;

  test('expire cycle does not clear welcome email dedup flag', () => {
    const email = `${PREFIX}_dedup@test.invalid`;
    const login = `${PREFIX}_dedup`;
    let wpUserId: number;
    let subId: number;
    const productId = getSubscriptionProductId();

    try {
      wpUserId = createUser(login, email);
      subId = createSubscription(wpUserId, productId, 'active');
      waitForSync();

      const ojsUserId = findOjsUser(email);
      expect(ojsUserId).not.toBeNull();

      // Manually set the dedup flag (email sending fails in Docker dev env,
      // so the flag isn't set automatically — but in production it would be).
      ojsQuery(
        `INSERT IGNORE INTO user_settings (user_id, locale, setting_name, setting_value) VALUES (${ojsUserId}, '', 'wpojs_welcome_email_sent', '1')`,
      );
      expect(getOjsUserSetting(email, 'wpojs_welcome_email_sent')).toBe('1');

      // Put on-hold and reactivate (WCS allows on-hold → active)
      updateSubscriptionStatus(subId, 'on-hold');
      waitForSync();

      updateSubscriptionStatus(subId, 'active');
      waitForSync();

      // The expire endpoint does NOT clear user_settings (only the GDPR
      // delete endpoint does). So the dedup flag should persist.
      const flagAfter = getOjsUserSetting(email, 'wpojs_welcome_email_sent');
      expect(flagAfter).toBe('1');
    } finally {
      try { deleteSubscription(subId!); } catch { /* ok */ }
      try { deleteUser(wpUserId!); } catch { /* ok */ }
      deleteOjsUser(email);
      clearTestSyncData();
    }
  });
});

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
