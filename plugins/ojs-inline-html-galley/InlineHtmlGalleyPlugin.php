<?php

/**
 * Inline HTML Galley Plugin
 *
 * Renders HTML galley content inline on article pages when the user has access
 * (open-access, active subscription, or completed purchase). Replaces the
 * separate full-text viewer link with inline content — no extra click needed.
 *
 * Deploy to: plugins/generic/inlineHtmlGalley/ in OJS installation.
 * Requires OJS 3.5+.
 */

namespace APP\plugins\generic\inlineHtmlGalley;

use APP\core\Application;
use APP\facades\Repo;
use Illuminate\Support\Facades\DB;
use PKP\db\DAORegistry;
use PKP\plugins\GenericPlugin;
use PKP\security\Role;
use PKP\plugins\Hook;

class InlineHtmlGalleyPlugin extends GenericPlugin
{
    // Default setting values — override via plugin settings UI
    private const DEFAULTS = [
        'organisationName' => 'your organisation',
        'membershipUrl' => '',
        'paywallSectionName' => 'Articles',
        'archiveNoticeEnabled' => true,
        'syncedMemberMessage' => 'Showing article full text linked to your {orgName} membership. Thanks for your support!',
        'subscriberMessage' => 'Showing article full text via your journal subscription.',
        'purchaseMessage' => 'Showing article full text. You have access via direct purchase.',
        'adminMessage' => 'Showing article full text. You have access as a journal administrator.',
    ];

    /**
     * Get a plugin setting with a default fallback.
     */
    private function cfg(string $key): string|bool
    {
        $contextId = Application::get()->getRequest()->getContext()?->getId() ?? 0;
        $val = $this->getSetting($contextId, $key);
        return $val !== null && $val !== '' ? $val : (self::DEFAULTS[$key] ?? '');
    }

    public function register($category, $path, $mainContextId = null)
    {
        $success = parent::register($category, $path, $mainContextId);

        if (!$success || !$this->getEnabled()) {
            return $success;
        }

        Hook::add('Templates::Article::Main', $this->renderInlineHtmlGalley(...));
        Hook::add('TemplateManager::display', $this->hideHtmlGalleyLink(...));

        return $success;
    }

    // ---------------------------------------------------------------
    // Settings UI
    // ---------------------------------------------------------------

    public function getActions($request, $actionArgs)
    {
        $actions = parent::getActions($request, $actionArgs);
        if (!$this->getEnabled()) {
            return $actions;
        }
        $router = $request->getRouter();
        array_unshift($actions, new \PKP\linkAction\LinkAction(
            'settings',
            new \PKP\linkAction\request\AjaxModal(
                $router->url($request, null, null, 'manage', null, [
                    'verb' => 'settings',
                    'plugin' => $this->getName(),
                    'category' => 'generic',
                ]),
                $this->getDisplayName()
            ),
            __('manager.plugins.settings'),
            null
        ));
        return $actions;
    }

    public function manage($args, $request)
    {
        $verb = $request->getUserVar('verb');
        if ($verb !== 'settings') {
            return parent::manage($args, $request);
        }

        $context = $request->getContext();
        $contextId = $context->getId();
        $templateMgr = \APP\template\TemplateManager::getManager($request);

        if ($request->getUserVar('save')) {
            // Save settings
            $settings = [
                'organisationName', 'membershipUrl',
                'paywallSectionName', 'syncedMemberMessage', 'subscriberMessage',
                'purchaseMessage', 'adminMessage',
            ];
            foreach ($settings as $key) {
                $this->updateSetting($contextId, $key, (string) $request->getUserVar($key));
            }
            $this->updateSetting($contextId, 'archiveNoticeEnabled',
                (bool) $request->getUserVar('archiveNoticeEnabled'));

            return new \PKP\core\JSONMessage(true);
        }

        // Display form
        $templateMgr->assign([
            'pluginName' => $this->getName(),
            'organisationName' => $this->getSetting($contextId, 'organisationName') ?: self::DEFAULTS['organisationName'],
            'membershipUrl' => $this->getSetting($contextId, 'membershipUrl') ?: self::DEFAULTS['membershipUrl'],
            'paywallSectionName' => $this->getSetting($contextId, 'paywallSectionName') ?: self::DEFAULTS['paywallSectionName'],
            'archiveNoticeEnabled' => $this->getSetting($contextId, 'archiveNoticeEnabled') ?? self::DEFAULTS['archiveNoticeEnabled'],
            'syncedMemberMessage' => $this->getSetting($contextId, 'syncedMemberMessage') ?: self::DEFAULTS['syncedMemberMessage'],
            'subscriberMessage' => $this->getSetting($contextId, 'subscriberMessage') ?: self::DEFAULTS['subscriberMessage'],
            'purchaseMessage' => $this->getSetting($contextId, 'purchaseMessage') ?: self::DEFAULTS['purchaseMessage'],
            'adminMessage' => $this->getSetting($contextId, 'adminMessage') ?: self::DEFAULTS['adminMessage'],
        ]);

        return $templateMgr->fetchJson($this->getTemplateResource('settings.tpl'));
    }

