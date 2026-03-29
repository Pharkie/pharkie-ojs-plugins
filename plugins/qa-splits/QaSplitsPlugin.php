<?php

/**
 * QA Splits Plugin
 *
 * Full-screen visual QA tool for reviewing backfill article splits.
 * Provides a three-pane interface: PDF viewer, HTML galley + end-matter
 * classification, and review controls with navigation.
 *
 * Deploy to: plugins/generic/qaSplits/ in OJS installation.
 * Requires OJS 3.5+.
 *
 * Configuration in config.inc.php:
 *   [qa-splits]
 *   backfill_output_dir = "/data/sample-issues"
 */

namespace APP\plugins\generic\qaSplits;

use APP\core\Application;
use Illuminate\Support\Facades\DB;
use PKP\config\Config;
use PKP\plugins\GenericPlugin;
use PKP\plugins\Hook;
use PKP\security\Role;
use APP\plugins\generic\qaSplits\QaSplitsMigration;

class QaSplitsPlugin extends GenericPlugin
{
    public function register($category, $path, $mainContextId = null)
    {
        $success = parent::register($category, $path, $mainContextId);

        if (!$success || !$this->getEnabled()) {
            return $success;
        }

        Hook::add('LoadHandler', $this->handlePageRequest(...));
        Hook::add('TemplateManager::display', $this->addDashboardLink(...));

        return $success;
    }

    public function getInstallMigration()
    {
        return new QaSplitsMigration();
    }

    public function getDisplayName()
    {
        return __('plugins.generic.qaSplits.displayName');
    }

    public function getDescription()
    {
        return __('plugins.generic.qaSplits.description');
    }

    /**
     * Add "Open QA Splits" action in the plugin's admin listing.
     */
    public function getActions($request, $actionArgs)
    {
        $actions = parent::getActions($request, $actionArgs);
        if (!$this->getEnabled()) return $actions;

        $qaUrl = $request->getBaseUrl() . '/index.php/'
            . ($request->getContext() ? $request->getContext()->getPath() : '')
            . '/qa-splits';

        array_unshift($actions,
            new \PKP\linkAction\LinkAction(
                'openQaSplits',
                new \PKP\linkAction\request\OpenWindowAction($qaUrl),
                'Open QA Splits'
            ),
        );

        return $actions;
    }

