<?php
/**
 * Staging environment config.
 */

use Roots\WPConfig\Config;
use function Env\env;

Config::define('WP_DEBUG', false);
Config::define('WP_DEBUG_DISPLAY', false);
Config::define('WP_DEBUG_LOG', env('WP_DEBUG_LOG') ?: '/var/www/html/web/app/uploads/debug.log');
Config::define('DISALLOW_INDEXING', true);

/**
 * Fence. This WordPress is the test rig, and its sync talks to the LIVE
 * journal: `http://ojs:80` is the public journal's own container. Its members
 * are a copy of real ones from March, each linked to their real journal
 * account. So it may write to the journal only for test accounts. Reads, and
 * the daily deep monitor's own round-trip (monitor-<epoch>@test.invalid), are
 * unaffected; `wp ojs-sync fence` proves it holds. `.invalid` is a reserved
 * TLD, so no real member can be inside this fence. Hard-coded rather than
 * read from the environment; it applies whenever WP_ENV is staging, which
 * docker-compose.staging.yml pins, and the deep monitor fails if it is ever
 * off. See docs/wpojs-sync-plugin.md#fence.
 */
Config::define('WPOJS_FENCE_EMAIL_PATTERN', '/^[^@\s]+@test\.invalid\z/i');
