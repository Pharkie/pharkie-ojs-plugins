<?php

namespace APP\plugins\generic\qaSplits;

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

class QaSplitsController extends PKPBaseController
{
    /** @var array<int, array{jats: string, html: string, pdf: string, issue_dir: string}>|null */
    private ?array $fileIndex = null;

    public function getHandlerPath(): string
    {
        return 'qa-splits';
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
        Route::get('articles', $this->listArticles(...))->name('qa.articles.list');
        Route::get('articles/{submissionId}', $this->getArticle(...))->name('qa.articles.get');
        Route::get('articles/{submissionId}/pdf', $this->getArticlePdf(...))->name('qa.articles.pdf');
        Route::get('articles/{submissionId}/html', $this->getArticleHtml(...))->name('qa.articles.html');
        Route::get('articles/{submissionId}/classification', $this->getClassification(...))->name('qa.articles.classification');
        Route::post('reviews', $this->submitReview(...))->name('qa.reviews.submit');
        Route::get('nav/random-unreviewed', $this->randomUnreviewed(...))->name('qa.nav.random');
        Route::get('nav/problem-case', $this->problemCase(...))->name('qa.nav.problem');
        Route::get('stats', $this->getStats(...))->name('qa.stats');
    }

    // ---------------------------------------------------------------
    // Auth + context helpers
    // ---------------------------------------------------------------

