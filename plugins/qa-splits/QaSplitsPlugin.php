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
        Hook::add('Templates::Article::Details', $this->addReviewCta(...));

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
     * Show "Help review the archive" CTA on article pages for logged-in users.
     */
    public function addReviewCta(string $hookName, array $params): bool
    {
        $request = Application::get()->getRequest();
        $user = $request->getUser();
        if (!$user) {
            return Hook::CONTINUE;
        }

        $context = $request->getContext();
        $contextId = $context ? $context->getId() : 0;

        $total = DB::table('submissions')
            ->where('context_id', $contextId)
            ->where('status', 3) // STATUS_PUBLISHED
            ->count();

        if ($total === 0) {
            return Hook::CONTINUE;
        }

        $reviewed = DB::table('qa_split_reviews as r1')
            ->join('submissions as s', 's.submission_id', '=', 'r1.submission_id')
            ->where('s.context_id', $contextId)
            ->whereRaw('r1.review_id = (SELECT MAX(r2.review_id) FROM qa_split_reviews r2 WHERE r2.submission_id = r1.submission_id)')
            ->where('r1.decision', 'approved')
            ->count();

        $remaining = $total - $reviewed;
        if ($remaining <= 0) {
            return Hook::CONTINUE;
        }

        $qaUrl = $request->getBaseUrl() . '/index.php/'
            . ($context ? $context->getPath() : '') . '/qa-splits';

        $output = &$params[2];
        $output .= '<section class="item qa-review-cta">'
            . '<h2 class="label">Help Review the Archive</h2>'
            . '<div class="value">'
            . '<p style="margin:0 0 8px;font-size:14px;line-height:1.5;color:#555;">'
            . "We've approved <strong>{$reviewed}</strong> of <strong>{$total}</strong> digitised articles so far. "
            . "Would you help by reviewing a few articles from the archive and flagging anything that needs fixing?"
            . '</p>'
            . '<a href="' . htmlspecialchars($qaUrl) . '" '
            . 'style="display:inline-block;padding:8px 14px;background:#1a1a1e;color:#fff;'
            . 'border-radius:4px;text-decoration:none;font-size:13px;font-weight:600;">'
            . 'Start reviewing</a>'
            . '</div></section>';

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
        $currentUsername = $request->getUser()->getUsername();

        header('Content-Type: text/html; charset=utf-8');
        echo <<<'HTMLSTART'
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>QA Splits</title>
HTMLSTART;
        echo '<link rel="stylesheet" href="' . $pluginUrl . '/css/pdf_viewer.css">';
        echo '<link rel="stylesheet" href="' . $pluginUrl . '/css/qa-review.css">';
        echo <<<'HTMLBODY'
</head>
<body>
    <div class="qa-layout" x-data="qaApp">

        <!-- Sidebar -->
        <div class="qa-drawer">
            <div class="qa-drawer-brand">
                <div class="qa-drawer-logo">QA Splits</div>
                <div class="qa-drawer-strapline">Check PDF splits, HTML accuracy, start/end bleed, end-matter classification
                    <a href="#" class="qa-drawer-more" @click.prevent="showChecklist = !showChecklist">What to check &rsaquo;</a>
                </div>
                <div class="qa-drawer-checklist" x-show="showChecklist" x-cloak>
                    <ul>
                        <li>PDF split at the right page</li>
                        <li>HTML text matches PDF content</li>
                        <li>No start bleed from previous article</li>
                        <li>No end bleed into next article</li>
                        <li>References, notes, bios correctly classified</li>
                        <li>Excluded content (ads, mastheads) not present</li>
                    </ul>
                </div>
            </div>

            <div class="qa-drawer-header">
                <input type="text" class="qa-drawer-search" placeholder="Search title, author, keyword..."
                    x-model="searchQuery" @input="refilter()">
                <button class="qa-search-clear" x-show="searchQuery" @click="searchQuery=''; refilter()">&times;</button>
            </div>

            <div class="qa-drawer-filter-row">
                <select class="qa-drawer-select" x-model="issueFilter" @change="refilter()">
                    <option value="">All issues</option>
                    <template x-for="iss in allIssues" :key="iss.key">
                        <option :value="iss.key" x-text="'Issue ' + iss.key + ' (' + iss.count + ')'"></option>
                    </template>
                </select>
            </div>

            <div class="qa-drawer-pills">
                <template x-for="p in statusPills" :key="p.key">
                    <button class="qa-drawer-pill qa-drawer-pill-status" :class="{ active: isStatusActive(p.key) }"
                        @click="toggleStatus(p.key)" x-text="p.label + ' (' + p.count + ')'"></button>
                </template>
            </div>
            <div class="qa-drawer-pills" x-show="sectionPills.length > 0">
                <template x-for="p in sectionPills" :key="p.key">
                    <button class="qa-drawer-pill qa-drawer-pill-section" :class="{ active: isSectionActive(p.key) }"
                        @click="toggleSection(p.key)" x-text="p.label + ' (' + p.count + ')'"></button>
                </template>
            </div>
            <div class="qa-drawer-pills" x-show="reviewerPills.length > 0">
                <template x-for="p in reviewerPills" :key="p.key">
                    <button class="qa-drawer-pill qa-drawer-pill-reviewer" :class="{ active: isReviewerActive(p.key) }"
                        @click="toggleReviewer(p.key)" x-text="p.label + ' (' + p.count + ')'"></button>
                </template>
            </div>
            <div class="qa-drawer-clear-row" x-show="hasFilters">
                <button class="qa-drawer-clear" @click="clearFilters()">Clear all filters</button>
            </div>

            <div class="qa-drawer-list">
                <template x-for="item in workingSetArticles" :key="item.artIdx">
                    <div class="qa-drawer-item" :class="{ active: item.active }" @click="selectArticle(item.si)">
                        <span class="qa-drawer-item-num" x-text="item.num"></span>
                        <span :class="item.statusCls" x-text="item.icon"></span>
                        <span class="qa-drawer-item-title" x-text="item.title"></span>
                    </div>
                </template>
                <div x-show="workingSet.length === 0" style="padding:20px;text-align:center;color:rgba(255,255,255,0.35)">No articles match</div>
            </div>

            <div class="qa-drawer-nav">
                <button class="qa-btn qa-btn-nav" :disabled="!canGoPrev" @click="navigate(-1)">&lsaquo; Previous</button>
                <button class="qa-btn qa-btn-nav" :disabled="!canGoNext" @click="navigate(1)">Next &rsaquo;</button>
                <button class="qa-btn qa-btn-nav qa-btn-random" @click="goToRandom()">&#127922; Random</button>
            </div>

            <div class="qa-drawer-footer">
                <span x-text="positionDisplay"></span>
                <span class="qa-drawer-random-label" x-show="setFilter && setFilter.type === 'random'">Random batch</span>
            </div>
        </div>

        <!-- Top bar -->
        <div class="qa-top">
            <div class="qa-row-1">
                <span class="qa-title" x-text="titleDisplay"></span>
                <span class="qa-authors" x-text="authorsDisplay"></span>
            </div>
            <div class="qa-row-2">
                <span :class="statusClass" @click="showFixReason()" :title="article?.comment || ''"
                    x-text="statusLabel" :style="article?.status === 'needs_fix' ? 'cursor:pointer' : ''"></span>
                <div class="qa-progress" x-text="progressDisplay" @click="openDashboard()"></div>
                <span class="qa-row-spacer"></span>
                <template x-if="!showRejectForm">
                    <div style="display:flex;gap:8px;">
                        <button class="qa-btn qa-btn-reject" @click="requestFix()" :disabled="submitting"
                            title="Request Fix (R)">Request Fix</button>
                        <button class="qa-btn qa-btn-approve" @click="approve()" :disabled="submitting"
                            title="Approve (A)">Approve</button>
                    </div>
                </template>
            </div>
            <div class="qa-row-reject" x-show="showRejectForm" x-cloak>
                <textarea class="qa-textarea" x-model="rejectComment" x-ref="rejectTextarea"
                    placeholder="What needs fixing? Describe the issue..." rows="3"
                    @keydown.ctrl.enter="submitFix()" @keydown.meta.enter="submitFix()" @keydown.escape="cancelFix()"></textarea>
                <button class="qa-btn qa-btn-reject-submit" @click="submitFix()" :disabled="submitting || !rejectComment.trim()"
                    title="Ctrl+Enter to submit">Request Fix</button>
                <button class="qa-btn qa-btn-nav" @click="cancelFix()">Cancel</button>
            </div>
        </div>

        <!-- PDF viewer -->
        <div class="qa-left">
            <div class="qa-pdf-toolbar">
                <span id="pdf-page-info" x-text="pdfPageInfo">Loading...</span>
                <div class="qa-pdf-search" :class="{ open: pdfSearchOpen }">
                    <button class="qa-pdf-search-toggle" @click="togglePdfSearch()" title="Search PDF (Ctrl+F)">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
                    </button>
                    <template x-if="pdfSearchOpen">
                        <div class="qa-pdf-search-bar">
                            <input id="pdf-search-input" x-ref="pdfSearchInput" type="text" placeholder="Find in PDF..."
                                @input.debounce.300ms="pdfSearch($el.value)">
                            <span class="qa-pdf-search-info" x-text="pdfSearchInfo"></span>
                            <button @click="pdfSearchPrev()" :disabled="pdfSearchInfo === 'No matches'" title="Previous (Shift+Enter)">&lsaquo;</button>
                            <button @click="pdfSearchNext()" :disabled="pdfSearchInfo === 'No matches'" title="Next (Enter)">&rsaquo;</button>
                            <button @click="clearPdfSearch(); $refs.pdfSearchInput.value = ''; $refs.pdfSearchInput.focus()" title="Clear">&times;</button>
                        </div>
                    </template>
                </div>
            </div>
            <div class="qa-pdf-container-wrap"><div id="pdf-container" class="qa-pdf-container"><div id="pdf-viewer" class="pdfViewer"></div></div></div>
        </div>

        <!-- HTML galley + end-matter -->
        <div class="qa-right">
            <div class="qa-article-meta" x-show="article">
                <div class="qa-meta-issue" x-text="(article?.issue_title || '') + ' ' + (article?.volume || '') + '.' + (article?.number || '') + ': ' + (article?.year || '')"></div>
                <div class="qa-meta-id" x-text="'Article #' + (article?.submission_id || '')"></div>
                <h1 class="qa-meta-title" x-text="article?.title"></h1>
                <h2 class="qa-meta-subtitle" x-show="article?.subtitle" x-text="article?.subtitle"></h2>
                <div class="qa-meta-authors" x-show="article?.authors?.length">
                    <template x-for="(author, i) in (article?.authors || [])" :key="i">
                        <span class="qa-meta-author" x-text="author"></span>
                    </template>
                </div>
                <div class="qa-meta-doi" x-show="article?.doi">
                    DOI: <a :href="'https://doi.org/' + (article?.doi || '')" target="_blank"
                        x-text="'https://doi.org/' + (article?.doi || '')"></a>
                </div>
                <div class="qa-meta-pages" x-show="article?.pages">
                    <span>Pages: </span><span x-text="article?.pages"></span>
                </div>
                <div class="qa-meta-keywords" x-show="article?.keywords?.length">
                    <span class="qa-meta-kw-label">Keywords: </span>
                    <span x-text="(article?.keywords || []).join(', ')"></span>
                </div>
                <div class="qa-meta-abstract" x-show="article?.abstract">
                    <h3>Abstract</h3>
                    <div x-html="article?.abstract || ''"></div>
                </div>
            </div>
            <div class="qa-html-content">
                <div x-show="htmlLoading" class="qa-loading">Loading HTML...</div>
                <div x-show="!htmlLoading" x-html="htmlContent"></div>
            </div>
            <div class="qa-endmatter" x-show="hasClassification">
                <template x-for="group in classificationGroups" :key="group.label">
                    <div class="qa-endmatter-group">
                        <div class="qa-endmatter-group-header">
                            <span class="qa-pill" :class="group.cls" x-text="group.label"></span>
                        </div>
                        <template x-for="(item, i) in group.items" :key="i">
                            <div class="qa-endmatter-item">
                                <span class="qa-endmatter-num" x-text="(i + 1) + '.'"></span>
                                <div class="qa-endmatter-text-wrap">
                                    <span class="qa-endmatter-text" x-text="item.text"></span>
                                    <template x-if="item.doi">
                                        <a class="qa-endmatter-doi" :href="'https://doi.org/' + item.doi" target="_blank" x-text="'doi:' + item.doi"></a>
                                    </template>
                                </div>
                            </div>
                        </template>
                    </div>
                </template>
            </div>
        </div>

        <!-- Help overlay -->
        <div class="qa-help-overlay" x-show="showHelp" x-cloak @click="showHelp = false"
            role="dialog" aria-modal="true" aria-label="Keyboard shortcuts">
            <div class="qa-help-box" @click.stop>
                <h3>Keyboard Shortcuts</h3>
                <table>
                    <tr><td><kbd>&larr;</kbd> / <kbd>&rarr;</kbd></td><td>Previous / Next article</td></tr>
                    <tr><td><kbd>A</kbd></td><td>Approve article</td></tr>
                    <tr><td><kbd>R</kbd></td><td>Request Fix (opens form)</td></tr>
                    <tr><td><kbd>Ctrl+Enter</kbd></td><td>Submit fix request</td></tr>
                    <tr><td><kbd>Esc</kbd></td><td>Cancel / close</td></tr>
                    <tr><td><kbd>?</kbd></td><td>Toggle this help</td></tr>
                </table>
                <p>Press any key to close</p>
            </div>
        </div>
    </div>

HTMLBODY;
        echo '<script>window.QA_CONFIG = { apiBase: "' . $apiBase . '", pluginUrl: "' . $pluginUrl . '", csrfToken: "' . $csrfToken . '", username: "' . htmlspecialchars($currentUsername, ENT_QUOTES) . '" };</script>';
        echo '<script type="module" src="' . $pluginUrl . '/js/qa-app.mjs"></script>';
        echo '</body></html>';
        exit;
    }
}
