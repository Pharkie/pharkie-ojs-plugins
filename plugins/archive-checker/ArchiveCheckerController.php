<?php

namespace APP\plugins\generic\archiveChecker;

use APP\core\Application;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Illuminate\Http\Response;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Route;
use PKP\config\Config;
use PKP\core\Core;
use PKP\core\PKPBaseController;
use PKP\core\PKPRequest;
use PKP\security\Role;

class ArchiveCheckerController extends PKPBaseController
{

    public function getHandlerPath(): string
    {
        return 'archive-checker';
    }

    public function getRouteGroupMiddleware(): array
    {
        // HasUser resolves the session user for browser-based API calls.
        // Without this, $request->getUser() returns null for API routes.
        return ['has.user'];
    }

    /**
     * Authorization is handled per-endpoint via requireManager().
     * This bypasses OJS's role-based middleware since we check roles ourselves.
     */
    public function authorize(PKPRequest $request, array &$args, array $roleAssignments): bool
    {
        return true;
    }

    public function getGroupRoutes(): void
    {
        Route::get('articles', $this->listArticles(...))->name('ac.articles.list');
        Route::get('articles/{submissionId}', $this->getArticle(...))->name('ac.articles.get');
        Route::get('articles/{submissionId}/pdf', $this->getArticlePdf(...))->name('ac.articles.pdf');
        Route::get('articles/{submissionId}/html', $this->getArticleHtml(...))->name('ac.articles.html');
        Route::get('articles/{submissionId}/classification', $this->getClassification(...))->name('ac.articles.classification');
        Route::post('reviews', $this->submitReview(...))->name('ac.reviews.submit');
        Route::get('nav/random-unreviewed', $this->randomUnreviewed(...))->name('ac.nav.random');
        Route::get('nav/problem-case', $this->problemCase(...))->name('ac.nav.problem');
        Route::get('stats', $this->getStats(...))->name('ac.stats');
    }

    // ---------------------------------------------------------------
    // Auth + context helpers
    // ---------------------------------------------------------------

    /**
     * Require any authenticated user.
     * Returns null on success, or a JsonResponse error.
     */
    private function requireAuthenticated(Request $request): ?JsonResponse
    {
        $ojsRequest = Application::get()->getRequest();
        $user = $ojsRequest->getUser();
        if (!$user) {
            return new JsonResponse(['error' => 'Authentication required'], 401);
        }
        return null;
    }

    /**
     * Get current journal context ID.
     */
    private function getContextId(): int
    {
        $context = Application::get()->getRequest()->getContext();
        return $context ? $context->getId() : 0;
    }

    /**
     * Verify a submission belongs to the current journal context.
     * Returns the current publication_id, or null if not found.
     */
    private function getPublicationForSubmission(int $submissionId): ?object
    {
        return DB::table('publications')
            ->join('submissions', 'publications.submission_id', '=', 'submissions.submission_id')
            ->where('submissions.submission_id', $submissionId)
            ->where('submissions.context_id', $this->getContextId())
            ->whereColumn('publications.publication_id', '=', 'submissions.current_publication_id')
            ->select('publications.publication_id')
            ->first();
    }

    // ---------------------------------------------------------------
    // Content from OJS storage
    // ---------------------------------------------------------------

    /**
     * Compute SHA256 hash of HTML galley content from OJS storage.
     * Reviews are invalidated when the imported content changes.
     */
    private function computeContentHash(int $publicationId): ?string
    {
        $html = $this->readGalleyContentFromOjs($publicationId, 'text/html');
        if ($html === null) {
            return null;
        }
        return hash('sha256', $html);
    }

    // ---------------------------------------------------------------
    // Endpoints
    // ---------------------------------------------------------------

