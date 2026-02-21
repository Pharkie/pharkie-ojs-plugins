<?php

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

class SEA_OJS_CLI {

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
	 * Register WP-CLI commands.
	 */
	public static function register( SEA_OJS_Sync $sync, SEA_OJS_Resolver $resolver, SEA_OJS_API_Client $api, SEA_OJS_Logger $logger ) {
		$instance = new self( $sync, $resolver, $api, $logger );
		WP_CLI::add_command( 'sea-ojs sync', array( $instance, 'sync' ) );
		WP_CLI::add_command( 'sea-ojs send-welcome-emails', array( $instance, 'send_welcome_emails' ) );
		WP_CLI::add_command( 'sea-ojs reconcile', array( $instance, 'reconcile' ) );
		WP_CLI::add_command( 'sea-ojs status', array( $instance, 'status' ) );
		WP_CLI::add_command( 'sea-ojs test-connection', array( $instance, 'test_connection' ) );
	}

	/**
	 * Bulk sync members to OJS, or sync a single user.
	 *
	 * ## OPTIONS
	 *
	 * [--dry-run]
	 * : Report what would happen without making changes.
	 *
	 * [--user=<id-or-email>]
	 * : Sync a single user by WP user ID or email.
	 *
	 * ## EXAMPLES
	 *
	 *     wp sea-ojs sync --dry-run
	 *     wp sea-ojs sync
	 *     wp sea-ojs sync --user=42
	 *     wp sea-ojs sync --user=member@example.com
	 *
	 * @param array $args
	 * @param array $assoc_args
	 */
	public function sync( $args, $assoc_args ) {
		$dry_run = isset( $assoc_args['dry-run'] );

		// Single user sync.
		if ( isset( $assoc_args['user'] ) ) {
			$this->sync_single_user( $assoc_args['user'], $dry_run );
			return;
		}

		// Bulk sync.
		$this->sync_bulk( $dry_run );
	}

	private function sync_single_user( $user_ref, $dry_run ) {
		if ( is_numeric( $user_ref ) ) {
			$user = get_userdata( (int) $user_ref );
		} else {
			$user = get_user_by( 'email', $user_ref );
		}

		if ( ! $user ) {
			WP_CLI::error( 'User not found: ' . $user_ref );
		}

		// Single-user sync sends welcome email (it's a targeted action).
		$result = $this->sync->sync_user( $user->ID, $dry_run, true );

		if ( $result['success'] ) {
			WP_CLI::success( $result['message'] );
		} else {
			WP_CLI::error( $result['message'] );
		}
	}

	private function sync_bulk( $dry_run ) {
		$members = $this->resolver->get_all_active_members();
		$total   = count( $members );

		if ( $total === 0 ) {
			WP_CLI::warning( 'No active members found.' );
			return;
		}

		WP_CLI::log( sprintf( 'Found %d active members.', $total ) );

		if ( $dry_run ) {
			WP_CLI::log( 'Dry run -- no changes will be made.' );
		}

		$batch_size = 50;
		$delay_ms   = 500000; // 500ms in microseconds.
		$success    = 0;
		$skipped    = 0;
		$failed     = 0;

		$progress = \WP_CLI\Utils\make_progress_bar( 'Syncing members', $total );

		foreach ( $members as $index => $wp_user_id ) {
			// Bulk sync does NOT send welcome emails -- use send-welcome-emails after verifying.
			$result = $this->sync->sync_user( $wp_user_id, $dry_run, false );

			if ( $result['success'] ) {
				$success++;
				if ( $dry_run ) {
					WP_CLI::log( '  ' . $result['message'] );
				}
			} else {
				if ( strpos( $result['message'], 'not an active member' ) !== false ) {
					$skipped++;
				} else {
					$failed++;
					WP_CLI::warning( sprintf( 'User #%d: %s', $wp_user_id, $result['message'] ) );
				}
			}

			$progress->tick();

			// Delay between API calls (not on dry run).
			if ( ! $dry_run && ( $index + 1 ) % $batch_size === 0 ) {
				WP_CLI::log( sprintf( '  Batch %d complete. Pausing...', ceil( ( $index + 1 ) / $batch_size ) ) );
			}
			if ( ! $dry_run ) {
				usleep( $delay_ms );
			}
		}

		$progress->finish();

		WP_CLI::log( '' );
		WP_CLI::log( sprintf( 'Results: %d synced, %d skipped, %d failed out of %d total.', $success, $skipped, $failed, $total ) );

		if ( $failed > 0 ) {
			WP_CLI::warning( sprintf( '%d members failed to sync. Check the sync log for details.', $failed ) );
		} else {
			WP_CLI::success( 'Bulk sync complete. Run "wp sea-ojs send-welcome-emails" to send invite emails.' );
		}
	}

