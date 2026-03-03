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
 *   default_login_hint = ""      ; optional: instance-specific default
 *   default_paywall_hint = ""    ; optional: instance-specific default
 *   default_footer_message = ""  ; optional: instance-specific default
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

class WpojsSubscriptionApiPlugin extends GenericPlugin
{
    private const SUB_STATUS_ACTIVE = 1;

    public const DEFAULT_LOGIN_HINT = 'Member? First time here? <a href="{lostPasswordUrl}">Set your password</a> to access journal content.';
    public const DEFAULT_PAYWALL_HINT = 'If you believe you should have access through your membership, please contact <a href="mailto:{supportEmail}">{supportEmail}</a>.';
    public const DEFAULT_FOOTER_MESSAGE = 'Your journal access is provided by your membership. <a href="{wpUrl}">Manage your membership</a>.';

    public function register($category, $path, $mainContextId = null)
    {
        $success = parent::register($category, $path, $mainContextId);

        if (!$success || !$this->getEnabled()) {
            return $success;
        }

        // UI messages
        Hook::add('TemplateManager::display', $this->addLoginMessage(...));
        Hook::add('Templates::Article::Footer::PageFooter', $this->addPaywallMessage(...));
        Hook::add('Templates::Common::Footer::PageFooter', $this->addFooterMessage(...));

        return $success;
    }

    /**
     * Resolve a UI message with fallback chain:
     * plugin setting → config.inc.php default → generic constant.
     *
     * Config keys use snake_case: default_login_hint, default_paywall_hint,
     * default_footer_message (under [wpojs] section).
     */
    private function getMessage(string $settingName, string $configKey, string $default): string
    {
        $context = Application::get()->getRequest()->getContext();
        $contextId = $context ? $context->getId() : 0;

        // 1. Admin-edited plugin setting (set via Settings form)
        $value = $this->getSetting($contextId, $settingName);
        if (!empty($value)) {
            return $value;
        }

        // 2. Instance default from config.inc.php [wpojs] section
        $configValue = Config::getVar('wpojs', $configKey, '');
        if (!empty($configValue)) {
            return $configValue;
        }

        // 3. Generic fallback constant
        return $default;
    }

    private function getDefault(string $configKey, string $constant): string
    {
        $configValue = Config::getVar('wpojs', $configKey, '');
        return !empty($configValue) ? $configValue : $constant;
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

            $messageTemplate = $this->getMessage('loginHint', 'default_login_hint', self::DEFAULT_LOGIN_HINT);
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
});
</script>');
        }

        return Hook::CONTINUE;
    }

    /**
     * Article page: hint for logged-in users who lack a subscription.
     * "Member? Contact support."
     */
    public function addPaywallMessage(string $hookName, array $params): bool
    {
        $output = &$params[2];

        $user = Application::get()->getRequest()->getUser();
        if (!$user) {
            return Hook::CONTINUE;
        }

        $context = Application::get()->getRequest()->getContext();
        if (!$context) {
            return Hook::CONTINUE;
        }

        $dao = DAORegistry::getDAO('IndividualSubscriptionDAO');
        $sub = $dao->getByUserIdForJournal($user->getId(), $context->getId());

        if (!$sub || (int) $sub->getStatus() !== self::SUB_STATUS_ACTIVE) {
            $supportEmail = Config::getVar('wpojs', 'support_email', '');
            if (!empty($supportEmail)) {
                $messageTemplate = $this->getMessage('paywallHint', 'default_paywall_hint', self::DEFAULT_PAYWALL_HINT);
                $escapedEmail = htmlspecialchars($supportEmail, ENT_QUOTES, 'UTF-8');
                $messageHtml = str_replace(
                    '{supportEmail}',
                    $escapedEmail,
                    $messageTemplate
                );
                $output .= '<div style="background:#fff3cd;border:1px solid #ffc107;border-radius:4px;padding:12px 16px;margin-top:16px;font-size:14px;">'
                    . $messageHtml
                    . '</div>';
            }
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

        $messageTemplate = $this->getMessage('footerMessage', 'default_footer_message', self::DEFAULT_FOOTER_MESSAGE);
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
            $this->updateSetting($contextId, 'loginHint', mb_substr(strip_tags($request->getUserVar('loginHint') ?? ''), 0, 500));
            $this->updateSetting($contextId, 'paywallHint', mb_substr(strip_tags($request->getUserVar('paywallHint') ?? ''), 0, 500));
            $this->updateSetting($contextId, 'footerMessage', mb_substr(strip_tags($request->getUserVar('footerMessage') ?? ''), 0, 500));

            return new \PKP\core\JSONMessage(true);
        }

        // Resolve defaults: config.inc.php override → generic constant
        $defaultLoginHint = $this->getDefault('default_login_hint', self::DEFAULT_LOGIN_HINT);
        $defaultPaywallHint = $this->getDefault('default_paywall_hint', self::DEFAULT_PAYWALL_HINT);
        $defaultFooterMessage = $this->getDefault('default_footer_message', self::DEFAULT_FOOTER_MESSAGE);

        $templateMgr = \APP\template\TemplateManager::getManager($request);
        $templateMgr->assign([
            'loginHint' => $this->getSetting($contextId, 'loginHint') ?: $defaultLoginHint,
            'paywallHint' => $this->getSetting($contextId, 'paywallHint') ?: $defaultPaywallHint,
            'footerMessage' => $this->getSetting($contextId, 'footerMessage') ?: $defaultFooterMessage,
            'defaultLoginHint' => $defaultLoginHint,
            'defaultPaywallHint' => $defaultPaywallHint,
            'defaultFooterMessage' => $defaultFooterMessage,
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
        $rateLimitRequests = (int) Config::getVar('wpojs', 'rate_limit_requests', 300);
        $rateLimitWindow = (int) Config::getVar('wpojs', 'rate_limit_window', 60);
        $rateLimitEnabled = $rateLimitRequests > 0;
        $rateLimitDetail = $rateLimitEnabled
            ? "{$rateLimitRequests} req / {$rateLimitWindow}s"
            : 'disabled (rate_limit_requests = 0)';

        $configChecks = [
            ['name' => 'API key defined', 'ok' => $apiKeyDefined],
            ['name' => 'Allowed IPs configured', 'ok' => !empty($allowedIps), 'detail' => $allowedIps ?: '(none)'],
            ['name' => 'WP member URL set', 'ok' => !empty($wpMemberUrl), 'detail' => $wpMemberUrl ?: '(not set)'],
            ['name' => 'Support email set', 'ok' => !empty($supportEmail), 'detail' => $supportEmail ?: '(not set)'],
            ['name' => 'Rate limiting', 'ok' => $rateLimitEnabled, 'detail' => $rateLimitDetail],
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