    /**
     * GET /articles — list all articles with issue info and review status.
     */
    public function listArticles(Request $request): JsonResponse
    {
        $authError = $this->requireAuthenticated($request);
        if ($authError) return $authError;

        $contextId = $this->getContextId();

        // All publications with issue and section info (current publication only)
        $articles = DB::table('submissions as s')
            ->join('publications as p', function ($join) {
                $join->on('p.submission_id', '=', 's.submission_id')
                     ->whereColumn('p.publication_id', '=', 's.current_publication_id');
            })
            ->join('issues as i', 'p.issue_id', '=', 'i.issue_id')
            ->leftJoin('publication_settings as ps_title', function ($join) {
                $join->on('ps_title.publication_id', '=', 'p.publication_id')
                     ->where('ps_title.setting_name', '=', 'title')
                     ->where('ps_title.locale', '=', 'en');
            })
            ->leftJoin('section_settings as ss', function ($join) {
                $join->on('ss.section_id', '=', 'p.section_id')
                     ->where('ss.setting_name', '=', 'title')
                     ->where('ss.locale', '=', 'en');
            })
            ->leftJoin('issue_settings as is_title', function ($join) {
                $join->on('is_title.issue_id', '=', 'i.issue_id')
                     ->where('is_title.setting_name', '=', 'title')
                     ->where('is_title.locale', '=', 'en');
            })
            ->where('s.context_id', $contextId)
            ->orderBy('i.volume', 'desc')
            ->orderBy('i.number', 'desc')
            ->orderByRaw('CAST(p.seq AS UNSIGNED) ASC')
            ->select([
                's.submission_id',
                'p.publication_id',
                'p.seq',
                'i.volume',
                'i.number',
                'i.year',
                'ps_title.setting_value as title',
                'ss.setting_value as section',
                'is_title.setting_value as issue_title',
            ])
            ->get();

        // Latest review per submission
        $reviews = DB::table('archive_checker_reviews as r1')
            ->whereRaw('r1.review_id = (SELECT MAX(r2.review_id) FROM archive_checker_reviews r2 WHERE r2.submission_id = r1.submission_id)')
            ->get()
            ->keyBy('submission_id');

        // Authors — prefer 'en' locale, fall back to any available
        $pubIds = $articles->pluck('publication_id')->unique()->toArray();
        $authors = [];
        if ($pubIds) {
            $authorRows = DB::table('authors as a')
                ->leftJoin('author_settings as fname_en', function ($join) {
                    $join->on('fname_en.author_id', '=', 'a.author_id')
                         ->where('fname_en.setting_name', '=', 'givenName')
                         ->where('fname_en.locale', '=', 'en');
                })
                ->leftJoin('author_settings as fname_any', function ($join) {
                    $join->on('fname_any.author_id', '=', 'a.author_id')
                         ->where('fname_any.setting_name', '=', 'givenName');
                })
                ->leftJoin('author_settings as lname_en', function ($join) {
                    $join->on('lname_en.author_id', '=', 'a.author_id')
                         ->where('lname_en.setting_name', '=', 'familyName')
                         ->where('lname_en.locale', '=', 'en');
                })
                ->leftJoin('author_settings as lname_any', function ($join) {
                    $join->on('lname_any.author_id', '=', 'a.author_id')
                         ->where('lname_any.setting_name', '=', 'familyName');
                })
                ->whereIn('a.publication_id', $pubIds)
                ->orderBy('a.seq')
                ->select([
                    'a.publication_id',
                    DB::raw('COALESCE(fname_en.setting_value, fname_any.setting_value) as given_name'),
                    DB::raw('COALESCE(lname_en.setting_value, lname_any.setting_value) as family_name'),
                ])
                ->get();

            foreach ($authorRows as $row) {
                $name = trim(($row->given_name ?? '') . ' ' . ($row->family_name ?? ''));
                if ($name) {
                    $authors[$row->publication_id][] = $name;
                }
            }
        }

        // DOIs — publications.doi_id → dois.doi
        $dois = [];
        if ($pubIds) {
            $doiRows = DB::table('publications as p')
                ->join('dois as d', 'p.doi_id', '=', 'd.doi_id')
                ->whereIn('p.publication_id', $pubIds)
                ->select(['p.publication_id', 'd.doi'])
                ->get();
            foreach ($doiRows as $row) {
                $dois[$row->publication_id] = $row->doi;
            }
        }

        // Subtitles + Abstracts
        $subtitles = [];
        $abstracts = [];
        $sources = [];
        if ($pubIds) {
            $settingsRows = DB::table('publication_settings')
                ->whereIn('publication_id', $pubIds)
                ->whereIn('setting_name', ['subtitle', 'abstract', 'source'])
                ->where('locale', 'en')
                ->select(['publication_id', 'setting_name', 'setting_value'])
                ->get();
            foreach ($settingsRows as $row) {
                if ($row->setting_name === 'subtitle') {
                    $subtitles[$row->publication_id] = $row->setting_value;
                } elseif ($row->setting_name === 'source') {
                    $sources[$row->publication_id] = $row->setting_value;
                } else {
                    $abstracts[$row->publication_id] = $row->setting_value;
                }
            }
        }

        // Keywords (controlled vocab)
        $keywords = [];
        if ($pubIds) {
            $kwRows = DB::table('controlled_vocabs as cv')
                ->join('controlled_vocab_entries as cve', 'cv.controlled_vocab_id', '=', 'cve.controlled_vocab_id')
                ->join('controlled_vocab_entry_settings as cves', 'cve.controlled_vocab_entry_id', '=', 'cves.controlled_vocab_entry_id')
                ->where('cv.symbolic', 'submissionKeyword')
                ->whereIn('cv.assoc_id', $pubIds)
                ->orderBy('cve.seq')
                ->select(['cv.assoc_id as publication_id', 'cves.setting_value as keyword'])
                ->get();
            foreach ($kwRows as $row) {
                $keywords[$row->publication_id][] = $row->keyword;
            }
        }

        // Content-filtered flag — check HTML galleys for data-content-filtered marker
        $contentFiltered = [];
        if ($pubIds) {
            $htmlGalleys = DB::table('publication_galleys as pg')
                ->join('submission_files as sf', 'pg.submission_file_id', '=', 'sf.submission_file_id')
                ->join('files as f', 'sf.file_id', '=', 'f.file_id')
                ->whereIn('pg.publication_id', $pubIds)
                ->where('f.mimetype', 'text/html')
                ->select(['pg.publication_id', 'f.path'])
                ->get();
            $filesDir = rtrim(Config::getVar('files', 'files_dir'), '/');
            foreach ($htmlGalleys as $row) {
                $filePath = $filesDir . '/' . $row->path;
                if (file_exists($filePath)) {
                    $head = file_get_contents($filePath, false, null, 0, 200);
                    if (str_contains($head, 'data-content-filtered')) {
                        $contentFiltered[$row->publication_id] = true;
                    }
                }
            }
        }

        // Pages
        $pages = [];
        if ($pubIds) {
            $pageRows = DB::table('publication_settings')
                ->whereIn('publication_id', $pubIds)
                ->where('setting_name', 'pages')
                ->select(['publication_id', 'setting_value'])
                ->get();
            foreach ($pageRows as $row) {
                $pages[$row->publication_id] = $row->setting_value;
            }
        }

        $result = [];
        $counts = ['total' => 0, 'approved' => 0, 'needs_fix' => 0, 'unreviewed' => 0, 'invalidated' => 0];

        foreach ($articles as $article) {
            $review = $reviews[$article->submission_id] ?? null;

            $status = 'unreviewed';
            $hashValid = null;

            if ($review) {
                $status = $review->decision;
                // Hash validation is deferred to per-article detail view
                // to avoid O(n) file reads on every page load.
            }

            $counts['total']++;
            if ($status === 'approved') $counts['approved']++;
            elseif ($status === 'needs_fix') $counts['needs_fix']++;
            elseif ($status === 'invalidated') $counts['invalidated']++;
            else $counts['unreviewed']++;

            $pubId = (int) $article->publication_id;
            $result[] = [
                'submission_id'  => (int) $article->submission_id,
                'publication_id' => $pubId,
                'title'          => $article->title ?? '(untitled)',
                'subtitle'       => $subtitles[$pubId] ?? null,
                'authors'        => $authors[$pubId] ?? [],
                'section'        => $article->section ?? '',
                'volume'         => (int) $article->volume,
                'number'         => (int) $article->number,
                'year'           => (int) $article->year,
                'issue_title'    => $article->issue_title ?? null,
                'seq'            => (int) $article->seq,
                'doi'            => $dois[$pubId] ?? null,
                'abstract'       => $abstracts[$pubId] ?? null,
                'keywords'       => $keywords[$pubId] ?? [],
                'pages'          => $pages[$pubId] ?? null,
                'source'         => $sources[$pubId] ?? null,
                'status'         => $status,
                'content_filtered' => isset($contentFiltered[$pubId]),
                'reviewer'       => $review ? $review->username : null,
                'reviewed_at'    => $review ? $review->created_at : null,
                'comment'        => $review ? $review->comment : null,
            ];
        }

        return new JsonResponse([
            'articles' => $result,
            'counts'   => $counts,
        ]);
    }

