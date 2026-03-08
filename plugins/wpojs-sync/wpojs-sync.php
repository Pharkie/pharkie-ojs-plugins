<?php
/**
 * Plugin Name: WP OJS Sync
 * Description: Syncs WooCommerce Subscription membership data to OJS journal access.
 * Version: 1.0.0
 * Author: Adam Knowles
 * Author URI: https://github.com/adamknowles
 * Requires PHP: 7.4
 * Requires at least: 5.6
 *
 * Hooks into WooCommerce Subscriptions lifecycle events and processes
 * sync operations asynchronously via Action Scheduler against the OJS
 * REST API (wpojs-subscription-api OJS plugin).
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

define( 'WPOJS_VERSION', '1.0.0' );
define( 'WPOJS_PLUGIN_DIR', plugin_dir_path( __FILE__ ) );
define( 'WPOJS_PLUGIN_URL', plugin_dir_url( __FILE__ ) );
define( 'WPOJS_PLUGIN_BASENAME', plugin_basename( __FILE__ ) );

// Includes.
require_once WPOJS_PLUGIN_DIR . 'includes/class-wpojs-activator.php';
require_once WPOJS_PLUGIN_DIR . 'includes/class-wpojs-api-client.php';
require_once WPOJS_PLUGIN_DIR . 'includes/class-wpojs-logger.php';
require_once WPOJS_PLUGIN_DIR . 'includes/class-wpojs-resolver.php';
require_once WPOJS_PLUGIN_DIR . 'includes/class-wpojs-sync.php';
require_once WPOJS_PLUGIN_DIR . 'includes/class-wpojs-hooks.php';
require_once WPOJS_PLUGIN_DIR . 'includes/class-wpojs-stats.php';
require_once WPOJS_PLUGIN_DIR . 'includes/class-wpojs-cron.php';
require_once WPOJS_PLUGIN_DIR . 'includes/class-wpojs-dashboard.php';

if ( is_admin() ) {
	require_once WPOJS_PLUGIN_DIR . 'includes/admin/class-wpojs-settings.php';
	require_once WPOJS_PLUGIN_DIR . 'includes/admin/class-wpojs-log-page.php';
	require_once WPOJS_PLUGIN_DIR . 'includes/admin/class-wpojs-log-actions.php';
}

if ( defined( 'WP_CLI' ) && WP_CLI ) {
	require_once WPOJS_PLUGIN_DIR . 'includes/cli/class-wpojs-cli.php';
}

// Activation / deactivation.
register_activation_hook( __FILE__, array( 'WPOJS_Activator', 'activate' ) );
register_deactivation_hook( __FILE__, array( 'WPOJS_Activator', 'deactivate' ) );

/**
 * Register a 'weekly' cron schedule as fallback.
 *
 * WordPress core doesn't include 'weekly' — it comes from WooCommerce.
 * If WooCommerce is deactivated before this plugin, wp_schedule_event()
 * with 'weekly' silently fails. This ensures the schedule always exists.
 */
add_filter( 'cron_schedules', 'wpojs_add_weekly_schedule' );
function wpojs_add_weekly_schedule( $schedules ) {
	if ( ! isset( $schedules['weekly'] ) ) {
		$schedules['weekly'] = array(
			'interval' => WEEK_IN_SECONDS,
			'display'  => 'Once Weekly',
		);
	}
	return $schedules;
}

/**
 * Bootstrap the plugin after all plugins are loaded.
 */
function wpojs_init() {
	// Shared instances.
	$api_client = new WPOJS_API_Client();
	$logger     = new WPOJS_Logger();
	$resolver   = new WPOJS_Resolver();
	$sync       = new WPOJS_Sync( $api_client, $logger, $resolver );
	$stats      = new WPOJS_Stats( $resolver, $logger );

	// Register Action Scheduler callbacks for sync actions.
	$sync->register();

	// Register WCS + profile hooks.
	$hooks = new WPOJS_Hooks( $resolver );
	$hooks->register();

	// Register cron handlers (reconciliation, daily digest).
	$cron = new WPOJS_Cron( $sync, $resolver, $api_client, $logger );
	$cron->register();

	// Member-facing dashboard widget.
	$dashboard = new WPOJS_Dashboard( $resolver, $logger );
	$dashboard->register();

	// Admin pages.
	if ( is_admin() ) {
		$settings = new WPOJS_Settings( $api_client, $logger );
		$settings->register();

		$log_page = new WPOJS_Log_Page( $logger, $stats );
		$log_page->register();

		$log_actions = new WPOJS_Log_Actions( $logger );
		$log_actions->register();

		// Startup config validation — warn admins about missing required config.
		$missing_config = [];
		if ( ! defined( 'WPOJS_API_KEY' ) ) {
			$missing_config[] = 'WPOJS_API_KEY constant (wp-config.php)';
		}
		if ( ! get_option( 'wpojs_url' ) ) {
			$missing_config[] = 'OJS URL (Settings → OJS Sync)';
		}
		if ( $missing_config && ! wp_doing_ajax() ) {
			add_action( 'admin_notices', function () use ( $missing_config ) {
				echo '<div class="notice notice-error"><p><strong>OJS Sync:</strong> Missing configuration: ' . esc_html( implode( ', ', $missing_config ) ) . '</p></div>';
			} );
		}
	}

	// WP-CLI commands.
	if ( defined( 'WP_CLI' ) && WP_CLI ) {
		WPOJS_CLI::register( $sync, $resolver, $api_client, $logger, $stats );
	}
}
add_action( 'plugins_loaded', 'wpojs_init' );