	/**
	 * Send welcome ("set your password") emails to synced members.
	 *
	 * Sends to all users with a cached _sea_ojs_user_id (i.e. successfully synced).
	 * OJS dedup prevents duplicate emails -- safe to run multiple times.
	 *
	 * ## OPTIONS
	 *
	 * [--dry-run]
	 * : Report how many emails would be sent without sending.
	 *
	 * ## EXAMPLES
	 *
	 *     wp sea-ojs send-welcome-emails --dry-run
	 *     wp sea-ojs send-welcome-emails
	 *
	 * @param array $args
	 * @param array $assoc_args
	 */
	public function send_welcome_emails( $args, $assoc_args ) {
		$dry_run = isset( $assoc_args['dry-run'] );

		// Find all WP users who have been synced (have an OJS user ID cached).
		global $wpdb;
		$synced_users = $wpdb->get_results(
			"SELECT user_id, meta_value AS ojs_user_id FROM {$wpdb->usermeta} WHERE meta_key = '_sea_ojs_user_id'"
		);

		$total = count( $synced_users );

		if ( $total === 0 ) {
			WP_CLI::warning( 'No synced users found. Run "wp sea-ojs sync" first.' );
			return;
		}

		WP_CLI::log( sprintf( 'Found %d synced users.', $total ) );

		if ( $dry_run ) {
			WP_CLI::success( sprintf( 'Dry run: would send welcome emails to %d users.', $total ) );
			return;
		}

		$sent    = 0;
		$skipped = 0;
		$failed  = 0;

		$progress = \WP_CLI\Utils\make_progress_bar( 'Sending welcome emails', $total );

		foreach ( $synced_users as $row ) {
			$ojs_user_id = (int) $row->ojs_user_id;
			$wp_user_id  = (int) $row->user_id;
			$user        = get_userdata( $wp_user_id );
			$email       = $user ? $user->user_email : 'unknown';

			$result = $this->api->send_welcome_email( $ojs_user_id );

			if ( $result['success'] ) {
				$body = $result['body'];
				if ( ! empty( $body['sent'] ) ) {
					$sent++;
				} else {
					// Already sent (dedup) or other skip reason.
					$skipped++;
				}
			} else {
				$failed++;
				WP_CLI::warning( sprintf( '  %s: %s', $email, $result['error'] ) );
				$this->logger->log( $wp_user_id, $email, 'welcome_email', 'fail', $result['code'], $result['error'] );
			}

			$progress->tick();
			usleep( 100000 ); // 100ms delay between emails.
		}

		$progress->finish();

		WP_CLI::log( '' );
		WP_CLI::log( sprintf( 'Results: %d sent, %d already sent (skipped), %d failed.', $sent, $skipped, $failed ) );

		if ( $failed > 0 ) {
			WP_CLI::warning( sprintf( '%d emails failed. Re-run to retry (OJS dedup prevents duplicates).', $failed ) );
		} else {
			WP_CLI::success( 'Welcome emails complete.' );
		}
	}

