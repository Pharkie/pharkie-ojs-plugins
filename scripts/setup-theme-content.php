<?php
/**
 * Set up theme content: navigation menus and static front page.
 * Matches live site (community.existentialanalysis.org.uk).
 * Idempotent — safe to run repeatedly.
 *
 * Run inside WP container:
 *   wp eval-file /var/www/html/scripts/setup-theme-content.php --allow-root
 */

// --- 1. Create "mainnav" menu (referenced by Gantry layout config) ---
$menu_slug = 'mainnav';
$menu_name = 'Main Navigation';
$menu = wp_get_nav_menu_object($menu_slug);

if (!$menu) {
    $menu_id = wp_create_nav_menu($menu_name);
    if (is_wp_error($menu_id)) {
        echo "ERROR: Could not create menu: " . $menu_id->get_error_message() . "\n";
    } else {
        // Set the slug explicitly (wp_create_nav_menu uses the name as slug).
        wp_update_term($menu_id, 'nav_menu', ['slug' => $menu_slug]);
        echo "[ok] Menu '$menu_name' created (slug: $menu_slug).\n";
    }
} else {
    $menu_id = $menu->term_id;
    echo "[ok] Menu '$menu_name' already exists.\n";
}

// Only add items if the menu is empty (idempotent).
$existing_items = wp_get_nav_menu_items($menu_id);
if (empty($existing_items)) {
    // Menu structure from live site (wp menu item list mainnav, 2026-03-10).
    // External links use full URLs. Internal pages use relative paths (resolved
    // to home_url at insert time). Pages that don't exist on dev/staging use '#'.
    $main_site = 'https://existentialanalysis.org.uk';
    $items = [
        ['title' => 'SEA Events', 'url' => '/events/'],
        ['title' => 'Membership', 'url' => '/sea-membership', 'children' => [
            ['title' => 'UK Membership', 'url' => '/product/uk-membership'],
            ['title' => 'International Membership', 'url' => '/product/international-membership'],
            ['title' => 'Student Membership', 'url' => '/product/sea-student-membership'],
        ]],
        ['title' => 'Members directory', 'url' => '/members-directory'],
        ['title' => 'Publications', 'url' => '/publications', 'children' => [
            ['title' => 'Journal', 'url' => '/journal', 'children' => [
                ['title' => 'Buy PDF journals', 'url' => '/product-category/pdf'],
                ['title' => 'Index of journals', 'url' => '/index-of-journals'],
                ['title' => 'Restricted content', 'url' => '/read-journals-online'],
            ]],
            ['title' => 'Hermeneutic Circular', 'url' => '/hermeneutic-circular', 'children' => [
                ['title' => 'Read Hermeneutic Circular issues online', 'url' => '/read-hermeneutic-circular-online'],
            ]],
            ['title' => 'Dialogues', 'url' => "$main_site/publications/sea-dialogues/"],
        ]],
        ['title' => 'UKCP Registration', 'url' => "$main_site/ukcp-registration-through-the-sea/"],
        ['title' => 'My account', 'url' => '/account'],
        ['title' => 'Contact the SEA', 'url' => '/contact-the-sea'],
        ['title' => 'Donate', 'url' => '/donate'],
    ];

    $home_url = home_url();

    // Recursive function to add menu items.
    $add_items = function ($items, $parent_id = 0) use (&$add_items, $menu_id, $home_url) {
        $position = 0;
        foreach ($items as $item) {
            $position++;
            $url = $item['url'];
            // Convert relative URLs to absolute.
            if (strpos($url, '/') === 0) {
                $url = $home_url . $url;
            }

            $item_id = wp_update_nav_menu_item($menu_id, 0, [
                'menu-item-title'   => $item['title'],
                'menu-item-url'     => $url,
                'menu-item-status'  => 'publish',
                'menu-item-type'    => 'custom',
                'menu-item-parent-id' => $parent_id,
                'menu-item-position'  => $position,
            ]);

            if (is_wp_error($item_id)) {
                echo "  ERROR adding '{$item['title']}': " . $item_id->get_error_message() . "\n";
                continue;
            }

            if (!empty($item['children'])) {
                $add_items($item['children'], $item_id);
            }
        }
    };

    $add_items($items);

    // Add Gantry5 JL Search particle as the last menu item (search icon in navbar).
    // On live this is a "particle" menu item — Gantry reads _menu_item_gantry5 postmeta
    // and renders the particle inline. The URL '#' is a placeholder (Gantry ignores it).
    $search_item_id = wp_update_nav_menu_item($menu_id, 0, [
        'menu-item-title'    => 'JL Search',
        'menu-item-url'      => '#',
        'menu-item-status'   => 'publish',
        'menu-item-type'     => 'custom',
        'menu-item-parent-id' => 0,
        'menu-item-position' => 19,
    ]);
    if (!is_wp_error($search_item_id)) {
        update_post_meta($search_item_id, '_menu_item_gantry5', json_encode([
            'type'     => 'particle',
            'particle' => 'jlsearch',
            'options'  => [
                'particle' => [
                    'enabled'      => '1',
                    'title'        => 'Search Form',
                    'placeholder'  => 'Type your search term here...',
                    'search_style' => 'modal',
                    'search_icon'  => 'right',
                ],
                'block' => ['extra' => []],
            ],
        ]));
    }

    $count = count(wp_get_nav_menu_items($menu_id));
    echo "[ok] $count menu items added.\n";
} else {
    echo "[ok] Menu already has " . count($existing_items) . " items, skipping.\n";
}

// --- 2. Create static front page (content from live site, page ID 667) ---
$front_slug = 'society-for-existential-analysis-community-site';
$front_page = get_page_by_path($front_slug);

if (!$front_page) {
    // Exact content from live wp post get 667, 2026-03-10.
    $content = '<p>The Society for Existential Analysis Community site is the place to view and book upcoming '
        . '<a href="/events">SEA Event tickets</a>, join or renew your '
        . '<a href="/sea-membership">SEA Membership</a> and browse and purchase back copies of the SEA in-house '
        . '<a href="/sea-journals">Journal</a>: Existential Analysis.</p>' . "\n\n"
        . '<p>Already an SEA Member? <a href="/my-account">Log in to your account</a> '
        . 'to view your member discounts and content.</p>';

    $page_id = wp_insert_post([
        'post_title'   => 'The Society For Existential Analysis Community',
        'post_name'    => $front_slug,
        'post_content' => $content,
        'post_status'  => 'publish',
        'post_type'    => 'page',
        'post_author'  => 1,
    ]);

    if (is_wp_error($page_id)) {
        echo "ERROR: Could not create front page: " . $page_id->get_error_message() . "\n";
    } else {
        echo "[ok] Front page created (ID: $page_id).\n";
    }
} else {
    $page_id = $front_page->ID;
    echo "[ok] Front page already exists (ID: $page_id).\n";
}

// Set as static front page (live has page_for_posts = 0, no separate blog page).
update_option('show_on_front', 'page');
update_option('page_on_front', $page_id);
echo "[ok] Static front page set.\n";
