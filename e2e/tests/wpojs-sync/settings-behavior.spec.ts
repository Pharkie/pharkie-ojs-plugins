import { test, expect } from '@playwright/test';
import {
  createUser,
  deleteUser,
  createSubscription,
  deleteSubscription,
  getSubscriptionProductId,
  clearTestSyncData,
  wpEval,
  wpCli,
  addUserRole,
  getOjsSetting,
  setOjsSetting,
  wpLogin,
  setUserPassword,
  WP_ADMIN_USER,
  getAdminPassword,
  runReconciliation,
  pruneQueueExcept,
  createUserWithSubscription,
  cleanupWpUser,
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
const SETTINGS_PAGE = '/wp/wp-admin/admin.php?page=wpojs-sync';

/**
 * Settings behavior: verify that changing settings on the admin page
 * actually affects sync behavior, not just display.
 */
test.describe('Settings affect sync behavior', () => {
  const PREFIX = `e2e_setbhv_${TS}`;

  // Save originals for restoration.
  let originalTypeMapping: string;
  let originalManualRoles: string;
  let originalJournalName: string;
  let originalOjsUrl: string;
  let createdTypeId: string | null = null;

  test.beforeAll(() => {
    // Fetch all settings in a single docker exec call.
    const settings = wpEval(`
      echo json_encode(get_option('wpojs_type_mapping', [])) . "\\n";
      echo json_encode(get_option('wpojs_manual_roles', [])) . "\\n";
      echo get_option('wpojs_journal_name', '') . "\\n";
      echo get_option('wpojs_url', '');
    `);
    const lines = settings.split('\n');
    originalTypeMapping = lines[0];
    originalManualRoles = lines[1];
    originalJournalName = lines[2];
    originalOjsUrl = lines[3];

    // Create a 2nd OJS subscription type for the type mapping test.
    ojsQuery(`
      INSERT INTO subscription_types (journal_id, cost, currency_code_alpha, duration, format, institutional, membership, disable_public_display, seq)
      VALUES (1, 0.00, 'GBP', 365, 31, 0, 0, 0, 99)
    `);
    // Get the ID of the created type.
    createdTypeId = ojsQuery(
      `SELECT MAX(type_id) FROM subscription_types WHERE seq = 99`,
    ).trim();
    // Add a name for the type.
    ojsQuery(`
      INSERT INTO subscription_type_settings (type_id, locale, setting_name, setting_value, setting_type)
      VALUES (${createdTypeId}, 'en', 'name', 'E2E Test Type', 'string')
    `);
  });

  test.afterAll(() => {
    // Restore all settings in a single docker exec call.
    wpEval(`
      update_option('wpojs_type_mapping', json_decode('${originalTypeMapping.replace(/'/g, "\\'")}', true));
      update_option('wpojs_manual_roles', json_decode('${originalManualRoles.replace(/'/g, "\\'")}', true));
      update_option('wpojs_journal_name', '${originalJournalName}');
      update_option('wpojs_url', '${originalOjsUrl}');
      delete_transient('wpojs_ojs_type_names');
    `);

    // Clean up the 2nd OJS subscription type.
    if (createdTypeId) {
      ojsQuery(
        `DELETE FROM subscription_type_settings WHERE type_id = ${createdTypeId};` +
        `DELETE FROM subscription_types WHERE type_id = ${createdTypeId};`,
      );
    }

    clearTestSyncData();
  });

  test('changed product-to-type mapping is used during sync', () => {
    const email = `${PREFIX}_typemap@test.invalid`;
    const login = `${PREFIX}_typemap`;
    let wpUserId: number;
    let subId: number;
    const productId = getSubscriptionProductId();

    try {
      // Remap the product to the new test type.
      wpEval(`
        $mapping = get_option('wpojs_type_mapping', []);
        $mapping[${productId}] = ${createdTypeId};
        update_option('wpojs_type_mapping', $mapping);
      `);

      ({ wpUserId, subId } = createUserWithSubscription(login, email, productId));
      waitForSync();

      const { userId: ojsUserId, hasActive } = findAndVerifyOjsUser(email);
      expect(ojsUserId).not.toBeNull();
      expect(hasActive).toBe(true);

      // Verify the subscription uses the new type_id.
      const typeId = ojsQuery(
        `SELECT type_id FROM subscriptions WHERE user_id = ${ojsUserId} ORDER BY subscription_id DESC LIMIT 1`,
      ).trim();
      expect(typeId).toBe(createdTypeId);
    } finally {
      // Restore original mapping.
      wpEval(`update_option('wpojs_type_mapping', json_decode('${originalTypeMapping.replace(/'/g, "\\'")}', true));`);
      cleanupWpUser({ subIds: [subId!], wpUserId: wpUserId! });
      deleteOjsUser(email);
    }
  });

  test('adding a role to manual roles grants OJS access', () => {
    const email = `${PREFIX}_addrole@test.invalid`;
    const login = `${PREFIX}_addrole`;
    let wpUserId: number;

    try {
      // Add 'editor' to manual roles.
      wpEval(`
        $roles = get_option('wpojs_manual_roles', []);
        if (!in_array('editor', $roles)) { $roles[] = 'editor'; }
        update_option('wpojs_manual_roles', $roles);
      `);

      wpUserId = createUser(login, email, { role: 'editor' });

      // Manual role doesn't fire WCS hooks — schedule activate explicitly.
      wpEval(`
        as_schedule_single_action(time(), 'wpojs_sync_activate', [['wp_user_id' => ${wpUserId}]], 'wpojs-sync');
      `);
      waitForSync();

      const ojsUserId = findOjsUser(email);
      expect(ojsUserId).not.toBeNull();
      expect(hasActiveSubscription(ojsUserId!)).toBe(true);
    } finally {
      // Restore manual roles.
      wpEval(`update_option('wpojs_manual_roles', json_decode('${originalManualRoles.replace(/'/g, "\\'")}', true));`);
      cleanupWpUser({ wpUserId: wpUserId! });
      deleteOjsUser(email);
    }
  });

  test('removing a role from manual roles → reconciliation expires', () => {
    const email = `${PREFIX}_rmrole@test.invalid`;
    const login = `${PREFIX}_rmrole`;
    let wpUserId: number;

    try {
      // Ensure 'editor' is in manual roles and create user with that role.
      wpEval(`
        $roles = get_option('wpojs_manual_roles', []);
        if (!in_array('editor', $roles)) { $roles[] = 'editor'; }
        update_option('wpojs_manual_roles', $roles);
      `);

      wpUserId = createUser(login, email, { role: 'editor' });
      wpEval(`
        as_schedule_single_action(time(), 'wpojs_sync_activate', [['wp_user_id' => ${wpUserId}]], 'wpojs-sync');
      `);
      waitForSync();

      const ojsUserId = findOjsUser(email);
      expect(ojsUserId).not.toBeNull();
      expect(hasActiveSubscription(ojsUserId!)).toBe(true);

      // Remove 'editor' from manual roles — user is no longer active member.
      wpEval(`update_option('wpojs_manual_roles', json_decode('${originalManualRoles.replace(/'/g, "\\'")}', true));`);

      // Reconciliation detects stale access and schedules expire.
      runReconciliation();
      pruneQueueExcept(wpUserId);
      waitForSync(120_000);

      expect(hasActiveSubscription(ojsUserId!)).toBe(false);
    } finally {
      wpEval(`update_option('wpojs_manual_roles', json_decode('${originalManualRoles.replace(/'/g, "\\'")}', true));`);
      cleanupWpUser({ wpUserId: wpUserId! });
      deleteOjsUser(email);
    }
  });

  test('changed journal name appears in My Account widget', async ({ page }) => {
    const email = `${PREFIX}_jname@test.invalid`;
    const login = `${PREFIX}_jname`;
    const password = 'TestPass123!';
    let wpUserId: number;
    let subId: number;
    const productId = getSubscriptionProductId();

    try {
      // Set a custom journal name.
      wpEval(`update_option('wpojs_journal_name', 'E2E Test Journal');`);

      wpUserId = createUser(login, email);
      setUserPassword(wpUserId, password);
      subId = createSubscription(wpUserId, productId, 'active');
      waitForSync();

      // Log in and check My Account page.
      await wpLogin(page, login, password);
      await page.goto('/my-account/', { waitUntil: 'networkidle' });

      const card = page.locator('.wpojs-journal-access');
      await expect(card).toBeVisible();
      await expect(card).toContainText('E2E Test Journal');
    } finally {
      wpEval(`update_option('wpojs_journal_name', '${originalJournalName}');`);
      cleanupWpUser({ subIds: [subId!], wpUserId: wpUserId! });
      deleteOjsUser(email);
    }
  });

  test('OJS connection status reflects URL change', async ({ page }) => {
    try {
      await wpLogin(page, WP_ADMIN_USER, getAdminPassword());

      // Set a bad URL and clear the cached types transient.
      setOjsSetting('http://localhost:19999');
      wpEval(`delete_transient('wpojs_ojs_type_names');`);

      await page.goto(SETTINGS_PAGE, { waitUntil: 'networkidle' });
      await expect(page.locator('text=Could not connect to OJS')).toBeVisible();

      // Restore good URL and clear cache.
      setOjsSetting(originalOjsUrl);
      wpEval(`delete_transient('wpojs_ojs_type_names');`);

      await page.goto(SETTINGS_PAGE, { waitUntil: 'networkidle' });
      await expect(page.locator('text=Connected to OJS')).toBeVisible();
    } finally {
      setOjsSetting(originalOjsUrl);
      wpEval(`delete_transient('wpojs_ojs_type_names');`);
    }
  });
});
