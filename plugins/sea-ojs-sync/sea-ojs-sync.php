<?php
/**
 * Plugin Name: SEA OJS Sync
 * Description: Syncs WooCommerce Subscription membership data to OJS journal access.
 * Version: 1.0.0
 * Author: Society for Existential Analysis
 * Requires PHP: 7.4
 * Requires at least: 5.6
 *
 * Hooks into WooCommerce Subscriptions lifecycle events and processes
 * sync operations asynchronously via Action Scheduler against the OJS
 * REST API (sea-subscription-api OJS plugin).
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

define( 'SEA_OJS_VERSION', '1.0.0' );
define( 'SEA_OJS_PLUGIN_DIR', plugin_dir_path( __FILE__ ) );
define( 'SEA_OJS_PLUGIN_URL', plugin_dir_url( __FILE__ ) );
define( 'SEA_OJS_PLUGIN_BASENAME', plugin_basename( __FILE__ ) );

// Includes.
require_once SEA_OJS_PLUGIN_DIR . 'includes/class-sea-ojs-activator.php';
require_once SEA_OJS_PLUGIN_DIR . 'includes/class-sea-ojs-api-client.php';
require_once SEA_OJS_PLUGIN_DIR . 'includes/class-sea-ojs-logger.php';
require_once SEA_OJS_PLUGIN_DIR . 'includes/class-sea-ojs-resolver.php';
require_once SEA_OJS_PLUGIN_DIR . 'includes/class-sea-ojs-sync.php';
require_once SEA_OJS_PLUGIN_DIR . 'includes/class-sea-ojs-hooks.php';
require_once SEA_OJS_PLUGIN_DIR . 'includes/class-sea-ojs-cron.php';
require_once SEA_OJS_PLUGIN_DIR . 'includes/class-sea-ojs-dashboard.php';

if ( is_admin() ) {
	require_once SEA_OJS_PLUGIN_DIR . 'includes/admin/class-sea-ojs-settings.php';
	require_once SEA_OJS_PLUGIN_DIR . 'includes/admin/class-sea-ojs-log-page.php';
}

if ( defined( 'WP_CLI' ) && WP_CLI ) {
	require_once SEA_OJS_PLUGIN_DIR . 'includes/cli/class-sea-ojs-cli.php';
}

// Activation / deactivation.
register_activation_hook( __FILE__, array( 'SEA_OJS_Activator', 'activate' ) );
register_deactivation_hook( __FILE__, array( 'SEA_OJS_Activator', 'deactivate' ) );

/**
 * Bootstrap the plugin after all plugins are loaded.
 */
function sea_ojs_init() {
	// Shared instances.
	$api_client = new SEA_OJS_API_Client();
	$logger     = new SEA_OJS_Logger();
	$resolver   = new SEA_OJS_Resolver();
	$sync       = new SEA_OJS_Sync( $api_client, $logger, $resolver );

	// Register Action Scheduler callbacks for sync actions.
	$sync->register();

	// Register WCS + profile hooks.
	$hooks = new SEA_OJS_Hooks( $resolver );
	$hooks->register();

	// Register cron handlers (reconciliation, daily digest).
	$cron = new SEA_OJS_Cron( $sync, $resolver, $api_client, $logger );
	$cron->register();

	// Member-facing dashboard widget.
	$dashboard = new SEA_OJS_Dashboard( $resolver );
	$dashboard->register();

	// Admin pages.
	if ( is_admin() ) {
		$settings = new SEA_OJS_Settings( $api_client );
		$settings->register();

		$log_page = new SEA_OJS_Log_Page( $logger );
		$log_page->register();
	}

	// WP-CLI commands.
	if ( defined( 'WP_CLI' ) && WP_CLI ) {
		SEA_OJS_CLI::register( $sync, $resolver, $api_client, $logger );
	}
}
add_action( 'plugins_loaded', 'sea_ojs_init' );