    // ---------------------------------------------------------------
    // Inline rendering
    // ---------------------------------------------------------------

    /**
     * Render HTML galley content inline on the article page.
     */
    public function renderInlineHtmlGalley(string $hookName, array $params): bool
    {
        $templateMgr = $params[1];
        $output = &$params[2];

        $article = $templateMgr->getTemplateVars('article');
        $publication = $templateMgr->getTemplateVars('publication');
        if (!$article || !$publication) {
            return Hook::CONTINUE;
        }

        $hasAccess = $templateMgr->getTemplateVars('hasAccess');

        // Non-subscriber CTA (shown instead of inline content when no access)
        if (!$hasAccess) {
            $output .= $this->getNonSubscriberNotice();
            return Hook::CONTINUE;
        }

        // Find HTML galley labeled "Full Text"
        $galleys = $publication->getData('galleys');
        $htmlGalley = null;
        foreach ($galleys as $galley) {
            if ($galley->getLabel() === 'Full Text'
                && $galley->getFileType() === 'text/html') {
                $htmlGalley = $galley;
                break;
            }
        }

        if (!$htmlGalley) {
            return Hook::CONTINUE;
        }

        $file = Repo::submissionFile()->get($htmlGalley->getData('submissionFileId'));
        if (!$file) {
            return Hook::CONTINUE;
        }

        $filePath = rtrim(\PKP\config\Config::getVar('files', 'files_dir'), '/')
            . '/' . $file->getData('path');
        if (!file_exists($filePath)) {
            return Hook::CONTINUE;
        }

        $htmlContent = file_get_contents($filePath);

        // Extract <body> content
        if (preg_match('/<body[^>]*>(.*?)<\/body>/si', $htmlContent, $matches)) {
            $bodyContent = $matches[1];
        } else {
            $bodyContent = $htmlContent;
        }

        $bodyContent = trim($bodyContent);
        if (empty($bodyContent)) {
            return Hook::CONTINUE;
        }

        // Subscriber notice — only on paywalled articles
        $subscriberNotice = $this->getSubscriberNotice($publication);

        // Archive quality notice (configurable)
        $archiveNotice = '';
        if ($this->cfg('archiveNoticeEnabled')) {
            $context = Application::get()->getRequest()->getContext();
            $email = htmlspecialchars($context ? $context->getData('contactEmail') : '');
            $emailLink = $email ? '<a href="mailto:' . $email . '">' . $email . '</a>' : 'the journal';
            $archiveNotice = '<div style="margin-bottom:16px;padding:10px 14px;background:#f8f5f0;'
                . 'border:1px solid #e0d8cc;border-radius:4px;font-size:13px;color:#666;line-height:1.5;">'
                . 'This article has been digitally restored from print. If you spot any errors '
                . 'or formatting issues, please email ' . $emailLink . '.'
                . ' You can also view the PDF version of this article.'
                . '</div>';
        }

        $output .= '<section class="item inline-html-galley">'
            . '<h2 class="label">Full Text</h2>'
            . $subscriberNotice
            . $archiveNotice
            . '<div class="value">' . $bodyContent . '</div>'
            . '</section>';

        return Hook::CONTINUE;
    }

