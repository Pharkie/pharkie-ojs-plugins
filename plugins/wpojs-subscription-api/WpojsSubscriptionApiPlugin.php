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
 */

namespace APP\plugins\generic\wpojsSubscriptionApi;

use APP\core\Application;
use Illuminate\Support\Facades\DB;
use PKP\config\Config;
use PKP\db\DAORegistry;
use PKP\plugins\GenericPlugin;
use PKP\plugins\Hook;
use APP\plugins\generic\wpojsSubscriptionApi\WpojsApiLog;

class WpojsSubscriptionApiPlugin extends GenericPlugin
{
    private const SUB_STATUS_ACTIVE = 1;

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
     * Login page: "Member? First time here? Set your password"
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

            // Build HTML server-side with proper escaping (matches paywall/footer pattern)
            $hintHtml = __('plugins.generic.wpojsSubscriptionApi.loginHint', [
                'lostPasswordUrl' => htmlspecialchars($lostPasswordUrl, ENT_QUOTES, 'UTF-8'),
            ]);

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
                $output .= '<div style="background:#fff3cd;border:1px solid #ffc107;border-radius:4px;padding:12px 16px;margin-top:16px;font-size:14px;">'
                    . __('plugins.generic.wpojsSubscriptionApi.paywallHint', ['supportEmail' => htmlspecialchars($supportEmail)])
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

        $output .= '<div style="text-align:center;padding:8px 16px;font-size:13px;color:#666;border-top:1px solid #eee;margin-top:8px;">'
            . __('plugins.generic.wpojsSubscriptionApi.footerMessage', ['wpUrl' => htmlspecialchars($wpUrl)])
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

        array_unshift($actions, new \PKP\linkAction\LinkAction(
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
        ));

        return $actions;
    }

    public function manage($args, $request)
    {
        if ($request->getUserVar('verb') !== 'status') {
            return parent::manage($args, $request);
        }

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

    private function gatherStatusData(): array
    {
        // Config health checks.
        $apiKeyDefined = !empty(Config::getVar('wpojs', 'api_key_secret', ''))
            || !empty(Config::getVar('security', 'api_key_secret', ''));
        $allowedIps = Config::getVar('wpojs', 'allowed_ips', '');
        $wpMemberUrl = Config::getVar('wpojs', 'wp_member_url', '');
        $supportEmail = Config::getVar('wpojs', 'support_email', '');

        $configChecks = [
            ['name' => 'API key defined', 'ok' => $apiKeyDefined],
            ['name' => 'Allowed IPs configured', 'ok' => !empty($allowedIps), 'detail' => $allowedIps ?: '(none)'],
            ['name' => 'WP member URL set', 'ok' => !empty($wpMemberUrl), 'detail' => $wpMemberUrl ?: '(not set)'],
            ['name' => 'Support email set', 'ok' => !empty($supportEmail), 'detail' => $supportEmail ?: '(not set)'],
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
            $subTypeCounts = DB::table('subscriptions')
                ->join('subscription_types', 'subscriptions.type_id', '=', 'subscription_types.type_id')
                ->where('subscription_types.journal_id', $journalId)
                ->where('subscriptions.status', self::SUB_STATUS_ACTIVE)
                ->select('subscription_types.type_name', DB::raw('COUNT(*) as count'))
                ->groupBy('subscription_types.type_id', 'subscription_types.type_name')
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

    public function getDisplayName()
    {
        return __('plugins.generic.wpojsSubscriptionApi.displayName');
    }

    public function getDescription()
    {
        return __('plugins.generic.wpojsSubscriptionApi.description');
    }
}
