<?php

/**
 * Archive mode's routing rules and bar insertion, tested without OJS.
 *
 *   php plugins/archive-mode/tests/routes-test.php
 *
 * Exits non-zero on the first failure. ArchiveModePlugin::insertBar is
 * static and touches nothing of OJS's, so its class is loaded with stubs for
 * the two base classes it names.
 */

namespace PKP\plugins {
    if (!class_exists(GenericPlugin::class)) {
        class GenericPlugin {}
    }
}

namespace {
    require __DIR__ . '/../ArchiveModeRoutes.php';
    require __DIR__ . '/../ArchiveModePlugin.php';

    use APP\plugins\generic\archiveMode\ArchiveModePlugin;
    use APP\plugins\generic\archiveMode\ArchiveModeRoutes as R;

    $failures = 0;
    function check(string $name, $got, $want): void
    {
        global $failures;
        if ($got === $want) {
            echo "  ok   {$name}\n";
            return;
        }
        $failures++;
        echo "  FAIL {$name}\n       got:  " . var_export($got, true) . "\n       want: " . var_export($want, true) . "\n";
    }

    echo "Closed routes\n";
    foreach ([
        ['login', ''], ['login', 'index'], ['login', 'signIn'], ['login', 'lostPassword'],
        ['login', 'requestResetPassword'], ['login', 'resetPassword'], ['login', 'changePassword'],
        ['login', 'savePassword'], ['login', 'updateResetPassword'], ['login', 'signInAsUser'],
        ['user', ''], ['user', 'register'], ['user', 'registerUser'], ['user', 'activateUser'],
        ['user', 'validate'], ['user', 'profile'], ['user', 'subscriptions'], ['user', 'purchaseSubscription'],
        ['user', 'payPurchaseSubscription'], ['user', 'payRenewSubscription'], ['user', 'payMembership'],
        ['user', 'completePurchaseSubscription'],
        ['payment', 'pay'], ['about', 'subscriptions'], ['about', 'submissions'], ['information', 'readers'],
        ['advertisers', ''], ['advertisers', 'index'],
    ] as [$page, $op]) {
        check("{$page}/{$op} is closed", R::isClosed($page, $op, []), true);
    }
    check('payment/plugin/StripePayment (no op) is closed', R::isClosed('payment', 'plugin', ['StripePayment']), true);
    check('payment/plugin/OtherPlugin/webhook is closed', R::isClosed('payment', 'plugin', ['OtherPlugin', 'webhook']), true);

    echo "Open routes\n";
    foreach ([
        ['', ''], ['index', ''], ['issue', 'current'], ['issue', 'archive'], ['issue', 'view'],
        ['article', 'view'], ['article', 'download'], ['search', 'search'], ['about', ''],
        ['about', 'contact'], ['about', 'editorialMasthead'], ['about', 'privacy'],
        ['information', 'authors'], ['information', 'librarians'], ['oai', ''],
        ['login', 'signOut'], ['login', 'signOutAsUser'],
    ] as [$page, $op]) {
        check("{$page}/{$op} stays open", R::isClosed($page, $op, []), false);
    }
    check('Stripe webhook stays open', R::isClosed('payment', 'plugin', ['StripePayment', 'webhook']), false);
    check('Stripe return stays open', R::isClosed('payment', 'plugin', ['StripePayment', 'return']), false);

    echo "Sidebar\n";
    $custom = ['advertisers-link', 'banner', 'sea-events-banner'];
    check('custom blocks go, reading blocks stay in order',
        R::readingSidebar(['informationblockplugin', 'advertisers-link', 'banner', 'browseblockplugin', 'sea-events-banner', 'languagetoggleblockplugin'], $custom),
        ['informationblockplugin', 'browseblockplugin', 'languagetoggleblockplugin']);
    check('live sidebar: Information stays, For Advertisers goes',
        R::readingSidebar(['informationblockplugin', 'advertisers-link'], ['advertisers-link']), ['informationblockplugin']);
    check('Make a Submission and Subscription blocks go',
        R::readingSidebar(['makesubmissionblockplugin', 'informationblockplugin', 'subscriptionblockplugin'], []), ['informationblockplugin']);
    check('a sidebar of offers only ends empty', R::readingSidebar(['banner'], $custom), []);
    check('no custom blocks registered', R::readingSidebar(['informationblockplugin'], []), ['informationblockplugin']);