    /**
     * Inject a link to QA Splits on the OJS dashboard/submissions page.
     */
    public function addDashboardLink(string $hookName, array $args): bool
    {
        $templateMgr = $args[0];
        $template = $args[1] ?? '';

        // Only add on the submissions/dashboard pages
        if (!str_contains($template, 'dashboard') && !str_contains($template, 'submissions')) {
            return Hook::CONTINUE;
        }

        $request = Application::get()->getRequest();
        $user = $request->getUser();
        if (!$user) {
            return Hook::CONTINUE;
        }

        $qaUrl = $request->getBaseUrl() . '/index.php/'
            . ($request->getContext() ? $request->getContext()->getPath() : '')
            . '/qa-splits';

        $templateMgr->addHeader('qa-splits-link', '
            <style>
            .qa-splits-dashboard-link {
                position: fixed; bottom: 20px; right: 20px; z-index: 1000;
                background: #1a1a1e; color: #faf8f4; padding: 10px 18px;
                border-radius: 6px; font-size: 13px; font-weight: 600;
                text-decoration: none; box-shadow: 0 4px 12px rgba(0,0,0,0.2);
                font-family: -apple-system, BlinkMacSystemFont, sans-serif;
                transition: background 0.15s;
            }
            .qa-splits-dashboard-link:hover { background: #2a2a30; color: #faf8f4; }
            </style>
            <a href="' . htmlspecialchars($qaUrl) . '" class="qa-splits-dashboard-link" target="_blank">
                QA Splits
            </a>
        ');

        return Hook::CONTINUE;
    }

    /**
     * Intercept requests to /qa-splits and serve the full-screen QA interface.
     */
    public function handlePageRequest(string $hookName, array $args): bool
    {
        $page = &$args[0];

        if ($page !== 'qa-splits') {
            return Hook::CONTINUE;
        }

        // Require any authenticated user
        $request = Application::get()->getRequest();
        $user = $request->getUser();
        if (!$user) {
            $request->redirect(null, 'login');
            return true;
        }

        $this->serveQaPage($request);
        return true;
    }

    /**
     * Check if user has Journal Manager or Site Admin role.
     */
    private function userIsManager($user, $request): bool
    {
        $context = $request->getContext();
        $contextId = $context ? $context->getId() : 0;

        return DB::table('user_user_groups')
            ->join('user_groups', 'user_user_groups.user_group_id', '=', 'user_groups.user_group_id')
            ->where('user_user_groups.user_id', $user->getId())
            ->where(function ($q) use ($contextId) {
                $q->where('user_groups.role_id', Role::ROLE_ID_MANAGER)
                  ->where('user_groups.context_id', $contextId);
            })
            ->orWhere(function ($q) use ($user) {
                $q->where('user_user_groups.user_id', $user->getId())
                  ->where('user_groups.role_id', Role::ROLE_ID_SITE_ADMIN);
            })
            ->exists();
    }

    /**
     * Serve the standalone QA review page (no OJS chrome).
     */
    private function serveQaPage($request): void
    {
        $context = $request->getContext();
        $baseUrl = $request->getBaseUrl();
        $contextPath = $context ? $context->getPath() : '';
        $apiBase = $baseUrl . '/' . $contextPath . '/api/v1/qa-splits';
        $pluginUrl = $baseUrl . '/plugins/generic/qaSplits';

        $csrfToken = $request->getSession()->token();

        header('Content-Type: text/html; charset=utf-8');
        echo <<<HTML
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>QA Splits</title>
    <link rel="stylesheet" href="{$pluginUrl}/css/qa-review.css">
</head>
<body>
    <div class="qa-layout">
        <!-- Top bar: two rows -->
        <div class="qa-top">
            <div class="qa-row-1">
                <button id="btn-last-seen" class="qa-back-btn" title="Return to where you were before Random/Problem jump">&larr; Back</button>
                <span class="qa-app-name">QA Splits</span>
                <span class="qa-title" id="qa-title">Loading...</span>
                <span class="qa-authors" id="qa-authors"></span>
                <span id="qa-section" style="display:none"></span>
                <span id="qa-issue" style="display:none"></span>
                <span id="qa-pages" style="display:none"></span>
            </div>
            <div class="qa-row-2">
                <span class="qa-badge" id="qa-status"></span>
                <div class="qa-progress" id="qa-progress"></div>
                <span class="qa-row-spacer"></span>
                <button id="btn-prev" class="qa-btn qa-btn-nav" title="Previous article (Left arrow)">&lsaquo; Previous</button>
                <button id="btn-next" class="qa-btn qa-btn-nav" title="Next article (Right arrow)">Next &rsaquo;</button>
                <button id="btn-random" class="qa-btn qa-btn-nav" title="Jump to a random unreviewed article">Random</button>
                <button id="btn-problem" class="qa-btn qa-btn-nav" title="Jump to next article needing fixes">Next Fix</button>
                <div class="qa-btn-wrap">
                    <button id="btn-approve" class="qa-btn qa-btn-approve" title="Approve (A)">Approve</button>
                    <span id="feedback-approve" class="qa-feedback qa-feedback-approve"></span>
                </div>
                <div class="qa-btn-wrap">
                    <button id="btn-reject" class="qa-btn qa-btn-reject" title="Request Fix (R)">Request Fix</button>
                    <span id="feedback-reject" class="qa-feedback qa-feedback-reject"></span>
                </div>
            </div>
            <div class="qa-row-reject" id="qa-row-reject" style="display:none">
                <textarea id="reject-comment" class="qa-textarea" placeholder="What needs fixing? Describe the issue..." rows="3"></textarea>
                <button id="btn-submit-reject" class="qa-btn qa-btn-reject-submit" title="Submit fix request (Ctrl+Enter)">Request Fix</button>
                <span class="qa-reject-hint">Ctrl+Enter to submit, Esc to cancel</span>
            </div>
        </div>

        <!-- Drawer: collapsible filter/article list panel -->
        <div class="qa-drawer-tab" id="qa-drawer-tab" title="Open article list">
            <span id="qa-drawer-tab-text">All</span>
        </div>
        <div class="qa-drawer" id="qa-drawer" style="display:none">
            <div class="qa-drawer-header">
                <input type="text" id="qa-drawer-search" class="qa-drawer-search" placeholder="Search title, author, keyword...">
                <button id="qa-drawer-close" class="qa-drawer-close">&times;</button>
            </div>
            <div class="qa-drawer-filters" id="qa-drawer-filters"></div>
            <div class="qa-drawer-list" id="qa-drawer-list"></div>
            <div class="qa-drawer-footer" id="qa-drawer-footer"></div>
        </div>

        <!-- Left pane: PDF viewer -->
        <div class="qa-left">
            <div class="qa-pdf-toolbar">
                <span id="pdf-page-info">Page - of -</span>
            </div>
            <div id="pdf-container" class="qa-pdf-container"></div>
        </div>

        <!-- Right pane: HTML galley + end-matter -->
        <div class="qa-right">
            <div id="html-content" class="qa-html-content">
                <p>Loading HTML galley...</p>
            </div>
            <div id="endmatter-section" class="qa-endmatter">
                <h3 class="qa-endmatter-heading">End-Matter Classification</h3>
                <div id="endmatter-items"></div>
            </div>
        </div>
    </div>

    <script>
        window.QA_CONFIG = {
            apiBase: '{$apiBase}',
            pluginUrl: '{$pluginUrl}',
            csrfToken: '{$csrfToken}'
        };
    </script>
    <script src="{$pluginUrl}/js/pdf.min.js"></script>
    <script src="{$pluginUrl}/js/qa-review.js"></script>
</body>
</html>
HTML;
        exit;
    }
}
