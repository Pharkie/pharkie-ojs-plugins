<?php

if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

class SEA_OJS_Activator {

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
        wp_clear_scheduled_hook( 'sea_ojs_process_queue' );
        wp_clear_scheduled_hook( 'sea_ojs_daily_reconcile' );
        wp_clear_scheduled_hook( 'sea_ojs_daily_digest' );
    }

    private static function create_tables() {
        global $wpdb;
        $charset_collate = $wpdb->get_charset_collate();

        $queue_table = $wpdb->prefix . 'sea_ojs_sync_queue';
        $log_table   = $wpdb->prefix . 'sea_ojs_sync_log';

        $sql_queue = "CREATE TABLE {$queue_table} (
            id bigint(20) unsigned NOT NULL AUTO_INCREMENT,
            wp_user_id bigint(20) unsigned NOT NULL,
            email varchar(255) NOT NULL,
            action varchar(30) NOT NULL,
            payload text NOT NULL,
            status varchar(20) NOT NULL DEFAULT 'pending',
            attempts tinyint(3) unsigned NOT NULL DEFAULT 0,
            next_retry_at datetime DEFAULT NULL,
            created_at datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            completed_at datetime DEFAULT NULL,
            PRIMARY KEY  (id),
            KEY idx_status_retry (status, next_retry_at),
            KEY idx_user_action (wp_user_id, action, status)
        ) $charset_collate;";

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
        dbDelta( $sql_queue );
        dbDelta( $sql_log );

        update_option( 'sea_ojs_db_version', SEA_OJS_VERSION );
    }

    private static function schedule_cron() {
        // Register custom 1-minute interval.
        add_filter( 'cron_schedules', array( __CLASS__, 'add_cron_schedules' ) );

        if ( ! wp_next_scheduled( 'sea_ojs_process_queue' ) ) {
            wp_schedule_event( time(), 'every_minute', 'sea_ojs_process_queue' );
        }

        if ( ! wp_next_scheduled( 'sea_ojs_daily_reconcile' ) ) {
            wp_schedule_event( time(), 'daily', 'sea_ojs_daily_reconcile' );
        }

        if ( ! wp_next_scheduled( 'sea_ojs_daily_digest' ) ) {
            wp_schedule_event( time(), 'daily', 'sea_ojs_daily_digest' );
        }
    }

    /**
     * Add custom cron schedule for every minute.
     */
    public static function add_cron_schedules( $schedules ) {
        $schedules['every_minute'] = array(
            'interval' => 60,
            'display'  => __( 'Every Minute' ),
        );
        return $schedules;
    }
}

// Keep the cron schedule registered at runtime too.
add_filter( 'cron_schedules', array( 'SEA_OJS_Activator', 'add_cron_schedules' ) );