    /**
     * Require authenticated user with Manager or Site Admin role.
     * Returns null on success, or a JsonResponse error.
     */
    private function requireManager(Request $request): ?JsonResponse
    {
        $ojsRequest = Application::get()->getRequest();
        $user = $ojsRequest->getUser();
        if (!$user) {
            return new JsonResponse(['error' => 'Authentication required'], 401);
        }

        $context = $ojsRequest->getContext();
        $contextId = $context ? $context->getId() : 0;

        $hasRole = DB::table('user_user_groups')
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

        if (!$hasRole) {
            return new JsonResponse(['error' => 'Manager or Site Admin role required'], 403);
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
    // File index: submission_id → JATS/PDF/HTML paths
    // ---------------------------------------------------------------

    private function getBackfillDir(): string
    {
        return Config::getVar('qa-splits', 'backfill_output_dir', '/data/sample-issues');
    }

    /**
     * Scan JATS files in the backfill output directory and build a mapping
     * from publisher-id (= OJS submission_id) to file paths.
     */
    private function buildFileIndex(): array
    {
        if ($this->fileIndex !== null) {
            return $this->fileIndex;
        }

        $this->fileIndex = [];
        $baseDir = $this->getBackfillDir();
        if (!is_dir($baseDir)) {
            return $this->fileIndex;
        }

        $realBase = realpath($baseDir);
        $issueDirs = glob($baseDir . '/*', GLOB_ONLYDIR);
        foreach ($issueDirs as $issueDir) {
            $jatsFiles = glob($issueDir . '/*.jats.xml');
            foreach ($jatsFiles as $jatsPath) {
                // Path traversal protection: ensure file is within base dir
                $realPath = realpath($jatsPath);
                if (!$realPath || !str_starts_with($realPath, $realBase)) {
                    continue;
                }

                $publisherId = $this->readPublisherId($realPath);
                if ($publisherId === null) {
                    continue;
                }

                $baseName = preg_replace('/\.jats\.xml$/', '', $realPath);
                $this->fileIndex[(int) $publisherId] = [
                    'jats' => $realPath,
                    'html' => $baseName . '.html',
                    'pdf'  => $baseName . '.pdf',
                    'issue_dir' => basename($issueDir),
                ];
            }
        }

        return $this->fileIndex;
    }

    /**
     * Read publisher-id from a JATS XML file header.
     */
    private function readPublisherId(string $jatsPath): ?string
    {
        $head = file_get_contents($jatsPath, false, null, 0, 2048);
        if ($head === false) {
            return null;
        }

        if (preg_match('/<article-id\s+pub-id-type="publisher-id">(\d+)<\/article-id>/', $head, $m)) {
            return $m[1];
        }

        return null;
    }

    /**
     * Get file paths for a submission, or null if not in backfill output.
     */
    private function getFilePaths(int $submissionId): ?array
    {
        $index = $this->buildFileIndex();
        return $index[$submissionId] ?? null;
    }

    // ---------------------------------------------------------------
    // JATS parsing
    // ---------------------------------------------------------------

    /**
     * Extract text content from a SimpleXML element's <p> children.
     */
    private function extractParagraphs(\SimpleXMLElement $element): string
    {
        $text = '';
        foreach ($element->p as $p) {
            $text .= trim(strip_tags($p->asXML())) . ' ';
        }
        return trim($text);
    }

    /**
     * Parse JATS <back> element to extract classified end-matter items.
     *
     * Items are already classified by the pipeline (extract_citations.py,
     * split_citation_tiers.py). This just reads the resulting structure:
     *   <ref-list>/<ref>/<mixed-citation> → references
     *   <fn-group>/<fn>                   → notes
     *   <bio>                             → author bios
     *   <notes notes-type="provenance">   → provenance
     */
    private function parseJatsBackMatter(string $jatsPath): array
    {
        $result = [
            'references' => [],
            'notes'      => [],
            'bios'       => [],
            'provenance' => [],
        ];

        if (!file_exists($jatsPath)) {
            return $result;
        }

        libxml_use_internal_errors(true);
        $xml = simplexml_load_file($jatsPath);
        if ($xml === false) {
            $result['error'] = 'Failed to parse JATS XML';
            return $result;
        }

        foreach ($xml->xpath('//back/ref-list/ref') as $ref) {
            $citation = $ref->{'mixed-citation'};
            if ($citation) {
                $result['references'][] = [
                    'text' => trim((string) $citation),
                    'id'   => (string) ($ref['id'] ?? ''),
                ];
            }
        }

        foreach ($xml->xpath('//back/fn-group/fn') as $fn) {
            $text = $this->extractParagraphs($fn);
            if ($text) {
                $result['notes'][] = [
                    'text' => $text,
                    'id'   => (string) ($fn['id'] ?? ''),
                ];
            }
        }

        foreach ($xml->xpath('//back/bio') as $bio) {
            $text = $this->extractParagraphs($bio);
            if ($text) {
                $result['bios'][] = ['text' => $text];
            }
        }

        foreach ($xml->xpath('//back/notes[@notes-type="provenance"]') as $notes) {
            $text = $this->extractParagraphs($notes);
            if ($text) {
                $result['provenance'][] = ['text' => $text];
            }
        }

        return $result;
    }

    /**
     * Compute SHA256 hash of HTML galley + JATS content.
     * Including JATS ensures reviews are invalidated when back-matter changes,
     * even if the HTML galley hasn't been regenerated yet.
     */
    private function computeContentHash(int $submissionId): ?string
    {
        $paths = $this->getFilePaths($submissionId);
        if (!$paths) {
            return null;
        }

        $parts = '';
        if (file_exists($paths['html'])) {
            $parts .= hash_file('sha256', $paths['html']);
        }
        if (file_exists($paths['jats'])) {
            $parts .= hash_file('sha256', $paths['jats']);
        }

        return $parts ? hash('sha256', $parts) : null;
    }

    // ---------------------------------------------------------------
    // Endpoints
    // ---------------------------------------------------------------

    /**
     * GET /articles — list all articles with issue info and review status.
     */
    public function listArticles(Request $request): JsonResponse
    {
        $authError = $this->requireManager($request);
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
            ])
            ->get();

        // Latest review per submission
        $reviews = DB::table('qa_split_reviews as r1')
            ->whereRaw('r1.review_id = (SELECT MAX(r2.review_id) FROM qa_split_reviews r2 WHERE r2.submission_id = r1.submission_id)')
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

        $fileIndex = $this->buildFileIndex();
        $result = [];
        $counts = ['total' => 0, 'approved' => 0, 'rejected' => 0, 'unreviewed' => 0, 'invalidated' => 0];

        foreach ($articles as $article) {
            $review = $reviews[$article->submission_id] ?? null;
            $hasFiles = isset($fileIndex[(int) $article->submission_id]);

            $status = 'unreviewed';
            $hashValid = null;

            if ($review) {
                $status = $review->decision;
                // Hash validation is deferred to per-article detail view
                // to avoid O(n) file reads on every page load.
            }

            $counts['total']++;
            if ($status === 'approved') $counts['approved']++;
            elseif ($status === 'rejected') $counts['rejected']++;
            elseif ($status === 'invalidated') $counts['invalidated']++;
            else $counts['unreviewed']++;

            $result[] = [
                'submission_id'  => (int) $article->submission_id,
                'publication_id' => (int) $article->publication_id,
                'title'          => $article->title ?? '(untitled)',
                'authors'        => $authors[$article->publication_id] ?? [],
                'section'        => $article->section ?? '',
                'volume'         => (int) $article->volume,
                'number'         => (int) $article->number,
                'year'           => (int) $article->year,
                'seq'            => (int) $article->seq,
                'status'         => $status,
                'has_files'      => $hasFiles,
                'reviewer'       => $review ? $review->username : null,
                'reviewed_at'    => $review ? $review->created_at : null,
                'comment'        => $review ? $review->comment : null,
            ];
        }

        // Warn about JATS files without publisher-id (not imported)
        $warnings = [];
        $baseDir = $this->getBackfillDir();
        if (is_dir($baseDir)) {
            foreach (glob($baseDir . '/*', GLOB_ONLYDIR) as $issueDir) {
                foreach (glob($issueDir . '/*.jats.xml') as $jatsPath) {
                    if ($this->readPublisherId($jatsPath) === null) {
                        $warnings[] = basename($issueDir) . '/' . basename($jatsPath) . ' — no publisher-id (not imported)';
                    }
                }
            }
        }

        return new JsonResponse([
            'articles' => $result,
            'counts'   => $counts,
            'warnings' => $warnings,
        ]);
    }

