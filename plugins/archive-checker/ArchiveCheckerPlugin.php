<?php

/**
 * Archive Checker Plugin
 *
 * Full-screen visual QA tool for reviewing backfill article splits.
 * Provides a three-pane interface: PDF viewer, HTML galley + end-matter
 * classification, and review controls with navigation.
 *
 * Deploy to: plugins/generic/archiveChecker/ in OJS installation.
 * Requires OJS 3.5+.
 *
 * Configuration in config.inc.php:
 *   [archive-checker]
 *   backfill_output_dir = "/data/sample-issues"
 */

namespace APP\plugins\generic\archiveChecker;

use APP\core\Application;
use Illuminate\Support\Facades\DB;
use PKP\config\Config;
use PKP\plugins\GenericPlugin;
use PKP\plugins\Hook;
use PKP\security\Role;
use APP\plugins\generic\archiveChecker\ArchiveCheckerMigration;

class ArchiveCheckerPlugin extends GenericPlugin
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
        return new ArchiveCheckerMigration();
    }

    public function getDisplayName()
    {
        return __('plugins.generic.archiveChecker.displayName');
    }

    public function getDescription()
    {
        return __('plugins.generic.archiveChecker.description');
    }

    /**
     * Add "Open Archive Checker" action in the plugin's admin listing.
     */
    public function getActions($request, $actionArgs)
    {
        $actions = parent::getActions($request, $actionArgs);
        if (!$this->getEnabled()) return $actions;

        $qaUrl = $request->getBaseUrl() . '/index.php/'
            . ($request->getContext() ? $request->getContext()->getPath() : '')
            . '/archive-checker';

        array_unshift($actions,
            new \PKP\linkAction\LinkAction(
                'openArchiveChecker',
                new \PKP\linkAction\request\OpenWindowAction($qaUrl),
                'Open Archive Checker'
            ),
        );

        return $actions;
    }

    /**
     * Inject a link to Archive Checker on the OJS dashboard/submissions page.
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
            . '/archive-checker';

        $templateMgr->addHeader('archive-checker-link', '
            <style>
            .ac-dashboard-link {
                position: fixed; bottom: 20px; right: 20px; z-index: 1000;
                background: #1a1a1e; color: #faf8f4; padding: 10px 18px;
                border-radius: 6px; font-size: 13px; font-weight: 600;
                text-decoration: none; box-shadow: 0 4px 12px rgba(0,0,0,0.2);
                font-family: -apple-system, BlinkMacSystemFont, sans-serif;
                transition: background 0.15s;
            }
            .ac-dashboard-link:hover { background: #2a2a30; color: #faf8f4; }
            </style>
            <a href="' . htmlspecialchars($qaUrl) . '" class="ac-dashboard-link" target="_blank">
                Archive Checker
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

        $reviewed = DB::table('archive_checker_reviews as r1')
            ->join('submissions as s', 's.submission_id', '=', 'r1.submission_id')
            ->where('s.context_id', $contextId)
            ->whereRaw('r1.review_id = (SELECT MAX(r2.review_id) FROM archive_checker_reviews r2 WHERE r2.submission_id = r1.submission_id)')
            ->where('r1.decision', 'approved')
            ->count();

        $remaining = $total - $reviewed;
        if ($remaining <= 0) {
            return Hook::CONTINUE;
        }

        $qaUrl = $request->getBaseUrl() . '/index.php/'
            . ($context ? $context->getPath() : '') . '/archive-checker?mode=random';

        $remaining = $total - $reviewed;

        $output = &$params[2];
        $output .= '<section class="item ac-review-cta">'
            . '<div style="padding:14px 16px;background:#f8f5f0;border:1px solid #e0d8cc;border-radius:6px;">'
            . '<div style="margin-bottom:8px;">'
            . '<span style="display:inline-block;padding:3px 10px;background:#b8860b;color:#fff;'
            . 'border-radius:12px;font-size:12px;font-weight:600;letter-spacing:0.3px;">Help Check the Archive</span>'
            . '</div>'
            . '<p style="margin:0 0 10px;font-size:14px;line-height:1.5;color:#555;">'
            . "<strong>{$reviewed}</strong> down, <strong>{$remaining}</strong> to go &mdash; help check the journal archive. "
            . "Take a moment to review a few articles and flag anything that needs fixing? "
            . "And uncover hidden gems from over the decades as you go."
            . '</p>'
            . '<a href="' . htmlspecialchars($qaUrl) . '" '
            . 'style="display:inline-block;padding:7px 14px;background:#b8860b;color:#fff;'
            . 'border-radius:4px;text-decoration:none;font-size:13px;font-weight:600;">'
            . 'Review articles &rarr;</a>'
            . '</div></section>';

        return Hook::CONTINUE;
    }

    /**
     * Intercept requests to /archive-checker and serve the full-screen QA interface.
     */
    public function handlePageRequest(string $hookName, array $args): bool
    {
        $page = &$args[0];

        if ($page !== 'archive-checker') {
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
        $apiBase = $baseUrl . '/' . $contextPath . '/api/v1/archive-checker';
        $pluginUrl = $baseUrl . '/plugins/generic/archiveChecker';

        $csrfToken = $request->getSession()->token();
        $currentUsername = $request->getUser()->getUsername();

        header('Content-Type: text/html; charset=utf-8');
        echo <<<'HTMLSTART'
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Archive Checker</title>
HTMLSTART;
        echo '<link rel="stylesheet" href="' . $pluginUrl . '/css/pdf_viewer.css">';
        echo '<link rel="stylesheet" href="' . $pluginUrl . '/css/archive-checker.css">';
        echo <<<'HTMLBODY'
</head>
<body>
    <div class="ac-layout" x-data="acApp">

        <!-- Sidebar -->
        <div class="ac-drawer">
            <div class="ac-drawer-brand">
                <div class="ac-drawer-logo">Archive Checker</div>
                <div class="ac-drawer-strapline">Check PDF splits, HTML accuracy, start/end bleed, end-matter classification
                    <a href="#" class="ac-drawer-more" @click.prevent="showChecklist = !showChecklist">What to check &rsaquo;</a>
                </div>
                <div class="ac-drawer-checklist" x-show="showChecklist" x-cloak>
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

            <div class="ac-drawer-header">
                <input type="text" class="ac-drawer-search" placeholder="Search title, author, keyword..."
                    x-model="searchQuery" @input="refilter()">
                <button class="ac-search-clear" x-show="searchQuery" @click="searchQuery=''; refilter()">&times;</button>
            </div>

            <div class="ac-drawer-filter-row">
                <select class="ac-drawer-select" x-model="issueFilter" @change="refilter()">
                    <option value="">All issues</option>
                    <template x-for="iss in allIssues" :key="iss.key">
                        <option :value="iss.key" x-text="'Issue ' + iss.key + ' (' + iss.count + ')'"></option>
                    </template>
                </select>
            </div>

            <div class="ac-drawer-pills">
                <template x-for="p in statusPills" :key="p.key">
                    <button class="ac-drawer-pill ac-drawer-pill-status" :class="{ active: isStatusActive(p.key) }"
                        @click="toggleStatus(p.key)" x-text="p.label + ' (' + p.count + ')'"></button>
                </template>
            </div>
            <div class="ac-drawer-pills" x-show="sectionPills.length > 0">
                <template x-for="p in sectionPills" :key="p.key">
                    <button class="ac-drawer-pill ac-drawer-pill-section" :class="{ active: isSectionActive(p.key) }"
                        @click="toggleSection(p.key)" x-text="p.label + ' (' + p.count + ')'"></button>
                </template>
            </div>
            <div class="ac-drawer-pills" x-show="reviewerPills.length > 0">
                <template x-for="p in reviewerPills" :key="p.key">
                    <button class="ac-drawer-pill ac-drawer-pill-reviewer" :class="{ active: isReviewerActive(p.key) }"
                        @click="toggleReviewer(p.key)" x-text="p.label + ' (' + p.count + ')'"></button>
                </template>
            </div>
            <div class="ac-drawer-clear-row" x-show="hasFilters">
                <button class="ac-drawer-clear" @click="clearFilters()">Clear all filters</button>
            </div>

            <div class="ac-drawer-list">
                <template x-for="item in workingSetArticles" :key="item.artIdx">
                    <div class="ac-drawer-item" :class="{ active: item.active }" @click="selectArticle(item.si)">
                        <span class="ac-drawer-item-num" x-text="item.num"></span>
                        <span :class="item.statusCls" x-text="item.icon"></span>
                        <span class="ac-drawer-item-title" x-text="item.title"></span>
                    </div>
                </template>
                <div x-show="workingSet.length === 0" style="padding:20px;text-align:center;color:rgba(255,255,255,0.35)">No articles match</div>
            </div>

            <div class="ac-drawer-nav">
                <button class="ac-btn ac-btn-nav" :disabled="!canGoPrev" @click="navigate(-1)">&lsaquo; Previous</button>
                <button class="ac-btn ac-btn-nav" :disabled="!canGoNext" @click="navigate(1)">Next &rsaquo;</button>
                <button class="ac-btn ac-btn-nav ac-btn-random" @click="goToRandom()">&#127922; Random</button>
            </div>

            <div class="ac-drawer-footer">
                <span x-text="positionDisplay"></span>
                <span class="ac-drawer-random-label" x-show="setFilter && setFilter.type === 'random'">Random batch</span>
            </div>
        </div>

        <!-- Top bar -->
        <div class="ac-top">
            <div class="ac-row-1">
                <span class="ac-title" x-text="titleDisplay"></span>
                <span class="ac-authors" x-text="authorsDisplay"></span>
            </div>
            <div class="ac-row-2">
                <span :class="statusClass" @click="showFixReason()" :title="article?.comment || ''"
                    x-text="statusLabel" :style="article?.status === 'needs_fix' ? 'cursor:pointer' : ''"></span>
                <div class="ac-progress" x-text="progressDisplay" @click="openDashboard()"></div>
                <span class="ac-row-spacer"></span>
                <template x-if="!showRejectForm">
                    <div style="display:flex;gap:8px;">
                        <button class="ac-btn ac-btn-reject" @click="requestFix()" :disabled="submitting"
                            title="Request Fix (R)">Request Fix</button>
                        <button class="ac-btn ac-btn-approve" @click="approve()" :disabled="submitting"
                            title="Approve (A)">Approve</button>
                    </div>
                </template>
            </div>
            <div class="ac-row-reject" x-show="showRejectForm" x-cloak>
                <textarea class="ac-textarea" x-model="rejectComment" x-ref="rejectTextarea"
                    placeholder="What needs fixing? Describe the issue..." rows="3"
                    @keydown.ctrl.enter="submitFix()" @keydown.meta.enter="submitFix()" @keydown.escape="cancelFix()"></textarea>
                <button class="ac-btn ac-btn-reject-submit" @click="submitFix()" :disabled="submitting || !rejectComment.trim()"
                    title="Ctrl+Enter to submit">Request Fix</button>
                <button class="ac-btn ac-btn-nav" @click="cancelFix()">Cancel</button>
            </div>
        </div>

        <!-- PDF viewer -->
        <div class="ac-left">
            <div class="ac-pdf-toolbar">
                <span id="pdf-page-info" x-text="pdfPageInfo">Loading...</span>
                <div class="ac-pdf-search" :class="{ open: pdfSearchOpen }">
                    <button class="ac-pdf-search-toggle" @click="togglePdfSearch()" title="Search PDF (Ctrl+F)">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
                    </button>
                    <template x-if="pdfSearchOpen">
                        <div class="ac-pdf-search-bar">
                            <input id="pdf-search-input" x-ref="pdfSearchInput" type="text" placeholder="Find in PDF..."
                                @input.debounce.300ms="pdfSearch($el.value)">
                            <span class="ac-pdf-search-info" x-text="pdfSearchInfo"></span>
                            <button @click="pdfSearchPrev()" :disabled="pdfSearchInfo === 'No matches'" title="Previous (Shift+Enter)">&lsaquo;</button>
                            <button @click="pdfSearchNext()" :disabled="pdfSearchInfo === 'No matches'" title="Next (Enter)">&rsaquo;</button>
                            <button @click="clearPdfSearch(); $refs.pdfSearchInput.value = ''; $refs.pdfSearchInput.focus()" title="Clear">&times;</button>
                        </div>
                    </template>
                </div>
            </div>
            <div class="ac-pdf-container-wrap"><div id="pdf-container" class="ac-pdf-container"><div id="pdf-viewer" class="pdfViewer"></div></div></div>
        </div>

        <!-- HTML galley + end-matter -->
        <div class="ac-right">
            <div class="ac-article-meta" x-show="article">
                <div class="ac-meta-issue" x-text="(article?.issue_title || '') + ' ' + (article?.volume || '') + '.' + (article?.number || '') + ': ' + (article?.year || '')"></div>
                <div class="ac-meta-id" x-text="'Article #' + (article?.submission_id || '')"></div>
                <h1 class="ac-meta-title" x-text="article?.title"></h1>
                <h2 class="ac-meta-subtitle" x-show="article?.subtitle" x-text="article?.subtitle"></h2>
                <div class="ac-meta-authors" x-show="article?.authors?.length">
                    <template x-for="(author, i) in (article?.authors || [])" :key="i">
                        <span class="ac-meta-author" x-text="author"></span>
                    </template>
                </div>
                <div class="ac-meta-doi" x-show="article?.doi">
                    DOI: <a :href="'https://doi.org/' + (article?.doi || '')" target="_blank"
                        x-text="'https://doi.org/' + (article?.doi || '')"></a>
                </div>
                <div class="ac-meta-pages" x-show="article?.pages">
                    <span>Pages: </span><span x-text="article?.pages"></span>
                </div>
                <div class="ac-meta-keywords" x-show="article?.keywords?.length">
                    <span class="ac-meta-kw-label">Keywords: </span>
                    <span x-text="(article?.keywords || []).join(', ')"></span>
                </div>
                <div class="ac-meta-abstract" x-show="article?.abstract">
                    <h3>Abstract</h3>
                    <div x-html="article?.abstract || ''"></div>
                </div>
            </div>
            <div class="ac-html-content">
                <div x-show="htmlLoading" class="ac-loading">Loading HTML...</div>
                <div x-show="!htmlLoading" x-html="htmlContent"></div>
            </div>
            <div class="ac-endmatter" x-show="hasClassification">
                <template x-for="group in classificationGroups" :key="group.label">
                    <div class="ac-endmatter-group">
                        <div class="ac-endmatter-group-header">
                            <span class="ac-pill" :class="group.cls" x-text="group.label"></span>
                        </div>
                        <template x-for="(item, i) in group.items" :key="i">
                            <div class="ac-endmatter-item">
                                <span class="ac-endmatter-num" x-text="(i + 1) + '.'"></span>
                                <div class="ac-endmatter-text-wrap">
                                    <span class="ac-endmatter-text" x-text="item.text"></span>
                                    <template x-if="item.doi">
                                        <a class="ac-endmatter-doi" :href="'https://doi.org/' + item.doi" target="_blank" x-text="'doi:' + item.doi"></a>
                                    </template>
                                </div>
                            </div>
                        </template>
                    </div>
                </template>
            </div>
        </div>

        <!-- Help overlay -->
        <div class="ac-help-overlay" x-show="showHelp" x-cloak @click="showHelp = false"
            role="dialog" aria-modal="true" aria-label="Keyboard shortcuts">
            <div class="ac-help-box" @click.stop>
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
        echo '<script>window.AC_CONFIG = { apiBase: "' . $apiBase . '", pluginUrl: "' . $pluginUrl . '", csrfToken: "' . $csrfToken . '", username: "' . htmlspecialchars($currentUsername, ENT_QUOTES) . '" };</script>';
        echo '<script type="module" src="' . $pluginUrl . '/js/archive-checker-app.mjs"></script>';
        echo '</body></html>';
        exit;
    }
}
