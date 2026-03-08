import { test, expect } from '@playwright/test';
import {
  createUser,
  deleteUser,
  createSubscription,
  deleteSubscription,
  getSubscriptionProductId,
  getWpPasswordHash,
  clearTestSyncData,
  wpCli,
  wpEval,
} from '../helpers/wp';
import {
  findOjsUser,
  getOjsUsername,
  getOjsPasswordHash,
  getMustChangePassword,
  deleteOjsUser,
  waitForSync,
} from '../helpers/ojs';

const TS = Date.now();
const WP_PASSWORD = 'TestMember123!';

test.describe('Password hash sync (WP → OJS)', () => {
  const PREFIX = `e2e_pwsync_${TS}`;

  test.afterAll(() => {
    clearTestSyncData();
  });

  test('bulk sync stores WP hash and allows OJS login', async ({ page }) => {
    const email = `${PREFIX}_bulk@test.invalid`;
    const login = `${PREFIX}_bulk`;
    let wpUserId: number;
    let subId: number;

    try {
      const productId = getSubscriptionProductId();
      wpUserId = createUser(login, email);

      // Set a known password so we can test login.
      wpCli(`user update ${wpUserId} --user_pass=${WP_PASSWORD}`);

      subId = createSubscription(wpUserId, productId, 'active');

      // Get the WP hash before sync.
      const wpHash = getWpPasswordHash(wpUserId);
      expect(wpHash).toBeTruthy();

      // Call find-or-create via WP eval with password hash (simulates bulk sync).
      wpEval(`
$api = new WPOJS_API_Client();
$hash = get_userdata(${wpUserId})->user_pass;
$result = $api->find_or_create_user("${email}", "E2E", "Test", $hash);
echo json_encode($result);
`);

      const ojsUserId2 = findOjsUser(email);
      expect(ojsUserId2).not.toBeNull();

      // Verify: WP hash is stored on OJS (the exact WP hash, not a random one).
      const ojsHash = getOjsPasswordHash(ojsUserId2!);
      expect(ojsHash).toBe(wpHash);

      // Verify: must_change_password is false.
      expect(getMustChangePassword(ojsUserId2!)).toBe(false);

      // Login to OJS with the WP password.
      const username = getOjsUsername(ojsUserId2!);
      expect(username).toBeTruthy();

      await page.goto('http://localhost:8081/index.php/journal/login');
      await page.locator('#username').fill(username);
      await page.locator('#password').fill(WP_PASSWORD);
      await page.locator('button[type="submit"], input[type="submit"]').first().click();

      // After login, user nav should be visible.
      await expect(
        page.locator('.pkp_navigation_user, .app__userNav, [class*="navigation_user"]').first(),
      ).toBeVisible({ timeout: 15_000 });

      // Verify lazy rehash: password should now be rehashed to cost 12.
      // WP uses cost 10, OJS uses cost 12 — needsRehash triggers on cost mismatch.
      const rehashed = getOjsPasswordHash(ojsUserId2!);
      expect(rehashed).not.toBe(wpHash); // Hash changed
      expect(rehashed).toMatch(/^\$2y\$12\$/); // Now cost 12
    } finally {
      try { deleteSubscription(subId!); } catch {}
      try { deleteUser(wpUserId!); } catch {}
      deleteOjsUser(email);
    }
  });

  test('existing OJS user password not overwritten on re-sync', async () => {
    const email = `${PREFIX}_existing@test.invalid`;
    const login = `${PREFIX}_existing`;
    let wpUserId: number;
    let subId: number;

    try {
      const productId = getSubscriptionProductId();
      wpUserId = createUser(login, email);
      subId = createSubscription(wpUserId, productId, 'active');

      // First sync — creates OJS user.
      wpCli(`ojs-sync sync --member=${email}`);

      const ojsUserId = findOjsUser(email);
      expect(ojsUserId).not.toBeNull();

      // Get the current OJS hash.
      const hashBefore = getOjsPasswordHash(ojsUserId!);

      // Re-sync — should find existing user, not overwrite password.
      wpCli(`ojs-sync sync --member=${email}`);

      const hashAfter = getOjsPasswordHash(ojsUserId!);
      expect(hashAfter).toBe(hashBefore);
    } finally {
      try { deleteSubscription(subId!); } catch {}
      try { deleteUser(wpUserId!); } catch {}
      deleteOjsUser(email);
    }
  });
});
