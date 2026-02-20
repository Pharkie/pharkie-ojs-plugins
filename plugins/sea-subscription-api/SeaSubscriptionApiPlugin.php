<?php

/**
 * SEA Subscription API Plugin
 *
 * Exposes REST endpoints for managing OJS user accounts and subscriptions,
 * called by the SEA WordPress membership sync plugin (sea-ojs-sync).
 *
 * Also adds UI messages: login hint, paywall hint, and site footer.
 *
 * Deploy to: plugins/generic/seaSubscriptionApi/ in OJS installation.
 * Requires OJS 3.5+ for plugin API extensibility (pkp-lib #9434).
 *
 * Configuration in config.inc.php:
 *   [sea]
 *   allowed_ips = "1.2.3.4,5.6.7.8"
 *   wp_member_url = "https://community.existentialanalysis.org.uk"
 *   support_email = "support@existentialanalysis.org.uk"
 */

namespace APP\plugins\generic\seaSubscriptionApi;

use APP\core\Application;
use PKP\config\Config;
use PKP\core\APIRouter;
use PKP\db\DAORegistry;
use PKP\plugins\GenericPlugin;
use PKP\plugins\Hook;

class SeaSubscriptionApiPlugin extends GenericPlugin
{
    private const SUB_STATUS_ACTIVE = 1;

    public function register($category, $path, $mainContextId = null)
    {
        $success = parent::register($category, $path, $mainContextId);

        if (!$success || !$this->getEnabled()) {
            return $success;
        }

        // API endpoints
        Hook::add('APIHandler::endpoints::plugin', function (
            string $hookName,
            APIRouter $apiRouter
        ): bool {
            $apiRouter->registerPluginApiControllers([
                new SeaApiController(),
            ]);
            return Hook::CONTINUE;
        });

        // UI messages
        Hook::add('TemplateManager::display', $this->addLoginMessage(...));
        Hook::add('Templates::Article::Footer::PageFooter', $this->addPaywallMessage(...));
        Hook::add('Templates::Common::Footer::PageFooter', $this->addFooterMessage(...));

        return $success;
    }

    /**
     * Login page: "SEA member? First time here? Set your password"
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

            $templateMgr->addHeader('sea-login-message', '<style>
.sea-login-hint { background: #e8f4f8; border: 1px solid #b8daff; border-radius: 4px; padding: 12px 16px; margin-bottom: 16px; font-size: 14px; line-height: 1.5; }
.sea-login-hint a { color: #0056b3; text-decoration: underline; }
</style>
<script>
document.addEventListener("DOMContentLoaded", function() {
    var h1 = document.querySelector(".page_login h1");
    if (h1) {
        var div = document.createElement("div");
        div.className = "sea-login-hint";
        div.innerHTML = "' . __('plugins.generic.seaSubscriptionApi.loginHint', ['lostPasswordUrl' => $lostPasswordUrl]) . '";
        h1.insertAdjacentElement("afterend", div);
    }
});
</script>');
        }

        return Hook::CONTINUE;
    }

    /**
     * Article page: hint for logged-in users who lack a subscription.
     * "SEA member? Contact support."
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
            $supportEmail = Config::getVar('sea', 'support_email', 'support@existentialanalysis.org.uk');
            $output .= '<div style="background:#fff3cd;border:1px solid #ffc107;border-radius:4px;padding:12px 16px;margin-top:16px;font-size:14px;">'
                . __('plugins.generic.seaSubscriptionApi.paywallHint', ['supportEmail' => htmlspecialchars($supportEmail)])
                . '</div>';
        }

        return Hook::CONTINUE;
    }

    /**
     * Site footer: "Your journal access is provided by your SEA membership."
     */
    public function addFooterMessage(string $hookName, array $params): bool
    {
        $output = &$params[2];

        $wpUrl = Config::getVar('sea', 'wp_member_url', '');
        if (empty($wpUrl)) {
            return Hook::CONTINUE;
        }

        $output .= '<div style="text-align:center;padding:8px 16px;font-size:13px;color:#666;border-top:1px solid #eee;margin-top:8px;">'
            . __('plugins.generic.seaSubscriptionApi.footerMessage', ['wpUrl' => htmlspecialchars($wpUrl)])
            . '</div>';

        return Hook::CONTINUE;
    }

    public function getDisplayName()
    {
        return __('plugins.generic.seaSubscriptionApi.displayName');
    }

    public function getDescription()
    {
        return __('plugins.generic.seaSubscriptionApi.description');
    }
}
