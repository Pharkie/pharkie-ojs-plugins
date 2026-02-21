<?php

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

class SEA_OJS_Cron {

	/** @var SEA_OJS_Sync */
	private $sync;

	/** @var SEA_OJS_Resolver */
	private $resolver;

	/** @var SEA_OJS_API_Client */
	private $api;

	/** @var SEA_OJS_Logger */
	private $logger;

	public function __construct( SEA_OJS_Sync $sync, SEA_OJS_Resolver $resolver, SEA_OJS_API_Client $api, SEA_OJS_Logger $logger ) {
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
		add_action( 'sea_ojs_daily_reconcile', array( $this, 'daily_reconcile' ) );
		add_action( 'sea_ojs_daily_digest', array( $this, 'daily_digest' ) );
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

		// Missing access check: ensure every active WP member has an active OJS subscription.
		foreach ( $active_members as $wp_user_id ) {
			$user = get_userdata( $wp_user_id );
			if ( ! $user ) {
				continue;
			}

			$email = $user->user_email;

			// Check OJS subscription status.
			$result = $this->api->get_subscriptions( array( 'email' => $email ) );

			if ( ! $result['success'] ) {
				$errors++;
				usleep( 100000 ); // 100ms delay between API calls.
				continue;
			}

			$subscriptions = $result['body'];

			// If no active subscription on OJS, schedule an activate.
			$has_active = false;
			if ( is_array( $subscriptions ) ) {
				foreach ( $subscriptions as $sub ) {
					if ( isset( $sub['status'] ) && (int) $sub['status'] === 1 ) {
						$has_active = true;
						break;
					}
				}
			}

			if ( ! $has_active ) {
				$args = array( 'wp_user_id' => $wp_user_id );
				if ( ! as_has_scheduled_action( 'sea_ojs_sync_activate', $args, 'sea-ojs-sync' ) ) {
					as_schedule_single_action( time(), 'sea_ojs_sync_activate', $args, 'sea-ojs-sync' );
					$this->logger->log( $wp_user_id, $email, 'reconcile_activate', 'queued', 0, 'Active member missing OJS subscription' );
					$queued++;
				}
			}

			usleep( 100000 ); // 100ms delay between API calls.
		}

		// Stale access check: find synced users who are no longer active WP members.
		global $wpdb;
		$synced_users = $wpdb->get_col(
			"SELECT user_id FROM {$wpdb->usermeta} WHERE meta_key = '_sea_ojs_user_id'"
		);
		$active_set = array_flip( $active_members );
		foreach ( $synced_users as $uid ) {
			$uid = (int) $uid;
			if ( ! isset( $active_set[ $uid ] ) ) {
				// This user was synced but is no longer active -- schedule expire.
				$args = array( 'wp_user_id' => $uid );
				if ( ! as_has_scheduled_action( 'sea_ojs_sync_expire', $args, 'sea-ojs-sync' ) ) {
					as_schedule_single_action( time(), 'sea_ojs_sync_expire', $args, 'sea-ojs-sync' );
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
			$pending_count = (int) $store->query_actions_count_by_status( ActionScheduler_Store::STATUS_PENDING, 'sea-ojs-sync' );
			$failed_count  = (int) $store->query_actions_count_by_status( ActionScheduler_Store::STATUS_FAILED, 'sea-ojs-sync' );
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
			admin_url( 'admin.php?page=sea-ojs-sync-log&status=fail' ),
			admin_url( 'admin.php?page=action-scheduler&status=pending&group=sea-ojs-sync' )
		);

		wp_mail( $to, $subject, $message );
	}
}
