<?php
/**
 * Bedrock main configuration.
 *
 * Reads all settings from .env — no environment-specific values hardcoded here.
 * Environment overrides live in config/environments/{WP_ENV}.php.
 */

use Roots\WPConfig\Config;
use function Env\env;

/** Directory containing all of the site's files */
$root_dir = dirname(__DIR__);

/** Document root (web/) */
$webroot_dir = $root_dir . '/web';

/**
 * Load .env file
 */
if (file_exists($root_dir . '/.env')) {
    $dotenv = Dotenv\Dotenv::createUnsafeImmutable($root_dir);
    $dotenv->load();
    $dotenv->required(['DB_NAME', 'DB_USER', 'DB_PASSWORD', 'WP_HOME', 'WP_SITEURL']);
}

/**
 * Set environment
 */
$env = env('WP_ENV') ?: 'production';

/**
 * Reverse proxy — trust X-Forwarded-Proto from Caddy so WP knows
 * the request arrived over HTTPS even though it sees plain HTTP.
 */
if (isset($_SERVER['HTTP_X_FORWARDED_PROTO']) && $_SERVER['HTTP_X_FORWARDED_PROTO'] === 'https') {
    $_SERVER['HTTPS'] = 'on';
}

/**
 * URLs
 */
Config::define('WP_HOME', env('WP_HOME'));
Config::define('WP_SITEURL', env('WP_SITEURL'));

/**
 * Custom content directory
 */
Config::define('CONTENT_DIR', '/app');
Config::define('WP_CONTENT_DIR', $webroot_dir . '/app');
Config::define('WP_CONTENT_URL', Config::get('WP_HOME') . '/app');

/**
 * Database
 */
Config::define('DB_NAME', env('DB_NAME'));
Config::define('DB_USER', env('DB_USER'));
Config::define('DB_PASSWORD', env('DB_PASSWORD'));
Config::define('DB_HOST', env('DB_HOST') ?: 'localhost');
Config::define('DB_CHARSET', 'utf8mb4');
Config::define('DB_COLLATE', '');
Config::define('MYSQL_CLIENT_FLAGS', 0);
$table_prefix = env('DB_PREFIX') ?: 'wp_';

/**
 * Authentication unique keys and salts
 */
Config::define('AUTH_KEY', env('AUTH_KEY'));
Config::define('SECURE_AUTH_KEY', env('SECURE_AUTH_KEY'));
Config::define('LOGGED_IN_KEY', env('LOGGED_IN_KEY'));
Config::define('NONCE_KEY', env('NONCE_KEY'));
Config::define('AUTH_SALT', env('AUTH_SALT'));
Config::define('SECURE_AUTH_SALT', env('SECURE_AUTH_SALT'));
Config::define('LOGGED_IN_SALT', env('LOGGED_IN_SALT'));
Config::define('NONCE_SALT', env('NONCE_SALT'));

/**
 * WP-OJS integration
 */
Config::define('WPOJS_API_KEY', env('WPOJS_API_KEY'));

/**
 * WordPress settings
 */
Config::define('AUTOMATIC_UPDATER_DISABLED', true);
Config::define('DISABLE_WP_CRON', env('DISABLE_WP_CRON') ?: false);
Config::define('DISALLOW_FILE_EDIT', true);
Config::define('DISALLOW_FILE_MODS', true);
Config::define('FS_METHOD', 'direct');

/**
 * Debugging — overridden per environment
 */
Config::define('WP_DEBUG', false);
Config::define('WP_DEBUG_DISPLAY', false);
Config::define('WP_DEBUG_LOG', false);
Config::define('SCRIPT_DEBUG', false);
ini_set('display_errors', '0');

/**
 * Load environment-specific config
 */
$env_config = __DIR__ . '/environments/' . $env . '.php';
if (file_exists($env_config)) {
    require_once $env_config;
}

Config::apply();

/** Absolute path to the WordPress directory */
if (!defined('ABSPATH')) {
    define('ABSPATH', $webroot_dir . '/wp/');
}
