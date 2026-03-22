import { test, expect } from '@playwright/test';
import {
  createUser,
  deleteUser,
  createSubscription,
  deleteSubscription,
  updateSubscriptionStatus,
  getSubscriptionProductId,
  getWpPasswordHash,
  clearTestSyncData,
  wpCli,
  wpEval,
  createUserWithSubscription,
  cleanupWpUser,
} from '../../helpers/wp';
import {
  findOjsUser,
  getOjsUsername,
  getOjsPasswordHash,
  getMustChangePassword,
  deleteOjsUser,
  hasActiveSubscription,
  waitForSync,
} from '../../helpers/ojs';

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

      await page.goto('http://localhost:8081/index.php/ea/login');
      await page.locator('#username').fill(username);
      await page.locator('#password').fill(WP_PASSWORD);
      await page.locator('button[type="submit"], input[type="submit"]').first().click();

      // After login, user nav should be visible.
      await expect(
        page.locator('.pkp_navigation_user, .app__userNav, [class*="navigation_user"]').first(),
      ).toBeVisible({ timeout: 15_000 });

      // Note: lazy rehash (cost 10 → 12) does NOT happen on OJS form login
      // because OJS uses its own Validation class, not Laravel's SessionGuard.
      // The WpCompatibleHasher is wired into EloquentUserProvider but OJS's
      // login form doesn't trigger the provider's rehash pipeline.
      // The WP hash (cost 10) remains usable indefinitely via check().

      // Verify subscriber can access paywalled content (no paywall hint).
      // The find_or_create_user call above only creates the OJS user — run a
      // full single-member sync to also create the OJS subscription record.
      wpCli(`ojs-sync sync --member=${email}`);

      await page.goto('http://localhost:8081/index.php/ea');
      const articleLink = page.locator('a[href*="/article/view/"]').first();
      if ((await articleLink.count()) > 0) {
        await articleLink.click();
        await page.waitForLoadState('domcontentloaded');

        // Subscriber should NOT see the non-subscriber CTA box.
        const ctaBox = page.locator('.inline-html-galley-cta');
        await expect(ctaBox).toHaveCount(0);

        // Subscriber should see a galley link (PDF download).
        const galleyLink = page.locator('a.obj_galley_link, a[href*="/galley/"]').first();
        await expect(galleyLink).toBeVisible({ timeout: 5_000 });
      }
    } finally {
      cleanupWpUser({ subIds: [subId!], wpUserId: wpUserId! });
      deleteOjsUser(email);
    }
  });

  test('WP password change syncs new hash to OJS', async ({ page }) => {
    const email = `${PREFIX}_pwchange@test.invalid`;
    const login = `${PREFIX}_pwchange`;
    const NEW_PASSWORD = 'ChangedPass456!';
    let wpUserId: number;
    let subId: number;

    try {
      const productId = getSubscriptionProductId();
      wpUserId = createUser(login, email);
      wpCli(`user update ${wpUserId} --user_pass=${WP_PASSWORD}`);
      subId = createSubscription(wpUserId, productId, 'active');

      // Sync to create OJS user with initial hash.
      wpCli(`ojs-sync sync --member=${email}`);
      const ojsUserId = findOjsUser(email);
      expect(ojsUserId).not.toBeNull();

      const hashBefore = getOjsPasswordHash(ojsUserId!);

      // Change WP password (triggers profile_update hook → schedules password sync).
      wpCli(`user update ${wpUserId} --user_pass=${NEW_PASSWORD}`);

      // Process the Action Scheduler queue.
      waitForSync();

      // Verify OJS hash changed.
      const hashAfter = getOjsPasswordHash(ojsUserId!);
      expect(hashAfter).not.toBe(hashBefore);

      // Verify new hash matches WP.
      const newWpHash = getWpPasswordHash(wpUserId);
      expect(hashAfter).toBe(newWpHash);

      // Verify login works with the new password.
      const username = getOjsUsername(ojsUserId!);
      await page.goto('http://localhost:8081/index.php/ea/login');
      await page.locator('#username').fill(username);
      await page.locator('#password').fill(NEW_PASSWORD);
      await page.locator('button[type="submit"], input[type="submit"]').first().click();

      await expect(
        page.locator('.pkp_navigation_user, .app__userNav, [class*="navigation_user"]').first(),
      ).toBeVisible({ timeout: 15_000 });
    } finally {
      cleanupWpUser({ subIds: [subId!], wpUserId: wpUserId! });
      deleteOjsUser(email);
    }
  });

  test('WP password login still works after subscription expires', async ({ page }) => {
    const email = `${PREFIX}_access@test.invalid`;
    const login = `${PREFIX}_access`;
    let wpUserId: number;
    let subId: number;

    try {
      const productId = getSubscriptionProductId();
      wpUserId = createUser(login, email);
      wpCli(`user update ${wpUserId} --user_pass=${WP_PASSWORD}`);
      subId = createSubscription(wpUserId, productId, 'active');

      // Sync to OJS with password hash.
      wpCli(`ojs-sync sync --member=${email}`);
      const ojsUserId = findOjsUser(email);
      expect(ojsUserId).not.toBeNull();
      expect(hasActiveSubscription(ojsUserId!)).toBe(true);

      // Expire the subscription.
      updateSubscriptionStatus(subId, 'expired');
      waitForSync();
      expect(hasActiveSubscription(ojsUserId!)).toBe(false);

      // WP password should still work for OJS login even after expiry.
      const username = getOjsUsername(ojsUserId!);
      await page.goto('http://localhost:8081/index.php/ea/login');
      await page.locator('#username').fill(username);
      await page.locator('#password').fill(WP_PASSWORD);
      await page.locator('button[type="submit"], input[type="submit"]').first().click();

      await expect(
        page.locator('.pkp_navigation_user, .app__userNav, [class*="navigation_user"]').first(),
      ).toBeVisible({ timeout: 15_000 });
    } finally {
      cleanupWpUser({ subIds: [subId!], wpUserId: wpUserId! });
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
      ({ wpUserId, subId } = createUserWithSubscription(login, email, productId));

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
      cleanupWpUser({ subIds: [subId!], wpUserId: wpUserId! });
      deleteOjsUser(email);
    }
  });
});