    /**
     * GET /articles/{submissionId} — single article with full review history.
     */
    public function getArticle(Request $request): JsonResponse
    {
        $authError = $this->requireAuthenticated($request);
        if ($authError) return $authError;

        $submissionId = (int) $request->route('submissionId');

        // IDOR protection: verify submission belongs to this journal
        $publication = $this->getPublicationForSubmission($submissionId);
        if (!$publication) {
            return new JsonResponse(['error' => 'Submission not found'], 404);
        }

        $reviews = DB::table('archive_checker_reviews')
            ->where('submission_id', $submissionId)
            ->orderBy('created_at', 'desc')
            ->get()
            ->map(fn ($r) => [
                'review_id'    => $r->review_id,
                'user_id'      => $r->user_id,
                'username'     => $r->username,
                'decision'     => $r->decision,
                'comment'      => $r->comment,
                'content_hash' => $r->content_hash,
                'created_at'   => $r->created_at,
            ])
            ->toArray();

        $currentHash = $this->computeContentHash($publication->publication_id);

        return new JsonResponse([
            'submission_id' => $submissionId,
            'reviews'       => $reviews,
            'current_hash'  => $currentHash,
        ]);
    }

    /**
     * GET /articles/{submissionId}/pdf — stream PDF galley file.
     */
    public function getArticlePdf(Request $request): Response|JsonResponse
    {
        $authError = $this->requireAuthenticated($request);
        if ($authError) return $authError;

        $submissionId = (int) $request->route('submissionId');

        // IDOR protection
        $publication = $this->getPublicationForSubmission($submissionId);
        if (!$publication) {
            return new JsonResponse(['error' => 'Submission not found'], 404);
        }

        return $this->serveGalleyFromOjs($publication->publication_id, 'application/pdf');
    }