	/**
	 * Run reconciliation now.
	 *
	 * ## EXAMPLES
	 *
	 *     wp sea-ojs reconcile
	 *
	 * @param array $args
	 * @param array $assoc_args
	 */
	public function reconcile( $args, $assoc_args ) {
		WP_CLI::log( 'Running reconciliation...' );

		$members = $this->resolver->get_all_active_members();
		$total   = count( $members );
		$queued  = 0;
		$errors  = 0;
		$ok      = 0;

		$progress = \WP_CLI\Utils\make_progress_bar( 'Checking members', $total );

		foreach ( $members as $wp_user_id ) {
			$user = get_userdata( $wp_user_id );
			if ( ! $user ) {
				$progress->tick();
				continue;
			}

			$result = $this->api->get_subscriptions( array( 'email' => $user->user_email ) );
			if ( ! $result['success'] ) {
				$errors++;
				WP_CLI::warning( sprintf( 'API error for %s: %s', $user->user_email, $result['error'] ) );
				$progress->tick();
				continue;
			}

			$has_active = false;
			if ( is_array( $result['body'] ) ) {
				foreach ( $result['body'] as $sub ) {
					if ( isset( $sub['status'] ) && (int) $sub['status'] === 1 ) {
						$has_active = true;
						break;
					}
				}
			}

			if ( ! $has_active ) {
				$as_args = array( 'wp_user_id' => $wp_user_id );
				if ( ! as_has_scheduled_action( 'sea_ojs_sync_activate', $as_args, 'sea-ojs-sync' ) ) {
					as_schedule_single_action( time(), 'sea_ojs_sync_activate', $as_args, 'sea-ojs-sync' );
				}
				$queued++;
				WP_CLI::log( sprintf( '  Queued activate for %s (no active OJS subscription)', $user->user_email ) );
			} else {
				$ok++;
			}

			$progress->tick();
			usleep( 100000 ); // 100ms delay to avoid hammering OJS.
		}

		$progress->finish();

		// Stale access check: find synced users who are no longer active WP members.
		WP_CLI::log( '' );
		WP_CLI::log( 'Stale access check: synced users no longer active...' );

		global $wpdb;
		$synced_users = $wpdb->get_col(
			"SELECT user_id FROM {$wpdb->usermeta} WHERE meta_key = '_sea_ojs_user_id'"
		);
		$active_set = array_flip( $members );
		$expired    = 0;

		foreach ( $synced_users as $uid ) {
			$uid = (int) $uid;
			if ( ! isset( $active_set[ $uid ] ) ) {
				$as_args = array( 'wp_user_id' => $uid );
				if ( ! as_has_scheduled_action( 'sea_ojs_sync_expire', $as_args, 'sea-ojs-sync' ) ) {
					as_schedule_single_action( time(), 'sea_ojs_sync_expire', $as_args, 'sea-ojs-sync' );
				}
				$expired++;
				$user = get_userdata( $uid );
				$email = $user ? $user->user_email : "user #$uid";
				WP_CLI::log( sprintf( '  Queued expire for %s (no longer active member)', $email ) );
			}
		}

		WP_CLI::log( '' );
		WP_CLI::log( sprintf( 'Missing access: %d OK, %d queued for sync, %d API errors.', $ok, $queued, $errors ) );
		WP_CLI::log( sprintf( 'Stale access:   %d queued for expiration.', $expired ) );

		if ( $queued > 0 || $expired > 0 ) {
			WP_CLI::log( 'Queued actions will be processed by Action Scheduler automatically.' );
		}

		WP_CLI::success( 'Reconciliation complete.' );
	}

	/**
	 * Show sync status.
	 *
	 * ## EXAMPLES
	 *
	 *     wp sea-ojs status
	 *
	 * @param array $args
	 * @param array $assoc_args
	 */
	public function status( $args, $assoc_args ) {
		// Action Scheduler stats.
		WP_CLI::log( 'Action Scheduler Queue (sea-ojs-sync)' );
		WP_CLI::log( '======================================' );

		if ( class_exists( 'ActionScheduler' ) ) {
			$store    = ActionScheduler::store();
			$statuses = array(
				'Pending'    => ActionScheduler_Store::STATUS_PENDING,
				'Running'    => ActionScheduler_Store::STATUS_RUNNING,
				'Failed'     => ActionScheduler_Store::STATUS_FAILED,
				'Complete'   => ActionScheduler_Store::STATUS_COMPLETE,
			);

			$rows = array();
			foreach ( $statuses as $label => $status ) {
				$rows[] = array(
					'Status' => $label,
					'Count'  => (int) $store->query_actions_count_by_status( $status, 'sea-ojs-sync' ),
				);
			}
			WP_CLI\Utils\format_items( 'table', $rows, array( 'Status', 'Count' ) );
		} else {
			WP_CLI::warning( 'Action Scheduler is not available.' );
		}

		// Active members.
		$members = $this->resolver->get_all_active_members();
		WP_CLI::log( '' );
		WP_CLI::log( sprintf( 'Active WP members: %d', count( $members ) ) );

		// Synced members (those with _sea_ojs_user_id).
		global $wpdb;
		$synced = (int) $wpdb->get_var(
			"SELECT COUNT(DISTINCT user_id) FROM {$wpdb->usermeta} WHERE meta_key = '_sea_ojs_user_id'"
		);
		WP_CLI::log( sprintf( 'Members synced to OJS: %d', $synced ) );

		// Recent failures.
		$since = gmdate( 'Y-m-d H:i:s', time() - DAY_IN_SECONDS );
		$failures = $this->logger->get_failure_count_since( $since );
		WP_CLI::log( sprintf( 'Failures in last 24h: %d', $failures ) );

		// Cron status.
		$next_recon  = wp_next_scheduled( 'sea_ojs_daily_reconcile' );
		$next_digest = wp_next_scheduled( 'sea_ojs_daily_digest' );

		WP_CLI::log( '' );
		WP_CLI::log( 'Cron Schedule' );
		WP_CLI::log( '=============' );
		WP_CLI::log( sprintf( 'Reconciliation: %s', $next_recon ? gmdate( 'Y-m-d H:i:s', $next_recon ) : 'Not scheduled' ) );
		WP_CLI::log( sprintf( 'Daily digest:   %s', $next_digest ? gmdate( 'Y-m-d H:i:s', $next_digest ) : 'Not scheduled' ) );
	}

