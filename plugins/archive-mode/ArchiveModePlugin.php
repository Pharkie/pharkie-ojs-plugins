<?php

/**
 * Archive Mode plugin
 *
 * Puts the journal into a read-only, archived state once it has moved to a
 * new home. For the SEA that is Harbour cut-over: OJS stays up for a few days
 * only, as the landing page for the journal's DOIs until Crossref holds the
 * new addresses, and nobody should sign in, register, reset a password or buy
 * anything on it.
 *
 * With archive mode on:
 * - every reader-facing page, the PDF and HTML galley viewers included,
 *   starts with a bar saying this is an archived copy, linking to the same
 *   article or issue on the new site when the map (map.json) knows it, and
 *   to the journal's home there when it does not;
 * - every route that signs in, registers, resets a password, edits a profile
 *   or takes a payment shows one message instead (ArchiveModeHandler,
 *   ArchiveModeRoutes::isClosed);
 * - OJS is told payments are off for readers, so no price or purchase step is
 *   offered, whatever the journal's fees are set to;
 * - the sidebar loses every block that offers something the archive no
 *   longer does (event banners, adverts, submissions, subscriptions) and the
 *   links to For Readers and For Authors, which are closed;
 * - the main menu and the footer lose their links to closed pages
 *   ("Submissions", "Submit an Article");
 * - Register and Login leave the header.
 * Reading is unchanged: the home page, issues, articles, open galleys,
 * search and OAI-PMH.
 *
 * The switch is ONE config value, `[archive] moved_to` in config.inc.php,
 * templated from OJS_ARCHIVE_MOVED_TO by docker/ojs/entrypoint.sh: the URL of
 * the journal's new home. Empty means off, and the plugin then adds no hooks
 * at all. The plugin is enabled ahead of time, so the day's step is setting
 * that value and recreating the container. See README.md.
 *
 * Two other plugins read the same value and stand down with it:
 * wpojsSubscriptionApi (its membership footer and sign-in messages) and
 * inlineHtmlGalley (its "log in or buy" box).
 *
 * Deploy to: plugins/generic/archiveMode/ in the OJS installation.
 * Requires OJS 3.5+.
 */

namespace APP\plugins\generic\archiveMode;

use APP\core\Application;
use PKP\config\Config;
use PKP\core\PKPRequest;
use PKP\plugins\GenericPlugin;
use PKP\plugins\Hook;
use PKP\plugins\PluginRegistry;
use PKP\security\Role;
use PKP\security\Validation;

class ArchiveModePlugin extends GenericPlugin
{
    /** Galley viewers render their own full-height page; the bar has to share it. */
    private const VIEWER_TEMPLATES = ['pdfJsViewer', 'htmlArticleGalley'];

    private ?array $map = null;
    private bool $filterAdded = false;
    private ?bool $staff = null;

    /** The journal's new home, or '' when archive mode is off. */
    public static function movedTo(): string
    {
        return trim((string) Config::getVar('archive', 'moved_to', ''));
    }

    public function register($category, $path, $mainContextId = null)
    {
        $success = parent::register($category, $path, $mainContextId);

        if (!$success || !$this->getEnabled() || self::movedTo() === '') {
            return $success;
        }

        // First in the queue, ahead of wpojsSubscriptionApi's own LoadHandler
        // for the password reset, so a closed route never reaches it.
        Hook::add('LoadHandler', $this->closeRoutes(...), Hook::SEQUENCE_CORE);
        Hook::add('TemplateManager::display', $this->addBar(...));
        // Last, so every item's address is final by the time it is read.
        Hook::add('NavigationMenus::displaySettings', $this->hideClosedMenuItem(...), Hook::SEQUENCE_LAST);

        return $success;
    }

    /**
     * A site plugin, enabled once for the whole install: OJS's site-level
     * pages (/index/login, /index/user/register) sign people in and register
     * them too, and a plugin enabled for the journal alone never loads there.
     */
    public function isSitePlugin()
    {
        return true;
    }

    public function getDisplayName()
    {
        return __('plugins.generic.archiveMode.displayName');
    }

    public function getDescription()
    {
        return __('plugins.generic.archiveMode.description');
    }

    // ---------------------------------------------------------------
    // Closed routes and payments
    // ---------------------------------------------------------------