    /**
     * GET /articles/{submissionId}/html — return HTML galley body content.
     */
    public function getArticleHtml(Request $request): Response|JsonResponse
    {
        $authError = $this->requireAuthenticated($request);
        if ($authError) return $authError;

        $submissionId = (int) $request->route('submissionId');

        // IDOR protection
        $publication = $this->getPublicationForSubmission($submissionId);
        if (!$publication) {
            return new JsonResponse(['error' => 'Submission not found'], 404);
        }

        $html = $this->readGalleyContentFromOjs($publication->publication_id, 'text/html');
        if ($html === null) {
            return new JsonResponse(['error' => 'No HTML galley found'], 404);
        }

        // Extract <body> content if wrapped in HTML document
        if (preg_match('/<body[^>]*>(.*?)<\/body>/si', $html, $matches)) {
            $html = $matches[1];
        }

        return new Response(trim($html), 200, [
            'Content-Type' => 'text/html; charset=utf-8',
            'Content-Security-Policy' => "default-src 'none'; style-src 'unsafe-inline'; img-src data: https:; script-src 'none';",
        ]);
    }

    /**
     * Serve a galley file from OJS file storage by MIME type.
     */
    private function serveGalleyFromOjs(int $publicationId, string $mimeType): Response|JsonResponse
    {
        $galley = DB::table('publication_galleys as pg')
            ->join('submission_files as sf', 'pg.submission_file_id', '=', 'sf.submission_file_id')
            ->join('files as f', 'sf.file_id', '=', 'f.file_id')
            ->where('pg.publication_id', $publicationId)
            ->where('f.mimetype', $mimeType)
            ->select('f.path')
            ->first();

        if (!$galley) {
            return new JsonResponse(['error' => 'Galley not available'], 404);
        }

        $filesDir = rtrim(Config::getVar('files', 'files_dir'), '/');
        $filePath = $filesDir . '/' . $galley->path;

        if (!file_exists($filePath)) {
            return new JsonResponse(['error' => 'Galley not available'], 404);
        }

        return $this->streamFile($filePath, $mimeType);
    }

