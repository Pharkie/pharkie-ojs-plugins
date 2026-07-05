<?php

/**
 * Umami Analytics Plugin
 *
 * Injects the Umami tracking script into the OJS reader-facing (frontend) pages
 * and wires up custom events for the journal actions worth measuring:
 * galley downloads (PDF/HTML/XML), paywall impressions, membership/purchase
 * clicks, DOI/reference clicks, search submissions, and login/register clicks.
 *
 * Privacy-friendly, cookieless. Works with Umami Cloud (cloud.umami.is) or a
 * self-hosted Umami instance — set the script URL + website ID in the plugin
 * settings. Nothing loads until a website ID is configured.
 *
 * Deploy to: plugins/generic/umamiAnalytics/ in OJS installation.
 * Requires OJS 3.5+.
 */

namespace APP\plugins\generic\umamiAnalytics;

use APP\core\Application;
use PKP\plugins\GenericPlugin;
use PKP\plugins\Hook;
use PKP\security\Role;

class UmamiAnalyticsPlugin extends GenericPlugin
{
    // Default setting values — override via plugin settings UI
    private const DEFAULTS = [
        'websiteId' => '',
        'scriptUrl' => 'https://cloud.umami.is/script.js',
        'dataDomains' => '',
        'trackEvents' => true,
        'excludeStaff' => true,
    ];

    /**
     * Get a plugin setting with a default fallback.
     */
    private function cfg(string $key): string|bool
    {
        $contextId = Application::get()->getRequest()->getContext()?->getId() ?? 0;
        $val = $this->getSetting($contextId, $key);
        if ($val === null || $val === '') {
            return self::DEFAULTS[$key] ?? '';
        }
        return $val;
    }

    public function register($category, $path, $mainContextId = null)
    {
        $success = parent::register($category, $path, $mainContextId);

        if (!$success || !$this->getEnabled()) {
            return $success;
        }

        Hook::add('TemplateManager::display', $this->injectTracking(...));

        return $success;
    }

    // ---------------------------------------------------------------
    // Tracking injection
    // ---------------------------------------------------------------

    /**
     * Inject the Umami script (and optional custom-event bindings) into the
     * <head> of reader-facing pages only. Editorial/back-office templates and
     * AJAX fragments are skipped.
     */
    public function injectTracking(string $hookName, array $args): bool
    {
        $templateMgr = $args[0];
        $template = $args[1] ?? '';

        // Frontend full pages only — these are the ones that render <head>.
        // (str_contains, not str_starts_with: some themes pass a fuller path.)
        if (!is_string($template) || !str_contains($template, 'frontend/')) {
            return Hook::CONTINUE;
        }

        $websiteId = trim((string) $this->cfg('websiteId'));
        if ($websiteId === '') {
            return Hook::CONTINUE;
        }

        // Optionally keep journal staff out of the reader stats. Managers and
        // admins browsing the public site would otherwise inflate counts.
        if ($this->cfg('excludeStaff')) {
            $userRoles = $templateMgr->getTemplateVars('userRoles') ?? [];
            $staffRoles = [Role::ROLE_ID_SITE_ADMIN, Role::ROLE_ID_MANAGER, Role::ROLE_ID_SUB_EDITOR];
            if (array_intersect($staffRoles, $userRoles)) {
                return Hook::CONTINUE;
            }
        }

        $scriptUrl = trim((string) $this->cfg('scriptUrl')) ?: self::DEFAULTS['scriptUrl'];

        $attrs = 'defer src="' . htmlspecialchars($scriptUrl, ENT_QUOTES)
            . '" data-website-id="' . htmlspecialchars($websiteId, ENT_QUOTES) . '"';

        $dataDomains = trim((string) $this->cfg('dataDomains'));
        if ($dataDomains !== '') {
            $attrs .= ' data-domains="' . htmlspecialchars($dataDomains, ENT_QUOTES) . '"';
        }

        $templateMgr->addHeader('umamiAnalytics', '<script ' . $attrs . '></script>');

        if ($this->cfg('trackEvents')) {
            $templateMgr->addHeader('umamiAnalyticsEvents', $this->getEventScript());
        }

        return Hook::CONTINUE;
    }

