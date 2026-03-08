<?php

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

class WPOJS_CLI {

	/** @var WPOJS_Sync */
	private $sync;

	/** @var WPOJS_Resolver */
	private $resolver;

	/** @var WPOJS_API_Client */
	private $api;

	/** @var WPOJS_Logger */
	private $logger;

	/** @var WPOJS_Stats */
	private $stats;

	public function __construct( WPOJS_Sync $sync, WPOJS_Resolver $resolver, WPOJS_API_Client $api, WPOJS_Logger $logger, WPOJS_Stats $stats ) {
		$this->sync     = $sync;
		$this->resolver = $resolver;
		$this->api      = $api;
		$this->logger   = $logger;
		$this->stats    = $stats;
	}

	/**
	 * Register WP-CLI commands.
	 */
	public static function register( WPOJS_Sync $sync, WPOJS_Resolver $resolver, WPOJS_API_Client $api, WPOJS_Logger $logger, WPOJS_Stats $stats ) {
		$instance = new self( $sync, $resolver, $api, $logger, $stats );
		WP_CLI::add_command( 'ojs-sync sync', array( $instance, 'sync' ) );
		WP_CLI::add_command( 'ojs-sync reconcile', array( $instance, 'reconcile' ) );
		WP_CLI::add_command( 'ojs-sync status', array( $instance, 'status' ) );
		WP_CLI::add_command( 'ojs-sync test-connection', array( $instance, 'test_connection' ) );
	}

	/**
	 * Bulk sync members to OJS, or sync a single user.
	 *
	 * ## OPTIONS
	 *
	 * [--dry-run]
	 * : Report what would happen without making changes.
	 *
	 * [--member=<id-or-email>]
	 * : Sync a single member by WP user ID or email.
	 *
	 * [--batch-size=<number>]
	 * : Number of users per batch before logging progress. Default 50.
	 *
	 * [--yes]
	 * : Skip confirmation prompt (for scripting).
	 *
	 * ## EXAMPLES
	 *
	 *     wp ojs-sync sync --dry-run
	 *     wp ojs-sync sync
	 *     wp ojs-sync sync --member=42
	 *     wp ojs-sync sync --member=member@example.com
	 *     wp ojs-sync sync --yes
	 *
	 * @param array $args
	 * @param array $assoc_args
	 */
	public function sync( $args, $assoc_args ) {
		// Reject unknown flags to prevent silent fallthrough to bulk sync.
		// e.g. --user= (wrong flag) would otherwise ignore the flag and run
		// a full bulk sync instead of targeting one member.
		$known_flags = array( 'dry-run', 'member', 'batch-size', 'yes' );
		$unknown     = array_diff( array_keys( $assoc_args ), $known_flags );
		if ( ! empty( $unknown ) ) {
			WP_CLI::error( 'Unknown flag(s): --' . implode( ', --', $unknown ) . '. Did you mean --member=<id-or-email>?' );
		}

		$dry_run = isset( $assoc_args['dry-run'] );

		// Single user sync.
		if ( isset( $assoc_args['member'] ) ) {
			$this->sync_single_user( $assoc_args['member'], $dry_run );
			return;
		}

		// Bulk sync.
		$batch_size   = isset( $assoc_args['batch-size'] ) ? absint( $assoc_args['batch-size'] ) : 50;
		$skip_confirm = isset( $assoc_args['yes'] );
		$this->sync_bulk( $dry_run, $batch_size, $skip_confirm );
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

		// Single-user sync sends password hash (member can log in with WP password).
		$result = $this->sync->sync_user( $user->ID, $dry_run, true );

		if ( $result['success'] ) {
			WP_CLI::success( $result['message'] );
		} else {
			WP_CLI::error( $result['message'] );
		}
	}