    /**
     * LoadHandler: hand a closed route to ArchiveModeHandler, and tell OJS
     * payments are off, and the sidebar's offers gone, for every page a
     * reader sees (the closed message included).
     */
    public function closeRoutes(string $hookName, array $args): bool
    {
        $page = (string) $args[0];
        $op = (string) $args[1];
        $request = Application::get()->getRequest();
        $routeArgs = $request->getRequestedArgs();
        $closed = ArchiveModeRoutes::isClosed($page, $op, $routeArgs);

        // Stripe's callbacks (the only payment routes left open) finish a
        // payment already taken, so they keep OJS's real payment settings.
        $this->forReaders($request, $closed || $page !== 'payment');

        if ($closed) {
            $args[3] = new ArchiveModeHandler();
            $args[1] = 'closed';
            return Hook::ABORT;
        }

        return Hook::CONTINUE;
    }

    /**
     * What a reader is offered, for this request only, in memory: never
     * saved. Payments off (when asked), and the sidebar without its offers.
     *
     * The sidebar is read from the request's journal when the page is drawn
     * (PKPTemplateManager::displaySidebar), and so are the Information
     * block's For Readers and For Authors links, each shown only while its
     * text is set.
     *
     * Not for journal managers or the site admin: a settings form they open
     * would read these values, and saving it would write them to the database.
     */
    private function forReaders(PKPRequest $request, bool $paymentsOff): void
    {
        $context = $request->getContext();
        if (!$context || $this->isStaff($request)) {
            return;
        }
        if ($paymentsOff) {
            self::zeroPayments($context);
        }

        $sidebar = $context->getData('sidebar');
        if (is_array($sidebar)) {
            $custom = [];
            foreach ((array) PluginRegistry::getPlugins('blocks') as $name => $plugin) {
                if ($plugin instanceof \APP\plugins\generic\customBlockManager\CustomBlockPlugin) {
                    $custom[] = (string) $name;
                }
            }
            $context->setData('sidebar', ArchiveModeRoutes::readingSidebar($sidebar, $custom));
        }
        // For Readers and For Authors are closed, so the Information block
        // stops linking to them. For Librarians stays.
        $context->setData('readerInformation', null);
        $context->setData('authorInformation', null);
    }

    /**
     * Payments off, on a journal object, in memory.
     *
     * With paymentsEnabled false, OJSPaymentManager reports no purchase,
     * issue or membership fee, so a reader without access is sent to sign in
     * or to About > Subscriptions, both of which are closed. The fees go to 0
     * as well, because the galley buttons print the journal's fee from the
     * context whether or not payments are on. That holds even if the
     * journal's fees are still set in the database.
     *
     * @param \PKP\context\Context $context
     */
    private static function zeroPayments($context): void
    {
        $context->setData('paymentsEnabled', false);
        foreach (['purchaseArticleFee', 'purchaseIssueFee', 'membershipFee'] as $fee) {
            $context->setData($fee, 0);
        }
    }

    /** Once per request: the menu asks for every item. */
    private function isStaff(PKPRequest $request): bool
    {
        if ($this->staff === null) {
            $contextId = $request->getContext()?->getId();
            $this->staff = $request->getUser() !== null && (Validation::isSiteAdmin()
                || ($contextId !== null && Validation::isAuthorized(Role::ROLE_ID_MANAGER, $contextId)));
        }
        return $this->staff;
    }

    // ---------------------------------------------------------------
    // Links to closed pages: the main menu and the footer
    // ---------------------------------------------------------------

    /**
     * NavigationMenus::displaySettings: a menu item that leads to a closed
     * page is not shown ("Submissions" locally, the SEA's "Submit an
     * Article" on live). In memory, for this request; the menu is untouched.
     *
     * Not the user menu (Register, Login, and a signed-in reader's name with
     * Log Out under it): the bar's CSS hides all of it for readers, and its
     * name item leads to the closed profile, so hiding by address here would
     * take Log Out from the page as well.
     */
    public function hideClosedMenuItem(string $hookName, array $args): bool
    {
        [$item, $menu] = $args;
        $request = Application::get()->getRequest();
        if ($item->getIsDisplayed() && $menu->getAreaName() !== 'user' && !$this->isStaff($request)
            && ArchiveModeRoutes::linksToClosed((string) $item->getUrl(), $request->getContext()?->getPath(), self::hosts($request))) {
            $item->setIsDisplayed(false);
        }
        return Hook::CONTINUE;
    }