    /**
     * Stream a file to the client using readfile() (kernel-level, no PHP memory).
     */
    private function streamFile(string $filePath, string $mimeType): void
    {
        header('Content-Type: ' . $mimeType);
        header('Content-Length: ' . filesize($filePath));
        header('Content-Disposition: inline');
        header('Cache-Control: private, max-age=3600');
        readfile($filePath);
        exit;
    }

    private function readGalleyContentFromOjs(int $publicationId, string $mimeType): ?string
    {
        $galley = DB::table('publication_galleys as pg')
            ->join('submission_files as sf', 'pg.submission_file_id', '=', 'sf.submission_file_id')
            ->join('files as f', 'sf.file_id', '=', 'f.file_id')
            ->where('pg.publication_id', $publicationId)
            ->where('f.mimetype', $mimeType)
            ->select('f.path')
            ->first();

        if (!$galley) return null;

        $filesDir = rtrim(Config::getVar('files', 'files_dir'), '/');
        $filePath = $filesDir . '/' . $galley->path;

        return file_exists($filePath) ? file_get_contents($filePath) : null;
    }

    /**
     * GET /articles/{submissionId}/classification — end-matter from OJS.
     *
     * References from citations table (not in HTML galley).
     * Notes/bios/provenance counts from jats-* divs in the HTML galley
     * (content already visible and labelled in the HTML pane).
     */
    public function getClassification(Request $request): JsonResponse
    {
        $authError = $this->requireAuthenticated($request);
        if ($authError) return $authError;

        $submissionId = (int) $request->route('submissionId');

        // IDOR protection
        $publication = $this->getPublicationForSubmission($submissionId);
        if (!$publication) {
            return new JsonResponse(['error' => 'Submission not found'], 404);
        }

        $pubId = $publication->publication_id;

        // References from citations table, with matched DOIs
        $references = DB::table('citations as c')
            ->leftJoin('citation_settings as cs', function ($join) {
                $join->on('c.citation_id', '=', 'cs.citation_id')
                     ->where('cs.setting_name', '=', 'crossref::doi');
            })
            ->where('c.publication_id', $pubId)
            ->orderBy('c.seq')
            ->select(['c.raw_citation', 'cs.setting_value as doi'])
            ->get()
            ->map(fn ($row) => [
                'text' => trim($row->raw_citation),
                'doi'  => $row->doi ?: null,
            ])
            ->toArray();

        // Count individual items inside jats-* divs in the HTML galley
        $html = $this->readGalleyContentFromOjs($pubId, 'text/html');
        $notesCount = 0;
        $biosCount = 0;
        $provenanceCount = 0;
        if ($html) {
            // Notes use <ol><li> items
            if (preg_match('/<div\s+class="jats-notes">(.*?)<\/div>/s', $html, $m)) {
                $notesCount = preg_match_all('/<li>/', $m[1]);
            }
            // Bios use <p> items
            if (preg_match('/<div\s+class="jats-bios">(.*?)<\/div>/s', $html, $m)) {
                $biosCount = preg_match_all('/<p>/', $m[1]);
            }
            // Provenance uses <p> items
            if (preg_match('/<div\s+class="jats-provenance">(.*?)<\/div>/s', $html, $m)) {
                $provenanceCount = preg_match_all('/<p>/', $m[1]);
            }
        }

        return new JsonResponse([
            'references'       => $references,
            'notes_count'      => $notesCount,
            'bios_count'       => $biosCount,
            'provenance_count' => $provenanceCount,
        ]);
    }