    echo "Destinations\n";
    $home = 'https://existentialanalysis.org.uk/journal';
    $map = [
        'articles' => ['9821' => 'the-twelve-day-war-experience', '9000' => 'Bad Slug!', '9001' => 42],
        'issues' => ['477' => 'vol-37-no-2'],
    ];
    check('mapped article', R::destination($home, 'article', 'view', ['9821'], $map), ['article', "{$home}/the-twelve-day-war-experience"]);
    check('mapped article galley', R::destination($home, 'article', 'view', ['9821', '41712'], $map), ['article', "{$home}/the-twelve-day-war-experience"]);
    check('mapped article download', R::destination($home, 'article', 'download', ['9821', '41712'], $map), ['article', "{$home}/the-twelve-day-war-experience"]);
    check('unmapped article', R::destination($home, 'article', 'view', ['1'], $map), ['journal', $home]);
    check('article with a bad slug in the map', R::destination($home, 'article', 'view', ['9000'], $map), ['journal', $home]);
    check('article with a non-string slug', R::destination($home, 'article', 'view', ['9001'], $map), ['journal', $home]);
    check('no map at all', R::destination($home, 'article', 'view', ['9821'], []), ['journal', $home]);
    check('mapped issue', R::destination($home, 'issue', 'view', ['477'], $map), ['issue', "{$home}/issues/vol-37-no-2"]);
    check('mapped issue galley', R::destination($home, 'issue', 'view', ['477', '409'], $map), ['issue', "{$home}/issues/vol-37-no-2"]);
    check('unmapped issue', R::destination($home, 'issue', 'view', ['1'], $map), ['journal', "{$home}/issues"]);
    check('archive', R::destination($home, 'issue', 'archive', [], $map), ['journal', "{$home}/issues"]);
    check('about', R::destination($home, 'about', '', [], $map), ['journal', "{$home}/about"]);
    check('about/submissions', R::destination($home, 'about', 'submissions', [], $map), ['journal', "{$home}/for-contributors"]);
    check('masthead', R::destination($home, 'about', 'editorialMasthead', [], $map), ['journal', "{$home}/editorial-board"]);
    check('search with words', R::destination($home, 'search', 'search', [], $map, ['query' => 'death anxiety']), ['journal', "{$home}?q=death%20anxiety"]);
    check('search without words', R::destination($home, 'search', 'search', [], $map, ['query' => '']), ['journal', $home]);
    check('home page', R::destination($home, '', '', [], $map), ['journal', $home]);
    check('trailing slash on moved_to', R::destination("{$home}/", 'article', 'view', ['9821'], $map), ['article', "{$home}/the-twelve-day-war-experience"]);

    echo "Sources\n";
    check('source path', R::parseSource('/ea/article/view/9821/41712', 'ea'), ['article', 'view', ['9821', '41712']]);
    check('source with index.php', R::parseSource('/index.php/ea/issue/view/477', 'ea'), ['issue', 'view', ['477']]);
    check('source with a query', R::parseSource('/ea/article/view/9821?x=1', 'ea'), ['article', 'view', ['9821']]);
    check('absolute URL is refused', R::parseSource('https://evil.example/ea/article/view/1', 'ea'), null);
    check('protocol-relative is refused', R::parseSource('//evil.example/x', 'ea'), null);
    check('empty is refused', R::parseSource('', 'ea'), null);

    echo "Site name\n";
    check('apex', R::siteName($home), 'existentialanalysis.org.uk');
    check('www dropped', R::siteName('https://www.example.org/journal'), 'example.org');

    echo "Bar insertion\n";
    $page = '<html><head><title>t</title></head><body class="pkp_page_index pkp_op_index" dir="ltr"><div class="pkp_structure_page">x</div></body></html>';
    $out = ArchiveModePlugin::insertBar($page, '<div class="archive-mode-bar">BAR</div>', 'CSS', false);
    check('bar straight after <body>', str_contains($out, 'dir="ltr"><div class="archive-mode-bar">BAR</div><div class="pkp_structure_page">'), true);
    check('css before </head>', str_contains($out, '<style id="archive-mode-css">CSS</style></head>'), true);
    check('one bar only, however often it runs', substr_count(ArchiveModePlugin::insertBar($out, '<div class="archive-mode-bar">BAR</div>', 'CSS', false), 'archive-mode-bar'), 1);
    check('fragment untouched', ArchiveModePlugin::insertBar('<div>fragment</div>', 'BAR', 'CSS', false), '<div>fragment</div>');
    $viewer = ArchiveModePlugin::insertBar('<head></head><body class="pkp_page_article pkp_op_view"><header class="header_view"></header></body>', '<div class="archive-mode-bar">B</div>', 'C', true);
    check('viewer body gets its class', str_contains($viewer, '<body class="archive-mode-viewer pkp_page_article pkp_op_view"><div class="archive-mode-bar">B</div><header'), true);
    $bare = ArchiveModePlugin::insertBar('<head></head><body><p>x</p></body>', '<div class="archive-mode-bar">B</div>', 'C', true);
    check('viewer body without a class', str_contains($bare, '<body class="archive-mode-viewer"><div class="archive-mode-bar">B</div>'), true);

    echo $failures ? "\n{$failures} FAILED\n" : "\nAll passed\n";
    exit($failures ? 1 : 0);
}
