<?php

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

class WPOJS_Cron {

	/** @var WPOJS_Sync */
	private $sync;

	/** @var WPOJS_Resolver */
	private $resolver;

	/** @var WPOJS_API_Client */
	private $api;

	/** @var WPOJS_Logger */
	private $logger;

	public function __construct( WPOJS_Sync $sync, WPOJS_Resolver $resolver, WPOJS_API_Client $api, WPOJS_Logger $logger ) {
		$this->sync     = $sync;
		$this->resolver = $resolver;
		$this->api      = $api;
		$this->logger   = $logger;
	}

	/**
	 * Register cron action hooks.
	 *
	 * Note: queue processing is handled by Action Scheduler automatically.
	 * We only need cron for reconciliation and the daily digest.
	 */
	public function register() {
		add_action( 'wpojs_daily_reconcile', array( $this, 'daily_reconcile' ) );
		add_action( 'wpojs_daily_digest', array( $this, 'daily_digest' ) );
		add_action( 'wpojs_log_cleanup', array( $this, 'log_cleanup' ) );
	}

	/**
	 * Daily reconciliation: compare WP active members vs OJS subscriptions.
	 * Schedule any drift (missing or expired subscriptions on OJS).
	 *
	 * Two checks:
	 * 1. Missing access: active WP members without active OJS subscriptions -> schedule activate.
	 * 2. Stale access: synced users who are no longer active WP members -> schedule expire.
	 */
	public function daily_reconcile() {
		$active_members = $this->resolver->get_all_active_members();
		$queued  = 0;
		$expired = 0;
		$errors  = 0;

		// Build email→user_id map for all active members.
		$email_to_user_id = array();
		foreach ( $active_members as $wp_user_id ) {
			$user = get_userdata( $wp_user_id );
			if ( $user ) {
				$email_to_user_id[ $user->user_email ] = $wp_user_id;
			}
		}

		// Missing access check: batch-query OJS for subscription status.
		$email_chunks = array_chunk( array_keys( $email_to_user_id ), 100 );
		foreach ( $email_chunks as $chunk ) {
			$result = $this->api->get_subscription_status_batch( $chunk );

			if ( ! $result['success'] ) {
				$errors += count( $chunk );
				continue;
			}

			$statuses = isset( $result['body']['results'] ) ? $result['body']['results'] : array();

			foreach ( $chunk as $email ) {
				$wp_user_id = $email_to_user_id[ $email ];
				$has_active = isset( $statuses[ $email ]['active'] ) && $statuses[ $email ]['active'];

				if ( ! $has_active ) {
					$args = array( array( 'wp_user_id' => $wp_user_id ) );
					if ( ! as_has_scheduled_action( 'wpojs_sync_activate', $args, 'wpojs-sync' ) ) {
						as_schedule_single_action( time(), 'wpojs_sync_activate', $args, 'wpojs-sync' );
						$this->logger->log( $wp_user_id, $email, 'reconcile_activate', 'queued', 0, 'Active member missing OJS subscription' );
						$queued++;
					}
				}
			}
		}

		// Stale access check: find synced users who are no longer active WP members.
		global $wpdb;
		$synced_users = $wpdb->get_col(
			"SELECT user_id FROM {$wpdb->usermeta} WHERE meta_key = '_wpojs_user_id'"
		);
		$active_set = array_flip( $active_members );
		foreach ( $synced_users as $uid ) {
			$uid = (int) $uid;
			if ( ! isset( $active_set[ $uid ] ) ) {
				// This user was synced but is no longer active -- schedule expire.
				$args = array( array( 'wp_user_id' => $uid ) );
				if ( ! as_has_scheduled_action( 'wpojs_sync_expire', $args, 'wpojs-sync' ) ) {
					as_schedule_single_action( time(), 'wpojs_sync_expire', $args, 'wpojs-sync' );
					$this->logger->log( $uid, '', 'reconcile_expire', 'queued', 0, 'Stale access: synced user no longer active member' );
					$expired++;
				}
			}
		}

		$this->logger->log(
			0,
			'system',
			'reconcile',
			'success',
			0,
			sprintf(
				'Checked %d members, queued %d activations, queued %d expirations, %d API errors',
				count( $active_members ),
				$queued,
				$expired,
				$errors
			)
		);
	}

	/**
	 * Weekly log cleanup: delete log entries older than 90 days.
	 */
	public function log_cleanup() {
		$this->logger->cleanup_old( 90 );
	}

	/**
	 * Daily digest: email admin if there were failures in the last 24 hours.
	 */
	public function daily_digest() {
		$since = gmdate( 'Y-m-d H:i:s', time() - DAY_IN_SECONDS );
		$count = $this->logger->get_failure_count_since( $since );

		if ( $count === 0 ) {
			return; // Skip if no failures.
		}

		// Query Action Scheduler for pending/failed counts.
		$pending_count = 0;
		$failed_count  = 0;
		if ( class_exists( 'ActionScheduler' ) ) {
			$store         = ActionScheduler::store();
			$pending_count = (int) $store->query_actions( array( 'status' => ActionScheduler_Store::STATUS_PENDING, 'group' => 'wpojs-sync' ), 'count' );
			$failed_count  = (int) $store->query_actions( array( 'status' => ActionScheduler_Store::STATUS_FAILED, 'group' => 'wpojs-sync' ), 'count' );
		}

		$to      = get_option( 'admin_email' );
		$subject = sprintf( 'OJS Sync Daily Digest: %d failure(s) in the last 24 hours', $count );
		$message = sprintf(
			"OJS Sync Daily Digest\n" .
			"=====================\n\n" .
			"Failures in last 24 hours: %d\n\n" .
			"Action Scheduler queue:\n" .
			"  Pending: %d\n" .
			"  Failed: %d\n\n" .
			"Review failures: %s\n" .
			"View scheduled actions: %s",
			$count,
			$pending_count,
			$failed_count,
			admin_url( 'admin.php?page=wpojs-sync-log&status=fail' ),
			admin_url( 'admin.php?page=action-scheduler&status=pending&group=wpojs-sync' )
		);

		wp_mail( $to, $subject, $message );
	}
}
