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
     * Intercept requests to /qa-splits and serve the full-screen QA interface.
     */
    public function handlePageRequest(string $hookName, array $args): bool
    {
        $page = &$args[0];

        if ($page !== 'qa-splits') {
            return Hook::CONTINUE;
        }

        // Require authenticated user with Manager or Site Admin role
        $request = Application::get()->getRequest();
        $user = $request->getUser();
        if (!$user) {
            $request->redirect(null, 'login');
            return true;
        }

        if (!$this->userIsManager($user, $request)) {
            header('HTTP/1.1 403 Forbidden');
            echo 'Access denied. Journal Manager or Site Admin role required.';
            exit;
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
        <!-- Top pane: metadata + controls -->
        <div class="qa-top">
            <div class="qa-meta">
                <div class="qa-meta-primary">
                    <span class="qa-title" id="qa-title">Loading...</span>
                    <span class="qa-badge" id="qa-status"></span>
                </div>
                <div class="qa-meta-secondary">
                    <span id="qa-authors"></span>
                    <span id="qa-section"></span>
                    <span id="qa-issue"></span>
                    <span id="qa-pages"></span>
                </div>
            </div>
            <div class="qa-controls">
                <div class="qa-review-actions">
                    <button id="btn-approve" class="qa-btn qa-btn-approve" title="Approve (A)">Approved</button>
                    <button id="btn-reject" class="qa-btn qa-btn-reject" title="Reject (R)">Reject</button>
                    <input type="text" id="reject-comment" class="qa-input" placeholder="Rejection comment..." style="display:none">
                    <button id="btn-submit-reject" class="qa-btn qa-btn-reject-submit" style="display:none">Submit Rejection</button>
                </div>
                <div class="qa-nav">
                    <button id="btn-last-seen" class="qa-btn qa-btn-nav" title="Back to last reviewed">Last Seen</button>
                    <button id="btn-prev" class="qa-btn qa-btn-nav" title="Previous (Left arrow)">Prev</button>
                    <button id="btn-next" class="qa-btn qa-btn-nav" title="Next (Right arrow)">Next</button>
                    <button id="btn-random" class="qa-btn qa-btn-nav" title="Random unreviewed">Random</button>
                    <button id="btn-problem" class="qa-btn qa-btn-nav qa-btn-problem" title="Jump to next problem case (rejected/invalidated)">Next Problem</button>
                </div>
                <div class="qa-progress" id="qa-progress"></div>
            </div>
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