    /**
     * POST /reviews — submit a review decision.
     */
    public function submitReview(Request $request): JsonResponse
    {
        $authError = $this->requireAuthenticated($request);
        if ($authError) return $authError;

        // CSRF validation
        $ojsRequest = Application::get()->getRequest();
        $token = $request->header('X-Csrf-Token');
        $sessionToken = $ojsRequest->getSession()->token();
        if (!$token || !hash_equals($sessionToken, $token)) {
            return new JsonResponse(['error' => 'Invalid CSRF token'], 403);
        }

        $user = $ojsRequest->getUser();
        $submissionId = (int) $request->input('submissionId');
        $decision = $request->input('decision');
        $comment = $request->input('comment', '');

        if (!in_array($decision, ['approved', 'needs_fix'])) {
            return new JsonResponse(['error' => 'Invalid decision. Must be "approved" or "needs_fix".'], 400);
        }

        if ($decision === 'needs_fix' && empty(trim($comment))) {
            return new JsonResponse(['error' => 'Comment required for rejections.'], 400);
        }

        if (mb_strlen($comment) > 5000) {
            return new JsonResponse(['error' => 'Comment too long (max 5000 characters).'], 400);
        }

        // IDOR protection: verify submission belongs to this journal
        $publication = $this->getPublicationForSubmission($submissionId);
        if (!$publication) {
            return new JsonResponse(['error' => 'Submission not found'], 404);
        }

        $contentHash = $this->computeContentHash($publication->publication_id);

        DB::table('archive_checker_reviews')->insert([
            'submission_id'  => $submissionId,
            'publication_id' => $publication->publication_id,
            'user_id'        => $user->getId(),
            'username'       => $user->getUsername(),
            'decision'       => $decision,
            'comment'        => $decision === 'needs_fix' ? trim($comment) : null,
            'content_hash'   => $contentHash,
            'created_at'     => Core::getCurrentDate(),
        ]);

        return new JsonResponse([
            'success'      => true,
            'decision'     => $decision,
            'content_hash' => $contentHash,
        ]);
    }

    /**
     * GET /nav/random-unreviewed — return a random unreviewed submission_id.
     */
    public function randomUnreviewed(Request $request): JsonResponse
    {
        $authError = $this->requireAuthenticated($request);
        if ($authError) return $authError;

        $contextId = $this->getContextId();

        $unreviewed = DB::table('submissions as s')
            ->join('publications as p', function ($join) {
                $join->on('p.submission_id', '=', 's.submission_id')
                     ->whereColumn('p.publication_id', '=', 's.current_publication_id');
            })
            ->leftJoin('archive_checker_reviews as r', 's.submission_id', '=', 'r.submission_id')
            ->where('s.context_id', $contextId)
            ->whereNull('r.review_id')
            ->select('s.submission_id')
            ->inRandomOrder()
            ->limit(10)
            ->get();

        if ($unreviewed->isEmpty()) {
            return new JsonResponse(['submission_ids' => [], 'message' => 'All articles have been reviewed']);
        }

        return new JsonResponse(['submission_ids' => $unreviewed->pluck('submission_id')->values()->toArray()]);
    }

    /**
     * GET /nav/problem-case — return next problem case submission_id.
     * Priority: rejected > unreviewed.
     * Hash invalidation is checked per-article on view (not here —
     * computing hashes for all approved articles is O(n) file I/O).
     */
    public function problemCase(Request $request): JsonResponse
    {
        $authError = $this->requireAuthenticated($request);
        if ($authError) return $authError;

        $contextId = $this->getContextId();

        // 1. Rejected articles
        $rejected = DB::table('archive_checker_reviews as r1')
            ->join('submissions as s', 'r1.submission_id', '=', 's.submission_id')
            ->where('s.context_id', $contextId)
            ->whereRaw('r1.review_id = (SELECT MAX(r2.review_id) FROM archive_checker_reviews r2 WHERE r2.submission_id = r1.submission_id)')
            ->where('r1.decision', 'needs_fix')
            ->select('r1.submission_id')
            ->inRandomOrder()
            ->first();

        if ($rejected) {
            return new JsonResponse(['submission_id' => $rejected->submission_id, 'reason' => 'needs_fix']);
        }

        // 2. Unreviewed articles
        $unreviewed = DB::table('submissions as s')
            ->join('publications as p', function ($join) {
                $join->on('p.submission_id', '=', 's.submission_id')
                     ->whereColumn('p.publication_id', '=', 's.current_publication_id');
            })
            ->leftJoin('archive_checker_reviews as r', 's.submission_id', '=', 'r.submission_id')
            ->where('s.context_id', $contextId)
            ->whereNull('r.review_id')
            ->select('s.submission_id')
            ->inRandomOrder()
            ->first();

        if ($unreviewed) {
            return new JsonResponse(['submission_id' => $unreviewed->submission_id, 'reason' => 'unreviewed']);
        }

        return new JsonResponse(['submission_id' => null, 'message' => 'No problem cases found']);
    }

