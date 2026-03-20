<?php

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

class WPOJS_Stats {

	/** @var WPOJS_Resolver */
	private $resolver;

	/** @var WPOJS_Logger */
	private $logger;

	public function __construct( WPOJS_Resolver $resolver, WPOJS_Logger $logger ) {
		$this->resolver = $resolver;
		$this->logger   = $logger;
	}

	/**
	 * Count of active WP members.
	 */
	public function get_active_member_count() {
		global $wpdb;
		$count = 0;

		// Count active WCS subscribers via direct DB query (avoid loading full objects).
		// Supports both HPOS (wc_orders) and legacy (wp_posts + wp_postmeta).
		$hpos_enabled = 'yes' === get_option( 'woocommerce_custom_orders_table_enabled', 'no' );
		if ( $hpos_enabled ) {
			$count += (int) $wpdb->get_var(
				"SELECT COUNT(DISTINCT customer_id)
				FROM {$wpdb->prefix}wc_orders
				WHERE type = 'shop_subscription'
				AND status = 'wc-active'"
			);
		} else {
			$count += (int) $wpdb->get_var(
				"SELECT COUNT(DISTINCT pm.meta_value)
				FROM {$wpdb->prefix}posts p
				JOIN {$wpdb->prefix}postmeta pm ON p.ID = pm.post_id AND pm.meta_key = '_customer_user'
				WHERE p.post_type = 'shop_subscription'
				AND p.post_status = 'wc-active'
				AND pm.meta_value > 0"
			);
		}

		// Count manual role members.
		$manual_roles = get_option( 'wpojs_manual_roles', array() );
		if ( ! empty( $manual_roles ) ) {
			foreach ( $manual_roles as $role ) {
				$count += count( get_users( array( 'role' => $role, 'fields' => 'ID' ) ) );
			}
		}

		return $count;
	}

	/**
	 * Count of members synced to OJS (those with _wpojs_user_id meta).
	 */
	public function get_synced_member_count() {
		global $wpdb;
		return (int) $wpdb->get_var(
			"SELECT COUNT(DISTINCT user_id) FROM {$wpdb->usermeta} WHERE meta_key = '_wpojs_user_id'"
		);
	}

	/**
	 * Count failures since a given datetime.
	 */
	public function get_failure_count_since( $datetime ) {
		return $this->logger->get_failure_count_since( $datetime );
	}

	/**
	 * Count failures in the last N hours.
	 */
	public function get_failure_count_hours( $hours = 24 ) {
		$since = gmdate( 'Y-m-d H:i:s', time() - ( $hours * HOUR_IN_SECONDS ) );
		return $this->get_failure_count_since( $since );
	}

	/**
	 * Count failures in the last N days.
	 */
	public function get_failure_count_days( $days = 7 ) {
		$since = gmdate( 'Y-m-d H:i:s', time() - ( $days * DAY_IN_SECONDS ) );
		return $this->get_failure_count_since( $since );
	}

	/**
	 * Success rate over the last N days (0-100, or null if no entries).
	 */
	public function get_success_rate_days( $days = 7 ) {
		global $wpdb;
		$table = $wpdb->prefix . 'wpojs_sync_log';
		$since = gmdate( 'Y-m-d H:i:s', time() - ( $days * DAY_IN_SECONDS ) );

		$row = $wpdb->get_row( $wpdb->prepare(
			"SELECT
				SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success_count,
				SUM(CASE WHEN status = 'fail' THEN 1 ELSE 0 END) AS fail_count
			FROM {$table}
			WHERE created_at >= %s",
			$since
		) );

		if ( ! $row ) {
			return null;
		}

		$total = (int) $row->success_count + (int) $row->fail_count;
		if ( $total === 0 ) {
			return null;
		}

		return round( ( (int) $row->success_count / $total ) * 100 );
	}

	/**
	 * Action Scheduler queue counts for wpojs-sync group.
	 *
	 * @return array { pending: int, running: int, failed: int, complete: int }
	 */
	public function get_queue_counts() {
		$counts = array(
			'pending'  => 0,
			'running'  => 0,
			'failed'   => 0,
			'complete' => 0,
		);

		if ( ! class_exists( 'ActionScheduler' ) ) {
			return $counts;
		}

		$store   = ActionScheduler::store();
		$mapping = array(
			'pending'  => ActionScheduler_Store::STATUS_PENDING,
			'running'  => ActionScheduler_Store::STATUS_RUNNING,
			'failed'   => ActionScheduler_Store::STATUS_FAILED,
			'complete' => ActionScheduler_Store::STATUS_COMPLETE,
		);

		foreach ( $mapping as $key => $status ) {
			$counts[ $key ] = (int) $store->query_actions( array(
				'status' => $status,
				'group'  => 'wpojs-sync',
			), 'count' );
		}

		return $counts;
	}

	/**
	 * Next scheduled cron times.
	 *
	 * @return array { reconcile: int|false, digest: int|false, log_cleanup: int|false }
	 */
	public function get_cron_schedule() {
		return array(
			'reconcile'   => wp_next_scheduled( 'wpojs_daily_reconcile' ),
			'digest'      => wp_next_scheduled( 'wpojs_daily_digest' ),
			'log_cleanup' => wp_next_scheduled( 'wpojs_log_cleanup' ),
		);
	}
}