	/**
	 * Bulk sync: processes members sequentially with adaptive throttling.
	 *
	 * Sequential processing is intentional at ~700 members. Each user requires
	 * 2 API calls (find-or-create + subscription upsert) and the OJS plugin
	 * uses individual DB transactions per call.
	 *
	 * Adaptive throttling uses two signals to pace requests:
	 * 1. OJS response time — if OJS slows down, WP backs off proportionally.
	 * 2. OJS load protection — if OJS returns 429 with Retry-After, WP
	 *    sleeps the requested duration before continuing.
	 *
	 * OJS self-monitors its own response times and pushes back (429) when
	 * under load. No magic request counts or fixed windows.
	 *
	 * If the member count grows past ~10k, consider a batch find-or-create
	 * endpoint on OJS to reduce the number of HTTP round-trips.
	 *
	 * @param bool $dry_run
	 * @param int  $batch_size
	 * @param bool $skip_confirm
	 */
	private function sync_bulk( $dry_run, $batch_size = 50, $skip_confirm = false ) {
		WP_CLI::log( 'Resolving active members (this may take a few minutes)...' );
		$members = $this->resolver->get_all_active_members();
		$total   = count( $members );

		if ( $total === 0 ) {
			WP_CLI::warning( 'No active members found.' );
			return;
		}

		WP_CLI::log( sprintf( 'Found %d active members.', $total ) );

		if ( ! $dry_run ) {
			WP_CLI::log( 'Throttling: adaptive (response-time based, OJS load protection).' );
		}

		if ( $dry_run ) {
			WP_CLI::log( 'Dry run -- no changes will be made.' );
		}

		// Confirm before bulk sync (skip in dry-run mode or with --yes).
		if ( ! $dry_run && ! $skip_confirm ) {
			WP_CLI::confirm( sprintf( 'Proceed with syncing %d members to OJS?', $total ) );
		}

		$success       = 0;
		$skipped       = 0;
		$failed        = 0;
		$start_time    = microtime( true );
		$total_delay   = 0;
		$last_delay_ms = 0;

		$progress = \WP_CLI\Utils\make_progress_bar( 'Syncing members', $total );

		foreach ( $members as $index => $wp_user_id ) {
			// Measure call duration for adaptive throttling.
			$call_start = microtime( true );

			// Bulk sync sends WP password hash (members can log in with existing WP password).
			// No welcome emails -- members already know their password.
			$result = $this->sync->sync_user( $wp_user_id, $dry_run, true );

			$call_ms = ( microtime( true ) - $call_start ) * 1000;

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

			// Batch progress logging.
			if ( ! $dry_run && ( $index + 1 ) % $batch_size === 0 ) {
				$batch_num = ceil( ( $index + 1 ) / $batch_size );
				WP_CLI::log( sprintf( '  Batch %d complete. Last call: %dms, delay: %dms.', $batch_num, (int) $call_ms, $last_delay_ms ) );
			}

			// Adaptive delay (not on dry run).
			if ( ! $dry_run ) {
				$http_code    = isset( $result['code'] ) ? (int) $result['code'] : 0;
				$retry_after  = isset( $result['retry_after'] ) ? (int) $result['retry_after'] : null;
				$delay_ms     = $this->calculate_adaptive_delay( $call_ms, $result['success'], $http_code, $retry_after );
				if ( $delay_ms > 0 ) {
					usleep( (int) ( $delay_ms * 1000 ) );
					$total_delay += $delay_ms;
				}
				$last_delay_ms = (int) $delay_ms;
			}
		}

		$progress->finish();

		$elapsed = microtime( true ) - $start_time;
		WP_CLI::log( '' );
		WP_CLI::log( sprintf(
			'Results: %d synced, %d skipped, %d failed out of %d total. (%.0fs elapsed, %.0fs in throttle delays)',
			$success, $skipped, $failed, $total, $elapsed, $total_delay / 1000
		) );

		if ( $failed > 0 ) {
			WP_CLI::warning( sprintf( '%d members failed to sync. Check the sync log for details.', $failed ) );
		} else {
			WP_CLI::success( 'Bulk sync complete. Members can log into OJS with their WP password.' );
		}
	}