    /**
     * This site's host names: the one the request came in on, and the one
     * in config.inc.php's base_url, which OJS writes its own links with.
     *
     * @return string[]
     */
    private static function hosts(PKPRequest $request): array
    {
        $hosts = [];
        foreach ([$request->getBaseUrl(), (string) Config::getVar('general', 'base_url')] as $url) {
            $host = parse_url($url, PHP_URL_HOST);
            if (is_string($host) && $host !== '') {
                $hosts[] = $host;
            }
        }
        return $hosts;
    }

    // ---------------------------------------------------------------
    // Where this page lives now
    // ---------------------------------------------------------------

    /**
     * [kind, url] of the page on the new site standing for this request.
     * A closed route that OJS reached by redirecting a reader to sign in
     * carries the page they wanted in `source`, so the link follows that.
     */
    private function destination(PKPRequest $request): array
    {
        $page = (string) $request->getRequestedPage();
        $op = (string) $request->getRequestedOp();
        $args = $request->getRequestedArgs();
        $query = ['query' => (string) $request->getUserVar('query')];

        $source = $request->getUserVar('source');
        if (is_string($source) && ArchiveModeRoutes::isClosed($page, $op, $args)) {
            $parsed = ArchiveModeRoutes::parseSource($source, $request->getContext()?->getPath());
            if ($parsed !== null) {
                [$page, $op, $args] = $parsed;
            }
        }

        return ArchiveModeRoutes::destination(self::movedTo(), $page, $op, $args, $this->map(), $query);
    }

    /** The link's words for a destination kind. */
    private function linkLabel(string $kind): string
    {
        return __('plugins.generic.archiveMode.link.' . $kind);
    }

    /**
     * map.json, beside this file: OJS ids to Harbour slugs, written on the
     * box from Harbour's database (README.md). Missing or unreadable means
     * every page links to the journal's home.
     */
    private function map(): array
    {
        if ($this->map === null) {
            $file = __DIR__ . '/map.json';
            $data = is_readable($file) ? json_decode((string) file_get_contents($file), true) : null;
            $this->map = is_array($data) ? $data : [];
        }
        return $this->map;
    }

    // ---------------------------------------------------------------
    // The bar
    // ---------------------------------------------------------------

    /**
     * TemplateManager::display: on reader-facing pages and the galley
     * viewers, add an output filter that puts the bar first in <body>.
     * Server-side, so it shows with JavaScript off. The back office is left
     * alone.
     */
    public function addBar(string $hookName, array $args): bool
    {
        $templateMgr = $args[0];
        $template = $args[1] ?? '';
        if (!is_string($template) || $this->filterAdded) {
            return Hook::CONTINUE;
        }

        $isViewer = false;
        foreach (self::VIEWER_TEMPLATES as $viewer) {
            $isViewer = $isViewer || str_contains($template, $viewer);
        }
        if (!$isViewer && !str_contains($template, 'frontend/')) {
            return Hook::CONTINUE;
        }

        $request = Application::get()->getRequest();
        $staff = $this->isStaff($request);

        // The galley buttons print the fee from the template's own copy of
        // the journal, which is not the object the request holds (measured:
        // a second instance), so it is zeroed here as well.
        $journal = $templateMgr->getTemplateVars('currentJournal');
        if ($journal && !$staff && (string) $request->getRequestedPage() !== 'payment') {
            self::zeroPayments($journal);
        }

        // The journal's footer HTML, without its links to closed pages.
        $footer = $templateMgr->getTemplateVars('pageFooter');
        if (!$staff && is_string($footer) && $footer !== '') {
            $templateMgr->assign('pageFooter', ArchiveModeRoutes::withoutClosedLinks($footer, $request->getContext()?->getPath(), self::hosts($request)));
        }

        [$kind, $url] = $this->destination($request);
        $bar = $this->barHtml($kind, $url);
        $css = $this->css(!$staff);

        // Named, so it neither replaces nor is replaced by another plugin's
        // closure (Smarty files every unnamed closure as "closure").
        $templateMgr->registerFilter('output', function (string $html) use ($bar, $css, $isViewer): string {
            return self::insertBar($html, $bar, $css, $isViewer);
        }, 'archiveModeBar');
        $this->filterAdded = true;

        return Hook::CONTINUE;
    }