    /**
     * Subscriber notice for paywalled articles.
     * Only shown on the configured paywall section.
     */
    private function getSubscriberNotice($publication): string
    {
        $paywallSection = $this->cfg('paywallSectionName');
        $sectionId = $publication->getData('sectionId');
        $section = Repo::section()->get($sectionId);
        if (!$section || $section->getLocalizedTitle() !== $paywallSection) {
            return '';
        }

        $request = Application::get()->getRequest();
        $user = $request->getUser();
        if (!$user) {
            return '';
        }

        // Site admins and journal managers
        $templateMgr = \APP\template\TemplateManager::getManager($request);
        $userRoles = $templateMgr->getTemplateVars('userRoles') ?? [];
        $privilegedRoles = [Role::ROLE_ID_SITE_ADMIN, Role::ROLE_ID_MANAGER];
        if (array_intersect($privilegedRoles, $userRoles)) {
            $message = $this->cfg('adminMessage');
        } elseif (DB::table('user_settings')
            ->where('user_id', $user->getId())
            ->where('setting_name', 'wpojs_created_by_sync')
            ->exists()) {
            // Synced member
            $orgName = htmlspecialchars($this->cfg('organisationName'));
            $message = str_replace('{orgName}', $orgName, $this->cfg('syncedMemberMessage'));
        } else {
            // Direct OJS subscriber or purchaser
            $subscriptionDao = DAORegistry::getDAO('IndividualSubscriptionDAO');
            $context = $request->getContext();
            $subscription = $subscriptionDao->getByUserIdForJournal($user->getId(), $context->getId());
            if (!$subscription || $subscription->getStatus() !== \APP\subscription\Subscription::SUBSCRIPTION_STATUS_ACTIVE) {
                $completedPaymentDao = DAORegistry::getDAO('OJSCompletedPaymentDAO');
                $submissionId = $publication->getData('submissionId');
                if ($completedPaymentDao->hasPaidPurchaseArticle($user->getId(), $submissionId)) {
                    $message = $this->cfg('purchaseMessage');
                } else {
                    return '';
                }
            } else {
                $message = $this->cfg('subscriberMessage');
            }
        }

        return '<div style="margin-bottom:16px;padding:10px 14px;background:#e8f0fe;'
            . 'border:1px solid #b8d4f0;border-radius:4px;font-size:13px;color:#1a4a7a;line-height:1.5;">'
            . $message
            . '</div>';
    }

    /**
     * Non-subscriber CTA box.
     */
    private function getNonSubscriberNotice(): string
    {
        $orgName = htmlspecialchars($this->cfg('organisationName'));
        $membershipUrl = htmlspecialchars($this->cfg('membershipUrl'));

        $membershipLink = $membershipUrl
            ? '<a href="' . $membershipUrl . '" style="color:#7a5a1a;font-weight:600;">' . $orgName . ' membership</a>'
            : $orgName . ' membership';

        return '<section class="item inline-html-galley-cta">'
            . '<div style="margin-top:1em;padding:16px 20px;background:#fef7ec;'
            . 'border:1px solid #f0d8a0;border-radius:6px;font-size:14px;color:#7a5a1a;line-height:1.6;">'
            . '<strong>Full text available to members</strong><br>'
            . 'Existing members: please log in (top right) with your membership password to view content. '
            . 'Complete access to the full archive of articles is available with '
            . $membershipLink . '. '
            . 'Or to buy a single article or issue, register an account on this website, '
            . 'then select a padlocked full text button to purchase.'
            . '</div>'
            . '</section>';
    }

    /**
     * Hide galley links and add inline HTML styling.
     */
    public function hideHtmlGalleyLink(string $hookName, array $args): bool
    {
        $templateMgr = $args[0];
        $template = $args[1] ?? '';

        if (!str_contains($template, 'article.tpl')
            && !str_contains($template, 'issue.tpl')
            && !str_contains($template, 'issueArchive.tpl')
            && !str_contains($template, 'indexJournal.tpl')) {
            return Hook::CONTINUE;
        }

        $templateMgr->addHeader('inline-html-galley-styles', '<style>
.inline-html-galley { margin-top: 2em; }
.inline-html-galley .value { line-height: 1.7; font-size: 15px; }
.inline-html-galley .value p { margin-bottom: 1em; }
</style>
<script>
document.addEventListener("DOMContentLoaded", function() {
    var isArticlePage = !!document.querySelector(".obj_article_details");
    var hasInlineContent = !!document.querySelector(".inline-html-galley");
    var refsSection = document.querySelector(".item.references");
    if (refsSection) {
        var refsValue = refsSection.querySelector(".value");
        if (refsValue && !refsValue.textContent.trim()) {
            refsSection.style.display = "none";
        }
    }
    document.querySelectorAll(".obj_galley_link").forEach(function(el) {
        var label = el.textContent.trim();
        if (!isArticlePage) {
            el.style.display = "none";
        } else if (label === "Full Text" && hasInlineContent) {
            el.style.display = "none";
        }
    });
});
</script>');

        return Hook::CONTINUE;
    }

    public function getDisplayName()
    {
        return __('plugins.generic.inlineHtmlGalley.displayName');
    }

    public function getDescription()
    {
        return __('plugins.generic.inlineHtmlGalley.description');
    }
}
