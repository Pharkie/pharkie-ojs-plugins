<?php

/**
 * WP-OJS Subscription API Plugin
 *
 * Exposes REST endpoints for managing OJS user accounts and subscriptions,
 * called by the WP OJS Sync plugin (wpojs-sync).
 *
 * Also adds UI messages: login hint, paywall hint, and site footer.
 *
 * Deploy to: plugins/generic/wpojsSubscriptionApi/ in OJS installation.
 * Requires OJS 3.5+.
 *
 * API endpoints are registered via api/v1/wpojs/index.php (mounted into
 * the OJS installation). This plugin handles UI messages only; the API
 * controller is loaded directly by OJS's APIRouter.
 *
 * Configuration in config.inc.php:
 *   [wpojs]
 *   allowed_ips = "1.2.3.4,5.6.7.8"
 *   wp_member_url = "https://your-wp-site.example.org"
 *   support_email = ""
 *
 * UI messages (login hint, paywall hint, footer) are stored in
 * plugin_settings (DB), not config.inc.php. PHP INI files corrupt
 * values containing " and {} (HTML href + placeholders). Instance
 * defaults are written by setup-ojs.sh during environment setup.
 */

namespace APP\plugins\generic\wpojsSubscriptionApi;

use APP\core\Application;
use Illuminate\Support\Facades\DB;
use PKP\config\Config;
use PKP\db\DAORegistry;
use PKP\plugins\GenericPlugin;
use PKP\plugins\Hook;
use APP\plugins\generic\wpojsSubscriptionApi\WpojsApiLog;
use APP\plugins\generic\wpojsSubscriptionApi\WpojsApiLogMigration;
use APP\plugins\generic\wpojsSubscriptionApi\WpCompatibleHasher;

class WpojsSubscriptionApiPlugin extends GenericPlugin
{
    private const SUB_STATUS_ACTIVE = 1;

    public const DEFAULT_LOGIN_HINT = 'Member? Log in with your membership email and password.';
    public const DEFAULT_FOOTER_MESSAGE = 'Your journal access is provided by your membership. <a href="{wpUrl}">Manage your membership</a>.';
    public const DEFAULT_PASSWORD_RESET_HINT = 'Members: <a href="{wpResetUrl}">change your password on the membership website</a> — it will sync to the journal automatically. Passwords set here may be overwritten by your membership password.';

    public function register($category, $path, $mainContextId = null)
    {
        $success = parent::register($category, $path, $mainContextId);

        if (!$success || !$this->getEnabled()) {
            return $success;
        }

        // Replace the default hasher so OJS can verify WP password hashes
        // at login time and lazy-rehash them to native bcrypt.
        $userProvider = app(\PKP\core\PKPUserProvider::class);
        if ($userProvider && method_exists($userProvider, 'setHasher')) {
            $userProvider->setHasher(new WpCompatibleHasher());
        }

        // UI messages
        Hook::add('TemplateManager::display', $this->addLoginMessage(...));
        Hook::add('TemplateManager::display', $this->addPasswordResetMessage(...));
        // Paywall hint removed — the inline HTML galley plugin now shows a more
        // actionable CTA box with membership link and purchase pricing.
        Hook::add('Templates::Common::Footer::PageFooter', $this->addFooterMessage(...));

        return $success;
    }

    /**
     * Resolve a UI message with fallback chain:
     * plugin setting (DB) → generic constant.
     *
     * Instance-specific defaults are written to plugin_settings by
     * setup-ojs.sh during environment setup. Admins can further edit
     * via the plugin Settings page.
     *
     * Note: config.inc.php is NOT used for messages. PHP INI files
     * corrupt values containing " and {} (HTML href + placeholders).
     */
    private function getMessage(string $settingName, string $default): string
    {
        $context = Application::get()->getRequest()->getContext();
        $contextId = $context ? $context->getId() : 0;

        $value = $this->getSetting($contextId, $settingName);
        if (!empty($value)) {
            return $value;
        }

        return $default;
    }