    /**
     * Put the CSS at the end of <head> and the bar straight after <body>.
     * Anything without a <body> (a fragment) passes through untouched, and a
     * page is only ever given one bar.
     */
    public static function insertBar(string $html, string $bar, string $css, bool $isViewer): string
    {
        if (str_contains($html, 'class="archive-mode-bar"') || !preg_match('/<body\b[^>]*>/i', $html, $m, PREG_OFFSET_CAPTURE)) {
            return $html;
        }
        $bodyTag = $m[0][0];
        $at = $m[0][1];
        $newBodyTag = $bodyTag;
        if ($isViewer) {
            $newBodyTag = preg_match('/\bclass="/', $bodyTag)
                ? preg_replace('/\bclass="/', 'class="archive-mode-viewer ', $bodyTag, 1)
                : preg_replace('/^<body\b/i', '<body class="archive-mode-viewer"', $bodyTag, 1);
        }
        $html = substr($html, 0, $at) . $newBodyTag . $bar . substr($html, $at + strlen($bodyTag));

        $head = stripos($html, '</head>');
        $style = '<style id="archive-mode-css">' . $css . '</style>';
        return $head === false ? $style . $html : substr($html, 0, $head) . $style . substr($html, $head);
    }

    private function barHtml(string $kind, string $url): string
    {
        $site = ArchiveModeRoutes::siteName(self::movedTo());
        return '<div class="archive-mode-bar" role="region" aria-label="' . htmlspecialchars(__('plugins.generic.archiveMode.bar.label'), ENT_QUOTES, 'UTF-8') . '">'
            . '<div class="archive-mode-bar__inner">'
            . '<p class="archive-mode-bar__lead">' . htmlspecialchars(__('plugins.generic.archiveMode.bar.lead'), ENT_QUOTES, 'UTF-8') . '</p>'
            . '<p class="archive-mode-bar__text">' . htmlspecialchars(__('plugins.generic.archiveMode.bar.text', ['site' => $site]), ENT_QUOTES, 'UTF-8') . '</p>'
            . '<a class="archive-mode-bar__link" href="' . htmlspecialchars($url, ENT_QUOTES, 'UTF-8') . '">'
            . htmlspecialchars($this->linkLabel($kind), ENT_QUOTES, 'UTF-8') . '</a>'
            . '</div></div>';
    }

    /**
     * The bar's styles, in the site's own font. White on deep slate is about
     * 12:1. Not sticky: a fixed band would eat a phone's screen on every page.
     */
    private function css(bool $hideUserMenu): string
    {
        $css = '
.archive-mode-bar { background: #1f3540; color: #fff; font-family: inherit; border-bottom: 4px solid #e9b949; }
.archive-mode-bar__inner { max-width: 1160px; margin: 0 auto; padding: 14px 16px; display: flex; flex-wrap: wrap; align-items: center; gap: 6px 24px; }
.archive-mode-bar p { margin: 0; line-height: 1.45; font-size: 15px; color: #fff; }
.archive-mode-bar__lead { flex: 1 1 100%; font-weight: 700; font-size: 17px !important; }
.archive-mode-bar__text { flex: 1 1 420px; }
.archive-mode-bar__link { flex: 0 0 auto; display: inline-block; padding: 9px 14px; border: 2px solid #fff; border-radius: 4px; color: #fff !important; font-weight: 700; font-size: 15px; line-height: 1.3; text-decoration: none; }
.archive-mode-bar__link:hover, .archive-mode-bar__link:focus { background: #fff; color: #1f3540 !important; text-decoration: none; }
.archive-mode-bar__link:focus-visible { outline: 3px solid #e9b949; outline-offset: 2px; }
@media (max-width: 480px) { .archive-mode-bar__link { display: block; width: 100%; text-align: center; box-sizing: border-box; } }
body.archive-mode-viewer { display: flex; flex-direction: column; height: 100vh; height: 100dvh; margin: 0; overflow: hidden; }
body.archive-mode-viewer > .archive-mode-bar, body.archive-mode-viewer > .header_view { flex: none; }
body.archive-mode-viewer > .galley_view { position: relative; inset: auto; flex: 1 1 auto; min-height: 0; }
body.archive-mode-viewer > .galley_view iframe { padding-top: 0; }
';
        if ($hideUserMenu) {
            $css .= '.pkp_navigation_user_wrapper { display: none !important; }';
        }
        return $css;
    }
}
