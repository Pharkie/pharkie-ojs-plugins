<?php

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

class WPOJS_Activator {

	/**
	 * Plugin activation: create DB tables, schedule cron events.
	 */
	public static function activate() {
		self::create_tables();
		self::schedule_cron();
	}

	/**
	 * Plugin deactivation: unschedule cron events (do NOT drop tables).
	 */
	public static function deactivate() {
		wp_clear_scheduled_hook( 'wpojs_daily_reconcile' );
		wp_clear_scheduled_hook( 'wpojs_daily_digest' );
		wp_clear_scheduled_hook( 'wpojs_log_cleanup' );
	}

	private static function create_tables() {
		global $wpdb;
		$charset_collate = $wpdb->get_charset_collate();

		$log_table = $wpdb->prefix . 'wpojs_sync_log';

		$sql_log = "CREATE TABLE {$log_table} (
			id bigint(20) unsigned NOT NULL AUTO_INCREMENT,
			wp_user_id bigint(20) unsigned NOT NULL,
			email varchar(255) NOT NULL,
			action varchar(30) NOT NULL,
			status varchar(10) NOT NULL,
			ojs_response_code smallint(5) unsigned DEFAULT NULL,
			ojs_response_body text DEFAULT NULL,
			attempt_count tinyint(3) unsigned NOT NULL DEFAULT 1,
			created_at datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
			PRIMARY KEY  (id),
			KEY idx_status (status),
			KEY idx_email (email),
			KEY idx_created (created_at)
		) $charset_collate;";

		require_once ABSPATH . 'wp-admin/includes/upgrade.php';
		dbDelta( $sql_log );

		update_option( 'wpojs_db_version', WPOJS_VERSION );
	}

	private static function schedule_cron() {
		if ( ! wp_next_scheduled( 'wpojs_daily_reconcile' ) ) {
			wp_schedule_event( time(), 'daily', 'wpojs_daily_reconcile' );
		}

		if ( ! wp_next_scheduled( 'wpojs_daily_digest' ) ) {
			wp_schedule_event( time(), 'daily', 'wpojs_daily_digest' );
		}

		if ( ! wp_next_scheduled( 'wpojs_log_cleanup' ) ) {
			wp_schedule_event( time(), 'weekly', 'wpojs_log_cleanup' );
		}
	}
}