	/**
	 * Calculate adaptive delay based on OJS response time and result.
	 *
	 * Two signals:
	 * 1. Response time — OJS's own load indicator. Fast = idle, slow = busy.
	 * 2. HTTP status — 429 with Retry-After from OJS load protection, or 5xx.
	 *
	 * @param float    $response_ms  How long the last API call took.
	 * @param bool     $success      Whether the call succeeded.
	 * @param int      $http_code    HTTP status code (0 = network error).
	 * @param int|null $retry_after  Retry-After value from 429 response (seconds).
	 * @return int Delay in milliseconds.
	 */
	private function calculate_adaptive_delay( $response_ms, $success = true, $http_code = 200, $retry_after = null ) {
		// 429 — OJS load protection is telling us to slow down.
		// Use the Retry-After header value (seconds → ms).
		if ( $http_code === 429 ) {
			$retry_s = $retry_after ? (int) $retry_after : 5;
			return $retry_s * 1000;
		}

		// Server error or network failure — back off.
		if ( $http_code === 0 || $http_code >= 500 ) {
			return 5000;
		}

		// Other failure (4xx) — don't delay, the issue isn't load.
		if ( ! $success ) {
			return 0;
		}

		// OJS is idle — full speed ahead.
		if ( $response_ms < 200 ) {
			return 0;
		}

		// Light load — small courtesy delay.
		if ( $response_ms < 500 ) {
			return 100;
		}

		// Under load — mirror the response time as delay.
		return (int) $response_ms;
	}