    /**
     * GET /articles/{submissionId} — single article with full review history.
     */
    public function getArticle(Request $request): JsonResponse
    {
        $authError = $this->requireManager($request);
        if ($authError) return $authError;

        $submissionId = (int) $request->route('submissionId');

        // IDOR protection: verify submission belongs to this journal
        $publication = $this->getPublicationForSubmission($submissionId);
        if (!$publication) {
            return new JsonResponse(['error' => 'Submission not found'], 404);
        }

        $reviews = DB::table('qa_split_reviews')
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

        $currentHash = $this->computeContentHash($submissionId);

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
        $authError = $this->requireManager($request);
        if ($authError) return $authError;

        $submissionId = (int) $request->route('submissionId');

        // IDOR protection
        $publication = $this->getPublicationForSubmission($submissionId);
        if (!$publication) {
            return new JsonResponse(['error' => 'Submission not found'], 404);
        }

        $paths = $this->getFilePaths($submissionId);
        if ($paths && file_exists($paths['pdf'])) {
            return $this->streamFile($paths['pdf'], 'application/pdf');
        }

        // Fallback: serve from OJS file storage
        return $this->serveGalleyFromOjs($publication->publication_id, 'application/pdf');
    }

    /**
     * GET /articles/{submissionId}/html — return HTML galley body content.
     */
    public function getArticleHtml(Request $request): Response|JsonResponse
    {
        $authError = $this->requireManager($request);
        if ($authError) return $authError;

        $submissionId = (int) $request->route('submissionId');

        // IDOR protection
        $publication = $this->getPublicationForSubmission($submissionId);
        if (!$publication) {
            return new JsonResponse(['error' => 'Submission not found'], 404);
        }

        $paths = $this->getFilePaths($submissionId);
        $html = null;

        if ($paths && file_exists($paths['html'])) {
            $html = file_get_contents($paths['html']);
        } else {
            $html = $this->readGalleyContentFromOjs($publication->publication_id, 'text/html');
        }

        if ($html === null) {
            return new JsonResponse(['error' => 'No HTML galley found'], 404);
        }

        // Extract <body> content if wrapped in HTML document
        if (preg_match('/<body[^>]*>(.*?)<\/body>/si', $html, $matches)) {
            $html = $matches[1];
        }

        return new Response(trim($html), 200, [
            'Content-Type' => 'text/html; charset=utf-8',
            // XSS mitigation: restrict what embedded content can do
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
     * Read galley file content from OJS storage. Returns null if not found.
     */
    /**
     * Stream a file to the client using readfile() (kernel-level, no PHP memory).
     * Same approach as OJS core FileManager — avoids loading entire file into memory.
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
     * GET /articles/{submissionId}/classification — JATS back-matter items.
     */
    public function getClassification(Request $request): JsonResponse
    {
        $authError = $this->requireManager($request);
        if ($authError) return $authError;

        $submissionId = (int) $request->route('submissionId');

        // IDOR protection
        $publication = $this->getPublicationForSubmission($submissionId);
        if (!$publication) {
            return new JsonResponse(['error' => 'Submission not found'], 404);
        }

        $paths = $this->getFilePaths($submissionId);
        if (!$paths || !file_exists($paths['jats'])) {
            return new JsonResponse([
                'references' => [],
                'notes' => [],
                'bios' => [],
                'provenance' => [],
            ]);
        }

        return new JsonResponse($this->parseJatsBackMatter($paths['jats']));
    }

    /**
     * POST /reviews — submit a review decision.
     */
    public function submitReview(Request $request): JsonResponse
    {
        $authError = $this->requireManager($request);
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

        if (!in_array($decision, ['approved', 'rejected'])) {
            return new JsonResponse(['error' => 'Invalid decision. Must be "approved" or "rejected".'], 400);
        }

        if ($decision === 'rejected' && empty(trim($comment))) {
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

        $contentHash = $this->computeContentHash($submissionId);

        DB::table('qa_split_reviews')->insert([
            'submission_id'  => $submissionId,
            'publication_id' => $publication->publication_id,
            'user_id'        => $user->getId(),
            'username'       => $user->getUsername(),
            'decision'       => $decision,
            'comment'        => $decision === 'rejected' ? trim($comment) : null,
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
        $authError = $this->requireManager($request);
        if ($authError) return $authError;

        $contextId = $this->getContextId();

        $unreviewed = DB::table('submissions as s')
            ->join('publications as p', function ($join) {
                $join->on('p.submission_id', '=', 's.submission_id')
                     ->whereColumn('p.publication_id', '=', 's.current_publication_id');
            })
            ->leftJoin('qa_split_reviews as r', 's.submission_id', '=', 'r.submission_id')
            ->where('s.context_id', $contextId)
            ->whereNull('r.review_id')
            ->select('s.submission_id')
            ->inRandomOrder()
            ->first();

        if (!$unreviewed) {
            return new JsonResponse(['submission_id' => null, 'message' => 'All articles have been reviewed']);
        }

        return new JsonResponse(['submission_id' => $unreviewed->submission_id]);
    }

    /**
     * GET /nav/problem-case — return next problem case submission_id.
     * Priority: rejected > unreviewed.
     * Hash invalidation is checked per-article on view (not here —
     * computing hashes for all approved articles is O(n) file I/O).
     */
    public function problemCase(Request $request): JsonResponse
    {
        $authError = $this->requireManager($request);
        if ($authError) return $authError;

        $contextId = $this->getContextId();

        // 1. Rejected articles
        $rejected = DB::table('qa_split_reviews as r1')
            ->join('submissions as s', 'r1.submission_id', '=', 's.submission_id')
            ->where('s.context_id', $contextId)
            ->whereRaw('r1.review_id = (SELECT MAX(r2.review_id) FROM qa_split_reviews r2 WHERE r2.submission_id = r1.submission_id)')
            ->where('r1.decision', 'rejected')
            ->select('r1.submission_id')
            ->inRandomOrder()
            ->first();

        if ($rejected) {
            return new JsonResponse(['submission_id' => $rejected->submission_id, 'reason' => 'rejected']);
        }

        // 2. Unreviewed articles
        $unreviewed = DB::table('submissions as s')
            ->join('publications as p', function ($join) {
                $join->on('p.submission_id', '=', 's.submission_id')
                     ->whereColumn('p.publication_id', '=', 's.current_publication_id');
            })
            ->leftJoin('qa_split_reviews as r', 's.submission_id', '=', 'r.submission_id')
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
        $authError = $this->requireManager($request);
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
        $reviewData = DB::table('qa_split_reviews')
            ->select([
                'submission_id',
                DB::raw('MAX(CASE WHEN review_id = (SELECT MAX(r2.review_id) FROM qa_split_reviews r2 WHERE r2.submission_id = qa_split_reviews.submission_id) THEN decision END) as latest_decision'),
                DB::raw('COUNT(DISTINCT user_id) as reviewer_count'),
            ])
            ->groupBy('submission_id')
            ->get()
            ->keyBy('submission_id');

        // Build section breakdown
        $sections = [];
        $overall = ['total' => 0, 'approved' => 0, 'rejected' => 0, 'unreviewed' => 0];
        $byReviewerCount = [0 => 0, 1 => 0, 2 => 0]; // 0 reviewers, 1 reviewer, 2+ reviewers

        foreach ($articles as $article) {
            $section = $article->section;
            if (!isset($sections[$section])) {
                $sections[$section] = ['total' => 0, 'approved' => 0, 'rejected' => 0, 'unreviewed' => 0];
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
        arsort(array_column($sections, 'total'));

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