	/**
	 * Test connection to OJS.
	 *
	 * ## EXAMPLES
	 *
	 *     wp sea-ojs test-connection
	 *
	 * @param array $args
	 * @param array $assoc_args
	 */
	public function test_connection( $args, $assoc_args ) {
		$ojs_url = get_option( 'sea_ojs_url', '' );
		if ( ! $ojs_url ) {
			WP_CLI::error( 'OJS URL not configured. Set it in Settings > OJS Sync.' );
		}

		WP_CLI::log( 'OJS URL: ' . $ojs_url );
		WP_CLI::log( 'API Key: ' . ( defined( 'SEA_OJS_API_KEY' ) && SEA_OJS_API_KEY ? 'Configured' : 'NOT CONFIGURED' ) );
		WP_CLI::log( '' );

		// Step 1: Ping (no auth).
		WP_CLI::log( 'Step 1: Ping (reachability, no auth)...' );
		$ping = $this->api->ping();
		if ( $ping['success'] ) {
			WP_CLI::log( '  OK: OJS is reachable.' );
		} else {
			WP_CLI::error( '  FAIL: OJS not reachable: ' . $ping['error'] );
		}

		// Step 2: Preflight (auth + IP + compatibility).
		WP_CLI::log( 'Step 2: Preflight (auth + IP + compatibility)...' );
		$preflight = $this->api->preflight();
		if ( ! $preflight['success'] ) {
			$code = $preflight['code'];
			if ( $code === 403 ) {
				WP_CLI::error( '  FAIL: Access denied. IP not allowlisted or insufficient role. HTTP ' . $code );
			} elseif ( $code === 401 ) {
				WP_CLI::error( '  FAIL: Authentication failed. Check API key. HTTP ' . $code );
			} else {
				WP_CLI::error( '  FAIL: Preflight failed: ' . $preflight['error'] . ' (HTTP ' . $code . ')' );
			}
		}

		$body = $preflight['body'];
		if ( isset( $body['compatible'] ) && ! $body['compatible'] ) {
			WP_CLI::log( '  WARNING: OJS is authenticated but incompatible.' );
			if ( isset( $body['checks'] ) ) {
				foreach ( $body['checks'] as $check ) {
					$status = $check['ok'] ? 'OK' : 'FAIL';
					WP_CLI::log( sprintf( '    %s: %s', $status, $check['name'] ) );
				}
			}
			WP_CLI::error( 'Incompatible OJS version. Update the OJS plugin.' );
		}

		WP_CLI::log( '  OK: Authenticated, IP allowed, and compatible.' );

		if ( isset( $body['checks'] ) ) {
			foreach ( $body['checks'] as $check ) {
				WP_CLI::log( sprintf( '    OK: %s', $check['name'] ) );
			}
		}

		WP_CLI::log( '' );
		WP_CLI::success( 'Connection test passed. OJS is ready for sync.' );
	}
}