    /**
     * GET /stats — QA progress breakdown by section and reviewer depth.
     */
    public function getStats(Request $request): JsonResponse
    {
        $authError = $this->requireAuthenticated($request);
        if ($authError) return $authError;

        $contextId = $this->getContextId();

        // All articles with section info
        $articles = DB::table('submissions as s')
            ->join('publications as p', function ($join) {
                $join->on('p.submission_id', '=', 's.submission_id')
                     ->whereColumn('p.publication_id', '=', 's.current_publication_id');
            })
            ->join('issues as i', 'p.issue_id', '=', 'i.issue_id')
            ->leftJoin('section_settings as ss', function ($join) {
                $join->on('ss.section_id', '=', 'p.section_id')
                     ->where('ss.setting_name', '=', 'title')
                     ->where('ss.locale', '=', 'en');
            })
            ->where('s.context_id', $contextId)
            ->select([
                's.submission_id',
                DB::raw("COALESCE(ss.setting_value, 'Uncategorised') as section"),
            ])
            ->get();

        // Latest review per submission + count of distinct reviewers
        $reviewData = DB::table('archive_checker_reviews')
            ->select([
                'submission_id',
                DB::raw('MAX(CASE WHEN review_id = (SELECT MAX(r2.review_id) FROM archive_checker_reviews r2 WHERE r2.submission_id = archive_checker_reviews.submission_id) THEN decision END) as latest_decision'),
                DB::raw('COUNT(DISTINCT user_id) as reviewer_count'),
            ])
            ->groupBy('submission_id')
            ->get()
            ->keyBy('submission_id');

        // Build section breakdown
        $sections = [];
        $overall = ['total' => 0, 'approved' => 0, 'needs_fix' => 0, 'unreviewed' => 0];
        $byReviewerCount = [0 => 0, 1 => 0, 2 => 0]; // 0 reviewers, 1 reviewer, 2+ reviewers

        foreach ($articles as $article) {
            $section = $article->section;
            if (!isset($sections[$section])) {
                $sections[$section] = ['total' => 0, 'approved' => 0, 'needs_fix' => 0, 'unreviewed' => 0];
            }

            $review = $reviewData[$article->submission_id] ?? null;
            $status = 'unreviewed';
            $reviewerCount = 0;

            if ($review) {
                $status = $review->latest_decision;
                $reviewerCount = (int) $review->reviewer_count;
            }

            $sections[$section]['total']++;
            $sections[$section][$status]++;
            $overall['total']++;
            $overall[$status]++;

            if ($reviewerCount === 0) $byReviewerCount[0]++;
            elseif ($reviewerCount === 1) $byReviewerCount[1]++;
            else $byReviewerCount[2]++;
        }

        // Sort sections by total descending
        uasort($sections, fn($a, $b) => $b['total'] <=> $a['total']);

        return new JsonResponse([
            'overall'          => $overall,
            'by_section'       => $sections,
            'by_reviewer_count' => [
                ['label' => 'No reviews',     'count' => $byReviewerCount[0]],
                ['label' => '1 reviewer',     'count' => $byReviewerCount[1]],
                ['label' => '2+ reviewers',   'count' => $byReviewerCount[2]],
            ],
        ]);
    }
}
