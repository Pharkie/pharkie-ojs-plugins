<?php

/**
 * Which OJS routes archive mode closes, and where on the new site each page
 * now lives. Pure functions with no OJS dependencies, so they can be tested
 * on their own (tests/routes-test.php).
 */

namespace APP\plugins\generic\archiveMode;

class ArchiveModeRoutes
{
    /** What a link to the new site points at, which picks its wording. */
    public const KIND_ARTICLE = 'article';
    public const KIND_ISSUE = 'issue';
    public const KIND_JOURNAL = 'journal';

    /** A slug as Harbour writes them (lowercase, digits, hyphens). Anything else in the map is ignored. */
    private const SLUG = '/^[a-z0-9][a-z0-9-]{0,199}$/';

    /**
     * Is this page closed in archive mode?
     *
     * Closed: signing in, registering and every password reset (the whole
     * `login` page except signing out), everything under `user` (profile,
     * registration, subscriptions and their payments), every `payment` route,
     * the About > Subscriptions page, and every page on joining or
     * submitting: For Readers (how to register), For Authors (stock OJS:
     * register or log in to submit), About > Submissions (stock OJS: "Login
     * or Register to make a submission") and the SEA's own "Submit an
     * Article" page. Also the SEA's "For Advertisers" page, which sells
     * adverts. Those two are static pages, at `submitanarticle` and
     * `advertisers`.
     *
     * Left open: Stripe's webhook and its return from Checkout. Both only
     * complete a payment that has already been taken, so a purchase made just
     * before the switch still reaches its buyer.
     */
    public static function isClosed(string $page, string $op, array $args): bool
    {
        switch ($page) {
            case 'login':
                return !in_array($op, ['signOut', 'signOutAsUser'], true);
            case 'user':
                return true;
            case 'payment':
                $stripeCallback = $op === 'plugin'
                    && ($args[0] ?? '') === 'StripePayment'
                    && in_array($args[1] ?? '', ['return', 'webhook'], true);
                return !$stripeCallback;
            case 'about':
                return in_array($op, ['subscriptions', 'submissions'], true);
            case 'information':
                return in_array($op, ['readers', 'authors'], true);
            case 'submitanarticle':
            case 'advertisers':
                return true;
        }
        return false;
    }

    /**
     * The sidebar's blocks, less every one that offers something the archive
     * no longer does: each custom block (the journal's own banners, event
     * adverts and its "For Advertisers" link, written for the live site), and
     * OJS's "Make a Submission" and "Subscription" blocks. Reading blocks
     * (Information, Browse, Language and the like) stay, in their order.
     *
     * @param array $sidebar block plugin names, as the journal's `sidebar` setting holds them
     * @param array $customBlocks the names of the custom blocks (Custom Block Manager)
     */
    public static function readingSidebar(array $sidebar, array $customBlocks): array
    {
        $hidden = array_merge(['makesubmissionblockplugin', 'subscriptionblockplugin'], $customBlocks);
        return array_values(array_filter($sidebar, fn ($name) => !in_array($name, $hidden, true)));
    }

    /**
     * The page on the new site that stands for this OJS page.
     *
     * $movedTo is the journal's new home (for the SEA,
     * https://existentialanalysis.org.uk/journal). An article or issue goes
     * to its own page there when the map knows it; otherwise each section
     * goes where Harbour's redirect map (src/lib/redirects.ts) sends it, and
     * anything else to the journal's home.
     *
     * @param array $map ['articles' => [submissionId => slug], 'issues' => [issueId => slug]]
     * @param array $query the request's query string, for a search
     * @return array{0: string, 1: string} [kind, url]
     */
    public static function destination(string $movedTo, string $page, string $op, array $args, array $map, array $query = []): array
    {
        $base = rtrim($movedTo, '/');
        $id = (string) ($args[0] ?? '');
        $viewing = in_array($op, ['view', 'download'], true);

        if ($page === 'article' && $viewing) {
            $slug = self::slug($map['articles'][$id] ?? null);
            if ($slug !== null) {
                return [self::KIND_ARTICLE, $base . '/' . $slug];
            }
            return [self::KIND_JOURNAL, $base];
        }

        if ($page === 'issue') {
            if ($viewing) {
                $slug = self::slug($map['issues'][$id] ?? null);
                if ($slug !== null) {
                    return [self::KIND_ISSUE, $base . '/issues/' . $slug];
                }
            }
            return [self::KIND_JOURNAL, $base . '/issues'];
        }

        if ($page === 'about') {
            switch ($op) {
                case '':
                case 'index':
                    return [self::KIND_JOURNAL, $base . '/about'];
                case 'submissions':
                    return [self::KIND_JOURNAL, $base . '/for-contributors'];
                case 'editorialMasthead':
                case 'editorialTeam':
                    return [self::KIND_JOURNAL, $base . '/editorial-board'];
            }
        }

        if (($page === 'information' && $op === 'authors') || $page === 'submitanarticle') {
            return [self::KIND_JOURNAL, $base . '/for-contributors'];
        }

        if ($page === 'search') {
            $words = trim((string) ($query['query'] ?? ''));
            if ($words !== '') {
                return [self::KIND_JOURNAL, $base . '?q=' . rawurlencode($words)];
            }
        }

        return [self::KIND_JOURNAL, $base];
    }

    /**
     * Split an OJS address (as OJS's `source` parameter carries it, e.g.
     * "/ea/article/view/9821/41712" or "/index.php/ea/login") into
     * [page, op, args]. The journal's own path, and index.php, are dropped.
     * Returns null for anything that is not a path on this site.
     *
     * @return array{0: string, 1: string, 2: array}|null
     */
    public static function parseSource(string $source, ?string $contextPath): ?array
    {
        if ($source === '' || $source[0] !== '/' || str_starts_with($source, '//')) {
            return null;
        }
        $path = parse_url($source, PHP_URL_PATH);
        if (!is_string($path)) {
            return null;
        }
        $parts = array_values(array_filter(explode('/', $path), fn ($p) => $p !== ''));
        if (($parts[0] ?? null) === 'index.php') {
            array_shift($parts);
        }
        if ($contextPath !== null && ($parts[0] ?? null) === $contextPath) {
            array_shift($parts);
        }
        $page = (string) array_shift($parts);
        $op = (string) array_shift($parts);
        return [$page, $op, $parts];
    }

    /** The host to name in the notice: "existentialanalysis.org.uk". */
    public static function siteName(string $movedTo): string
    {
        $host = (string) parse_url($movedTo, PHP_URL_HOST);
        return preg_replace('/^www\./', '', $host) ?: $movedTo;
    }

    private static function slug($value): ?string
    {
        return is_string($value) && preg_match(self::SLUG, $value) ? $value : null;
    }
}
