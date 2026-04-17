<?php

/**
 * Similar Articles Plugin (cache-backed).
 *
 * Renders a "Related articles" sidebar on article pages by reading
 * pre-computed similarity rows from the similar_articles table. Similarity
 * is computed offline (see scripts/ojs/build_similar_articles.py) — this
 * plugin is render-only and does no analysis on the request path.
 *
 * Replaces the stock recommendBySimilarity plugin, which runs a corpus-wide
 * multi-JOIN query on every article view and collapses on thematically
 * narrow journals (see docs/ojs-issues-log.md #26).
 *
 * Deployed via docker-compose bind mount:
 *   ./plugins/similar-articles:/var/www/html/plugins/generic/similarArticles
 */

namespace APP\plugins\generic\similarArticles;

use APP\core\Application;
use APP\facades\Repo;
use APP\submission\Submission;
use Illuminate\Support\Facades\DB;
use PKP\plugins\GenericPlugin;
use PKP\plugins\Hook;

class SimilarArticlesPlugin extends GenericPlugin
{
    private const MAX_RESULTS = 5;

    public function register($category, $path, $mainContextId = null)
    {
        $success = parent::register($category, $path, $mainContextId);
        if (Application::isUnderMaintenance()) {
            return $success;
        }

        if ($success && $this->getEnabled($mainContextId)) {
            Hook::add('Templates::Article::Footer::PageFooter', $this->renderFooter(...));
        }
        return $success;
    }

    public function getInstallMigration()
    {
        return new SimilarArticlesMigration();
    }

    public function getDisplayName()
    {
        return __('plugins.generic.similarArticles.displayName');
    }

    public function getDescription()
    {
        return __('plugins.generic.similarArticles.description');
    }

    /**
     * Hook: append rendered sidebar to article page footer.
     *
     * Renders nothing on cache miss — preferred over a live-query fallback
     * because the whole point of this plugin is to eliminate live queries.
     * The nightly rebuild and Publication::publish hook keep the cache warm.
     */
    public function renderFooter(string $hookName, array $params): bool
    {
        $templateManager = $params[1];
        $output = &$params[2];

        $article = $templateManager->getTemplateVars('article');
        if (!$article) {
            return Hook::CONTINUE;
        }
        $submissionId = $article->getId();

        // Defensive: if the table is missing (migration didn't run or got
        // dropped), or the DB is throwing for any reason, degrade silently
        // to no-sidebar rather than letting the exception abort the whole
        // article-page render.
        try {
            $similarIds = DB::table('similar_articles')
                ->where('submission_id', $submissionId)
                ->orderBy('rank')
                ->limit(self::MAX_RESULTS)
                ->pluck('similar_id')
                ->toArray();
        } catch (\Throwable $e) {
            error_log('similarArticles: cache query failed for submission '
                . $submissionId . ': ' . $e->getMessage());
            return Hook::CONTINUE;
        }

        if (empty($similarIds)) {
            return Hook::CONTINUE;
        }

        // Resolve current journal context — Repo::submission()->getCollector()
        // requires filterByContextIds() to be set, otherwise it throws (as of
        // OJS 3.5, requiring callers to opt into cross-context behaviour).
        $request = Application::get()->getRequest();
        $context = $request->getContext();
        if (!$context) {
            return Hook::CONTINUE;
        }

        $submissions = Repo::submission()->getCollector()
            ->filterByContextIds([$context->getId()])
            ->filterByStatus([Submission::STATUS_PUBLISHED])
            ->filterBySubmissionIds($similarIds)
            ->getMany();

        $byId = [];
        foreach ($submissions as $s) {
            $byId[$s->getId()] = $s;
        }
        $ordered = [];
        foreach ($similarIds as $id) {
            if (isset($byId[$id])) {
                $ordered[] = $byId[$id];
            }
        }

        // Log when the collector dropped any cached IDs — means the cache
        // was built against a submission set wider than the current context
        // can see (e.g. an article was moved to a different journal, unpub-
        // lished, or our context filter was tightened). Not a user-facing
        // error, but operators need visibility to know the cache is drifting.
        $dropped = count($similarIds) - count($ordered);
        if ($dropped > 0) {
            $missing = array_values(array_diff((array) $similarIds, array_keys($byId)));
            error_log('similarArticles: dropped ' . $dropped . ' of '
                . count($similarIds) . ' cached neighbours for submission '
                . $submissionId . ' (missing: ' . implode(',', $missing) . ')');
        }

        if (empty($ordered)) {
            return Hook::CONTINUE;
        }

        $templateManager->assign('similarArticles', $ordered);
        $output .= $templateManager->fetch($this->getTemplateResource('articleFooter.tpl'));
        return Hook::CONTINUE;
    }
}