    /**
     * Login page hint message.
     *
     * The login template has no hook points, so we detect it via
     * TemplateManager::display and inject a styled message via
     * addHeader (inline CSS + JS that prepends the message).
     */
    public function addLoginMessage(string $hookName, array $args): bool
    {
        $templateMgr = $args[0];
        $template = $args[1] ?? '';

        if (str_contains($template, 'userLogin.tpl')) {
            $lostPasswordUrl = Application::get()->getRequest()->getDispatcher()->url(
                Application::get()->getRequest(),
                Application::ROUTE_PAGE,
                null,
                'login',
                'lostPassword'
            );

            $messageTemplate = $this->getMessage('loginHint', self::DEFAULT_LOGIN_HINT);
            $escapedUrl = htmlspecialchars($lostPasswordUrl, ENT_QUOTES, 'UTF-8');
            $hintHtml = str_replace(
                '{lostPasswordUrl}',
                $escapedUrl,
                $messageTemplate
            );

            // Escape for safe embedding inside a JS string literal
            $jsEscapedHtml = strtr($hintHtml, [
                '\\' => '\\\\',
                "'" => "\\'",
                '"' => '\\"',
                "\n" => '\\n',
                "\r" => '\\r',
                '</' => '<\\/',  // prevent </script> breaking out
            ]);

            $templateMgr->addHeader('wpojs-login-message', '<style>
.wpojs-login-hint { background: #e8f4f8; border: 1px solid #b8daff; border-radius: 4px; padding: 12px 16px; margin-bottom: 16px; font-size: 14px; line-height: 1.5; }
.wpojs-login-hint a { color: #0056b3; text-decoration: underline; }
</style>
<script>
document.addEventListener("DOMContentLoaded", function() {
    var h1 = document.querySelector(".page_login h1");
    if (h1) {
        var div = document.createElement("div");
        div.className = "wpojs-login-hint";
        div.innerHTML = "' . $jsEscapedHtml . '";
        h1.insertAdjacentElement("afterend", div);
    }
    // Relabel "Username or Email" to just "Email" — members only have email, not OJS usernames
    var usernameLabel = document.querySelector(".page_login .username .label");
    if (usernameLabel) {
        usernameLabel.childNodes[0].textContent = "Email ";
    }
    var usernameInput = document.getElementById("username");
    if (usernameInput) {
        usernameInput.setAttribute("type", "email");
        usernameInput.setAttribute("autocomplete", "email");
    }
    // Simplify "Remember username and password" to "Remember me"
    var rememberLabel = document.querySelector(".page_login .remember .label");
    if (rememberLabel) {
        rememberLabel.textContent = "Remember me";
    }
});
</script>');
        }

        return Hook::CONTINUE;
    }

    /**
     * Password reset page hint message.
     *
     * Warns members that passwords set via OJS forgot-password may be
     * overwritten by WP→OJS sync. Directs them to reset on WP instead.
     * Same injection pattern as addLoginMessage().
     */
    public function addPasswordResetMessage(string $hookName, array $args): bool
    {
        $templateMgr = $args[0];
        $template = $args[1] ?? '';

        if (str_contains($template, 'userLostPassword.tpl')) {
            $wpSiteUrl = Config::getVar('wpojs', 'wp_member_url', '');
            $wpResetUrl = rtrim($wpSiteUrl, '/') . '/wp-login.php?action=lostpassword';

            $messageTemplate = $this->getMessage('passwordResetHint', self::DEFAULT_PASSWORD_RESET_HINT);
            $escapedUrl = htmlspecialchars($wpResetUrl, ENT_QUOTES, 'UTF-8');
            $hintHtml = str_replace('{wpResetUrl}', $escapedUrl, $messageTemplate);

            $jsEscapedHtml = strtr($hintHtml, [
                '\\' => '\\\\',
                "'" => "\\'",
                '"' => '\\"',
                "\n" => '\\n',
                "\r" => '\\r',
                '</' => '<\\/',
            ]);

            $templateMgr->addHeader('wpojs-pw-reset-message', '<style>
.wpojs-pw-reset-hint { background: #fff3cd; border: 1px solid #ffc107; border-radius: 4px; padding: 12px 16px; margin-bottom: 16px; font-size: 14px; line-height: 1.5; }
.wpojs-pw-reset-hint a { color: #856404; text-decoration: underline; font-weight: 600; }
</style>
<script>
document.addEventListener("DOMContentLoaded", function() {
    var h1 = document.querySelector(".page_login h1, .page_lost_password h1, #content h1");
    if (h1) {
        var div = document.createElement("div");
        div.className = "wpojs-pw-reset-hint";
        div.innerHTML = "' . $jsEscapedHtml . '";
        h1.insertAdjacentElement("afterend", div);
    }
});
</script>');
        }

        // Profile page: warn synced members that password changes here will be overwritten
        if (str_contains($template, 'user/profile.tpl')) {
            $wpSiteUrl = Config::getVar('wpojs', 'wp_member_url', '');
            if (empty($wpSiteUrl)) {
                return Hook::CONTINUE;
            }
            $wpResetUrl = htmlspecialchars(rtrim($wpSiteUrl, '/') . '/wp-login.php?action=lostpassword', ENT_QUOTES, 'UTF-8');

            $templateMgr->addHeader('wpojs-profile-pw-hint', '<style>
.wpojs-profile-pw-hint { background: #fff3cd; border: 1px solid #ffc107; border-radius: 4px; padding: 12px 16px; margin-bottom: 12px; font-size: 13px; line-height: 1.5; }
.wpojs-profile-pw-hint a { color: #856404; text-decoration: underline; font-weight: 600; }
</style>
<script>
document.addEventListener("DOMContentLoaded", function() {
    // Inject warning into the Change Password tab when it loads (AJAX tab)
    var tabs = document.getElementById("profileTabs");
    if (!tabs) return;
    var observer = new MutationObserver(function() {
        var pwForm = document.getElementById("changePasswordForm");
        if (pwForm && !document.querySelector(".wpojs-profile-pw-hint")) {
            var div = document.createElement("div");
            div.className = "wpojs-profile-pw-hint";
            div.innerHTML = "SEA members: <a href=\"' . $wpResetUrl . '\">change your password on the membership website</a> instead. Passwords set here will be overwritten by your membership password.";
            pwForm.insertBefore(div, pwForm.firstChild);
        }
    });
    observer.observe(tabs, { childList: true, subtree: true });
});
</script>');
        }

        return Hook::CONTINUE;
    }

    /**
     * Site footer: "Your journal access is provided by your membership."
     */
    public function addFooterMessage(string $hookName, array $params): bool
    {
        $output = &$params[2];

        $wpUrl = Config::getVar('wpojs', 'wp_member_url', '');
        if (empty($wpUrl)) {
            return Hook::CONTINUE;
        }

        $messageTemplate = $this->getMessage('footerMessage', self::DEFAULT_FOOTER_MESSAGE);
        $escapedUrl = htmlspecialchars($wpUrl, ENT_QUOTES, 'UTF-8');
        $messageHtml = str_replace(
            '{wpUrl}',
            $escapedUrl,
            $messageTemplate
        );
        $output .= '<div style="text-align:center;padding:8px 16px;font-size:13px;color:#666;border-top:1px solid #eee;margin-top:8px;">'
            . $messageHtml
            . '</div>';

        return Hook::CONTINUE;
    }

    public function getActions($request, $actionArgs)
    {
        $actions = parent::getActions($request, $actionArgs);

        if (!$this->getEnabled()) {
            return $actions;
        }

        $router = $request->getRouter();

        array_unshift($actions,
            new \PKP\linkAction\LinkAction(
                'settings',
                new \PKP\linkAction\request\AjaxModal(
                    $router->url(
                        $request,
                        null,
                        null,
                        'manage',
                        null,
                        ['verb' => 'settings', 'plugin' => $this->getName(), 'category' => 'generic']
                    ),
                    __('plugins.generic.wpojsSubscriptionApi.settings')
                ),
                __('plugins.generic.wpojsSubscriptionApi.settings')
            ),
            new \PKP\linkAction\LinkAction(
                'status',
                new \PKP\linkAction\request\AjaxModal(
                    $router->url(
                        $request,
                        null,
                        null,
                        'manage',
                        null,
                        ['verb' => 'status', 'plugin' => $this->getName(), 'category' => 'generic']
                    ),
                    __('plugins.generic.wpojsSubscriptionApi.status')
                ),
                __('plugins.generic.wpojsSubscriptionApi.status')
            ),
        );

        return $actions;
    }

    public function manage($args, $request)
    {
        // Verify caller is a journal manager or site admin (OJS 3.5-compatible DB query)
        $user = $request->getUser();
        if (!$user) {
            return new \PKP\core\JSONMessage(false, 'Not authenticated');
        }

        $context = $request->getContext();
        $contextId = $context ? $context->getId() : 0;

        $hasPermission = DB::table('user_user_groups')
            ->join('user_groups', 'user_user_groups.user_group_id', '=', 'user_groups.user_group_id')
            ->where('user_user_groups.user_id', $user->getId())
            ->where(function ($q) use ($contextId) {
                $q->where(function ($q2) use ($contextId) {
                    $q2->where('user_groups.role_id', \PKP\security\Role::ROLE_ID_MANAGER)
                       ->where('user_groups.context_id', $contextId);
                })->orWhere('user_groups.role_id', \PKP\security\Role::ROLE_ID_SITE_ADMIN);
            })
            ->exists();

        if (!$hasPermission) {
            return new \PKP\core\JSONMessage(false, 'Permission denied');
        }

        $verb = $request->getUserVar('verb');

        if ($verb === 'settings') {
            return $this->manageSettings($request);
        }

        if ($verb === 'status') {
            // Cleanup old API log entries on page load.
            WpojsApiLog::cleanup(30);

            $data = $this->gatherStatusData();

            $templateMgr = \APP\template\TemplateManager::getManager($request);
            $templateMgr->assign($data);

            return new \PKP\core\JSONMessage(
                true,
                $templateMgr->fetch($this->getTemplateResource('status.tpl'))
            );
        }

        return parent::manage($args, $request);
    }

    private function manageSettings($request): \PKP\core\JSONMessage
    {
        $context = $request->getContext();
        $contextId = $context ? $context->getId() : 0;

        if ($request->isPost()) {
            $this->updateSetting($contextId, 'loginHint', mb_substr(strip_tags($request->getUserVar('loginHint') ?? '', '<a>'), 0, 1000));
            $this->updateSetting($contextId, 'passwordResetHint', mb_substr(strip_tags($request->getUserVar('passwordResetHint') ?? '', '<a>'), 0, 1000));
            $this->updateSetting($contextId, 'footerMessage', mb_substr(strip_tags($request->getUserVar('footerMessage') ?? '', '<a>'), 0, 1000));

            return new \PKP\core\JSONMessage(true);
        }

        $templateMgr = \APP\template\TemplateManager::getManager($request);
        $templateMgr->assign([
            'loginHint' => $this->getSetting($contextId, 'loginHint') ?: self::DEFAULT_LOGIN_HINT,
            'passwordResetHint' => $this->getSetting($contextId, 'passwordResetHint') ?: self::DEFAULT_PASSWORD_RESET_HINT,
            'footerMessage' => $this->getSetting($contextId, 'footerMessage') ?: self::DEFAULT_FOOTER_MESSAGE,
            'defaultLoginHint' => self::DEFAULT_LOGIN_HINT,
            'defaultPasswordResetHint' => self::DEFAULT_PASSWORD_RESET_HINT,
            'defaultFooterMessage' => self::DEFAULT_FOOTER_MESSAGE,
        ]);

        return new \PKP\core\JSONMessage(
            true,
            $templateMgr->fetch($this->getTemplateResource('settings.tpl'))
        );
    }

    private function gatherStatusData(): array
    {
        // Config health checks.
        $apiKeyDefined = !empty(Config::getVar('wpojs', 'api_key_secret', ''))
            || !empty(Config::getVar('security', 'api_key_secret', ''));
        $allowedIps = Config::getVar('wpojs', 'allowed_ips', '');
        $wpMemberUrl = Config::getVar('wpojs', 'wp_member_url', '');
        $supportEmail = Config::getVar('wpojs', 'support_email', '');
        $loadStats = WpojsApiLog::getAverageResponseTime(20, 60);
        $loadDetail = $loadStats['avg_ms'] !== null
            ? "load-based (avg response: {$loadStats['avg_ms']}ms, samples: {$loadStats['sample_count']})"
            : 'load-based (no recent data)';

        $configChecks = [
            ['name' => 'API key defined', 'ok' => $apiKeyDefined],
            ['name' => 'Allowed IPs configured', 'ok' => !empty($allowedIps), 'detail' => $allowedIps ?: '(none)'],
            ['name' => 'WP member URL set', 'ok' => !empty($wpMemberUrl), 'detail' => $wpMemberUrl ?: '(not set)'],
            ['name' => 'Support email set', 'ok' => !empty($supportEmail), 'detail' => $supportEmail ?: '(not set)'],
            ['name' => 'Load protection', 'ok' => true, 'detail' => $loadDetail],
        ];

        $allGreen = true;
        foreach ($configChecks as $check) {
            if (!$check['ok']) {
                $allGreen = false;
                break;
            }
        }

        // Sync stats.
        $context = Application::get()->getRequest()->getContext();
        $journalId = $context ? $context->getId() : 0;

        $activeSubCount = 0;
        $syncCreatedCount = 0;
        $subTypeCounts = [];

        try {
            $dao = DAORegistry::getDAO('IndividualSubscriptionDAO');

            // Active subscriptions count.
            $activeSubCount = (int) DB::table('subscriptions')
                ->join('subscription_types', 'subscriptions.type_id', '=', 'subscription_types.type_id')
                ->where('subscription_types.journal_id', $journalId)
                ->where('subscriptions.status', self::SUB_STATUS_ACTIVE)
                ->count();

            // Subscription types in use.
            // Type names live in subscription_type_settings (not a column on subscription_types).
            $locale = Application::get()->getRequest()->getContext()?->getPrimaryLocale() ?? 'en';
            $subTypeCounts = DB::table('subscriptions')
                ->join('subscription_types', 'subscriptions.type_id', '=', 'subscription_types.type_id')
                ->leftJoin('subscription_type_settings', function ($join) use ($locale) {
                    $join->on('subscription_types.type_id', '=', 'subscription_type_settings.type_id')
                         ->where('subscription_type_settings.setting_name', '=', 'name')
                         ->where('subscription_type_settings.locale', '=', $locale);
                })
                ->where('subscription_types.journal_id', $journalId)
                ->where('subscriptions.status', self::SUB_STATUS_ACTIVE)
                ->select(
                    'subscription_types.type_id',
                    DB::raw("COALESCE(subscription_type_settings.setting_value, CONCAT('Type #', subscription_types.type_id)) as type_name"),
                    DB::raw('COUNT(*) as count')
                )
                ->groupBy('subscription_types.type_id', 'subscription_type_settings.setting_value')
                ->get()
                ->toArray();
        } catch (\Exception $e) {
            // Tables may not exist yet.
            error_log('[wpojs-status] Subscription query failed: ' . $e->getMessage());
        }

        try {
            // Users created by sync.
            $syncCreatedCount = (int) DB::table('user_settings')
                ->where('setting_name', 'wpojs_created_by_sync')
                ->count();
        } catch (\Exception $e) {
            // OK if no entries yet.
            error_log('[wpojs-status] Sync-created user count query failed: ' . $e->getMessage());
        }

        // Recent API activity log.
        $recentLogs = WpojsApiLog::getRecent(50);

        return [
            'configChecks'    => $configChecks,
            'allGreen'        => $allGreen,
            'activeSubCount'  => $activeSubCount,
            'syncCreatedCount' => $syncCreatedCount,
            'subTypeCounts'   => $subTypeCounts,
            'recentLogs'      => $recentLogs,
        ];
    }

    public function getInstallMigration()
    {
        return new WpojsApiLogMigration();
    }

    public function getDisplayName()
    {
        return __('plugins.generic.wpojsSubscriptionApi.displayName');
    }

    public function getDescription()
    {
        return __('plugins.generic.wpojsSubscriptionApi.description');
    }
}