    /**
     * Small dependency-free snippet that maps notable reader actions onto
     * Umami custom events. Guards on window.umami so it is inert if the tracker
     * is blocked or still loading.
     */
    private function getEventScript(): string
    {
        return <<<'HTML'
<script>
(function () {
    function track(name, data) {
        try { if (window.umami && typeof window.umami.track === 'function') { window.umami.track(name, data); } } catch (e) {}
    }
    function galleyType(s) {
        if (/xml|jats/i.test(s)) return 'xml';
        if (/pdf/i.test(s)) return 'pdf';
        if (/html|full\s*text/i.test(s)) return 'html';
        return 'other';
    }

    // Delegated click tracking — survives content added after load.
    document.addEventListener('click', function (e) {
        var a = e.target.closest ? e.target.closest('a') : null;
        if (!a) return;
        var href = a.getAttribute('href') || '';
        var label = (a.textContent || '').trim().slice(0, 80);

        // Galley file downloads (PDF / HTML / XML)
        if (href.indexOf('/download/') !== -1 || a.classList.contains('obj_galley_link')) {
            track('download', { type: galleyType(label + ' ' + href), label: label, path: location.pathname });
            return;
        }
        // Purchase / subscription payment links
        if (/\/payment\/|\/purchase|subscri/i.test(href)) {
            track('purchase-click', { href: href, path: location.pathname });
            return;
        }
        // Membership CTA link (Inline HTML Galley non-subscriber box)
        if (a.closest('.inline-html-galley-cta')) {
            track('membership-click', { href: href, path: location.pathname });
            return;
        }
        // DOI / reference resolver clicks
        var m = href.match(/^https?:\/\/(?:dx\.)?doi\.org\/(.+)$/i);
        if (m) {
            track('doi-click', { doi: m[1], path: location.pathname });
            return;
        }
        // Login / register
        if (/\/user\/register/i.test(href)) { track('register-click', { path: location.pathname }); return; }
        if (/\/login/i.test(href)) { track('login-click', { path: location.pathname }); return; }
    }, true);

    document.addEventListener('DOMContentLoaded', function () {
        // Paywall impression — the non-subscriber CTA was rendered on this page.
        if (document.querySelector('.inline-html-galley-cta')) {
            track('paywall-view', { path: location.pathname });
        }
        // Search submissions (query text intentionally not captured).
        var forms = document.querySelectorAll('form[action*="/search"]');
        for (var i = 0; i < forms.length; i++) {
            forms[i].addEventListener('submit', function () { track('search', { path: location.pathname }); });
        }
    });
})();
</script>
HTML;
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
            foreach (['websiteId', 'scriptUrl', 'dataDomains'] as $key) {
                $this->updateSetting($contextId, $key, trim((string) $request->getUserVar($key)));
            }
            $this->updateSetting($contextId, 'trackEvents', (bool) $request->getUserVar('trackEvents'));
            $this->updateSetting($contextId, 'excludeStaff', (bool) $request->getUserVar('excludeStaff'));

            return new \PKP\core\JSONMessage(true);
        }

        $templateMgr->assign([
            'pluginName' => $this->getName(),
            'websiteId' => $this->getSetting($contextId, 'websiteId') ?: self::DEFAULTS['websiteId'],
            'scriptUrl' => $this->getSetting($contextId, 'scriptUrl') ?: self::DEFAULTS['scriptUrl'],
            'dataDomains' => $this->getSetting($contextId, 'dataDomains') ?: self::DEFAULTS['dataDomains'],
            'trackEvents' => $this->getSetting($contextId, 'trackEvents') ?? self::DEFAULTS['trackEvents'],
            'excludeStaff' => $this->getSetting($contextId, 'excludeStaff') ?? self::DEFAULTS['excludeStaff'],
        ]);

        return $templateMgr->fetchJson($this->getTemplateResource('settings.tpl'));
    }

    public function getDisplayName()
    {
        return __('plugins.generic.umamiAnalytics.displayName');
    }

    public function getDescription()
    {
        return __('plugins.generic.umamiAnalytics.description');
    }
}