	/**
	 * Run reconciliation now.
	 *
	 * ## EXAMPLES
	 *
	 *     wp ojs-sync reconcile
	 *
	 * @param array $args
	 * @param array $assoc_args
	 */
	public function reconcile( $args, $assoc_args ) {
		WP_CLI::log( 'Running reconciliation...' );
		WP_CLI::log( 'Resolving active members (this may take a few minutes)...' );

		$members = $this->resolver->get_all_active_members();
		$total   = count( $members );
		$queued  = 0;
		$errors  = 0;
		$ok      = 0;

		// Build email→user_id map.
		$email_to_user_id = array();
		foreach ( $members as $wp_user_id ) {
			$user = get_userdata( $wp_user_id );
			if ( $user ) {
				$email_to_user_id[ $user->user_email ] = $wp_user_id;
			}
		}

		$checked = count( $email_to_user_id );
		WP_CLI::log( sprintf( 'Checking %d members in batches of 100...', $checked ) );
		$progress = \WP_CLI\Utils\make_progress_bar( 'Checking members', $checked );

		// Batch-query OJS for subscription status.
		$email_chunks = array_chunk( array_keys( $email_to_user_id ), 100 );
		foreach ( $email_chunks as $chunk ) {
			$result = $this->api->get_subscription_status_batch( $chunk );

			if ( ! $result['success'] ) {
				$errors += count( $chunk );
				WP_CLI::warning( sprintf( 'Batch API error: %s', $result['error'] ) );
				for ( $i = 0; $i < count( $chunk ); $i++ ) {
					$progress->tick();
				}
				continue;
			}

			$statuses = isset( $result['body']['results'] ) ? $result['body']['results'] : array();

			foreach ( $chunk as $email ) {
				$wp_user_id = $email_to_user_id[ $email ];
				$has_active = isset( $statuses[ $email ]['active'] ) && $statuses[ $email ]['active'];

				if ( ! $has_active ) {
					$as_args = array( array( 'wp_user_id' => $wp_user_id ) );
					if ( ! as_has_scheduled_action( 'wpojs_sync_activate', $as_args, 'wpojs-sync' ) ) {
						as_schedule_single_action( time(), 'wpojs_sync_activate', $as_args, 'wpojs-sync' );
					}
					$queued++;
					WP_CLI::log( sprintf( '  Queued activate for %s (no active OJS subscription)', $email ) );
				} else {
					$ok++;
				}

				$progress->tick();
			}
		}

		$progress->finish();

		// Stale access check: find synced users who are no longer active WP members.
		WP_CLI::log( '' );
		WP_CLI::log( 'Stale access check: synced users no longer active...' );

		global $wpdb;
		$synced_users = $wpdb->get_col(
			"SELECT user_id FROM {$wpdb->usermeta} WHERE meta_key = '_wpojs_user_id'"
		);
		$active_set = array_flip( $members );
		$expired    = 0;

		foreach ( $synced_users as $uid ) {
			$uid = (int) $uid;
			if ( ! isset( $active_set[ $uid ] ) ) {
				$as_args = array( array( 'wp_user_id' => $uid ) );
				if ( ! as_has_scheduled_action( 'wpojs_sync_expire', $as_args, 'wpojs-sync' ) ) {
					as_schedule_single_action( time(), 'wpojs_sync_expire', $as_args, 'wpojs-sync' );
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
	 *     wp ojs-sync status
	 *
	 * @param array $args
	 * @param array $assoc_args
	 */
	public function status( $args, $assoc_args ) {
		// Action Scheduler stats.
		WP_CLI::log( 'Action Scheduler Queue (wpojs-sync)' );
		WP_CLI::log( '======================================' );

		$queue = $this->stats->get_queue_counts();
		if ( class_exists( 'ActionScheduler' ) ) {
			$rows = array();
			foreach ( array( 'Pending' => 'pending', 'Running' => 'running', 'Failed' => 'failed', 'Complete' => 'complete' ) as $label => $key ) {
				$rows[] = array( 'Status' => $label, 'Count' => $queue[ $key ] );
			}
			WP_CLI\Utils\format_items( 'table', $rows, array( 'Status', 'Count' ) );
		} else {
			WP_CLI::warning( 'Action Scheduler is not available.' );
		}

		// Active members.
		WP_CLI::log( 'Resolving active members...' );
		$active = $this->stats->get_active_member_count();
		$synced = $this->stats->get_synced_member_count();
		WP_CLI::log( '' );
		WP_CLI::log( sprintf( 'Active WP members: %d', $active ) );
		WP_CLI::log( sprintf( 'Members synced to OJS: %d', $synced ) );

		// Recent failures.
		$failures = $this->stats->get_failure_count_hours( 24 );
		WP_CLI::log( sprintf( 'Failures in last 24h: %d', $failures ) );

		// Cron status.
		$cron = $this->stats->get_cron_schedule();

		WP_CLI::log( '' );
		WP_CLI::log( 'Cron Schedule' );
		WP_CLI::log( '=============' );
		WP_CLI::log( sprintf( 'Reconciliation: %s', $cron['reconcile'] ? gmdate( 'Y-m-d H:i:s', $cron['reconcile'] ) : 'Not scheduled' ) );
		WP_CLI::log( sprintf( 'Daily digest:   %s', $cron['digest'] ? gmdate( 'Y-m-d H:i:s', $cron['digest'] ) : 'Not scheduled' ) );
		WP_CLI::log( sprintf( 'Log cleanup:    %s', $cron['log_cleanup'] ? gmdate( 'Y-m-d H:i:s', $cron['log_cleanup'] ) : 'Not scheduled' ) );
	}

	/**
	 * Test connection to OJS.
	 *
	 * ## EXAMPLES
	 *
	 *     wp ojs-sync test-connection
	 *
	 * @param array $args
	 * @param array $assoc_args
	 */
	public function test_connection( $args, $assoc_args ) {
		$ojs_url = get_option( 'wpojs_url', '' );
		if ( ! $ojs_url ) {
			WP_CLI::error( 'OJS URL not configured. Set it in Settings > OJS Sync.' );
		}

		WP_CLI::log( 'OJS URL: ' . $ojs_url );
		WP_CLI::log( 'API Key: ' . ( defined( 'WPOJS_API_KEY' ) && WPOJS_API_KEY ? 'Configured' : 'NOT CONFIGURED' ) );
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
