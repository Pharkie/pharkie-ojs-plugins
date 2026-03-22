<?php

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

class WPOJS_Sync {

	/** @var WPOJS_API_Client */
	private $api;

	/** @var WPOJS_Logger */
	private $logger;

	/** @var WPOJS_Resolver */
	private $resolver;

	public function __construct( WPOJS_API_Client $api, WPOJS_Logger $logger, WPOJS_Resolver $resolver ) {
		$this->api      = $api;
		$this->logger   = $logger;
		$this->resolver = $resolver;
	}

	/**
	 * Register Action Scheduler callbacks for each sync action.
	 */
	public function register() {
		add_action( 'wpojs_sync_activate', array( $this, 'handle_activate' ) );
		add_action( 'wpojs_sync_expire', array( $this, 'handle_expire' ) );
		add_action( 'wpojs_sync_email_change', array( $this, 'handle_email_change' ) );
		add_action( 'wpojs_sync_delete_user', array( $this, 'handle_delete_user' ) );
		add_action( 'wpojs_sync_password_change', array( $this, 'handle_password_change' ) );
	}

	/**
	 * Handle activate: find-or-create user + create subscription.
	 *
	 * Called by Action Scheduler. On failure, throws an exception so AS
	 * marks the action as failed. Recovery is handled by the daily
	 * reconciliation cron, which detects missing OJS subscriptions and
	 * schedules fresh activate actions.
	 *
	 * We send the WP password hash so the member can log into OJS with
	 * their existing WP password. The hash is only applied when the OJS
	 * user is newly created (existing users' passwords are not overwritten).
	 *
	 * @param array $args { wp_user_id: int }
	 */
	public function handle_activate( $args ) {
		$wp_user_id = isset( $args['wp_user_id'] ) ? (int) $args['wp_user_id'] : 0;

		$user = get_userdata( $wp_user_id );
		if ( ! $user ) {
			$this->logger->log( $wp_user_id, '', 'activate', 'fail', 0, 'WP user not found', 1, $this->api->get_last_request_id() );
			// Permanent failure: no point retrying a non-existent user.
			return;
		}

		$email      = strtolower( $user->user_email );
		// Step 1: Resolve subscription data BEFORE creating OJS user.
		// Avoids orphaned OJS accounts when type mapping is misconfigured.
		$sub_data = $this->resolver->resolve_subscription_data( $wp_user_id );
		if ( ! $sub_data || ! $sub_data['type_id'] ) {
			$this->logger->log( $wp_user_id, $email, 'activate', 'fail', 0, 'Could not resolve subscription type. Check type mapping settings.', 1, $this->api->get_last_request_id() );
			$this->send_admin_alert(
				'OJS Sync: No Subscription Type',
				sprintf( "Action: activate\nEmail: %s\nWP User ID: %d\nNo subscription type resolved. Check type mapping settings.", $email, $wp_user_id )
			);
			return; // Don't retry -- config issue, not transient.
		}

		$first_name = $user->first_name ?: $user->display_name;
		$last_name  = $user->last_name ?: '';

		// Step 2: Find or create OJS user (with WP password hash so they
		// can log into OJS with their existing WP password).
		$result = $this->api->find_or_create_user( $email, $first_name, $last_name, $user->user_pass );
		if ( ! $result['success'] ) {
			$result = $this->find_after_fail( $result, $email );
		}
		if ( ! $result['success'] ) {
			$this->logger->log( $wp_user_id, $email, 'activate', 'fail', $result['code'], $result['error'], 1, $this->api->get_last_request_id() );
			if ( $this->api->is_permanent_fail( $result['code'] ) ) {
				$this->send_admin_alert(
					'OJS Sync: Permanent Failure',
					sprintf( "Action: activate\nEmail: %s\nWP User ID: %d\nHTTP %d: %s", $email, $wp_user_id, $result['code'], $result['error'] )
				);
				return; // Don't retry permanent failures.
			}
			throw new Exception( 'find_or_create_user failed: ' . $result['error'] );
		}

		$ojs_user_id = $result['body']['userId'] ?? null;
		if ( ! $ojs_user_id ) {
			$this->logger->log( $wp_user_id, $email, 'activate', 'fail', $result['code'], 'Unexpected API response: missing userId', 1, $this->api->get_last_request_id() );
			throw new Exception( 'find_or_create_user returned success but no userId' );
		}

		// Cache OJS userId in usermeta.
		update_user_meta( $wp_user_id, '_wpojs_user_id', $ojs_user_id );

		// Log user creation if new.
		if ( ! empty( $result['body']['created'] ) ) {
			$this->logger->log( $wp_user_id, $email, 'create_user', 'success', $result['code'], wp_json_encode( $result['body'] ), 1, $this->api->get_last_request_id() );
		}

		$sub_result = $this->api->create_subscription(
			$ojs_user_id,
			$sub_data['type_id'],
			$sub_data['date_start'],
			$sub_data['date_end']
		);

		if ( ! $sub_result['success'] ) {
			$this->logger->log( $wp_user_id, $email, 'activate', 'fail', $sub_result['code'], $sub_result['error'], 1, $this->api->get_last_request_id() );
			if ( $this->api->is_permanent_fail( $sub_result['code'] ) ) {
				$this->send_admin_alert(
					'OJS Sync: Permanent Failure',
					sprintf( "Action: activate (subscription)\nEmail: %s\nWP User ID: %d\nHTTP %d: %s", $email, $wp_user_id, $sub_result['code'], $sub_result['error'] )
				);
				return;
			}
			throw new Exception( 'create_subscription failed: ' . $sub_result['error'] );
		}

		$this->logger->log( $wp_user_id, $email, 'activate', 'success', $sub_result['code'], wp_json_encode( $sub_result['body'] ), 1, $this->api->get_last_request_id() );
	}

	/**
	 * Handle expire: expire subscription by OJS userId.
	 *
	 * @param array $args { wp_user_id: int }
	 */
	public function handle_expire( $args ) {
		$wp_user_id = isset( $args['wp_user_id'] ) ? (int) $args['wp_user_id'] : 0;

		$user  = get_userdata( $wp_user_id );
		$email = $user ? $user->user_email : '';

		$ojs_user_id = $this->resolve_ojs_user_id( $wp_user_id, $email );
		if ( ! $ojs_user_id ) {
			// User never synced to OJS. Nothing to expire.
			$this->logger->log( $wp_user_id, $email, 'expire', 'success', 0, 'User not found on OJS -- nothing to expire', 1, $this->api->get_last_request_id() );
			return;
		}

		$result = $this->api->expire_subscription_by_user( $ojs_user_id );

		// 404 = no subscription to expire -- that's fine.
		if ( ! $result['success'] && $result['code'] === 404 ) {
			$this->logger->log( $wp_user_id, $email, 'expire', 'success', 404, 'No OJS subscription found -- nothing to expire', 1, $this->api->get_last_request_id() );
			return;
		}

		if ( ! $result['success'] ) {
			$this->logger->log( $wp_user_id, $email, 'expire', 'fail', $result['code'], $result['error'], 1, $this->api->get_last_request_id() );
			if ( $this->api->is_permanent_fail( $result['code'] ) ) {
				$this->send_admin_alert(
					'OJS Sync: Permanent Failure',
					sprintf( "Action: expire\nEmail: %s\nWP User ID: %d\nHTTP %d: %s", $email, $wp_user_id, $result['code'], $result['error'] )
				);
				return;
			}
			throw new Exception( 'expire_subscription failed: ' . $result['error'] );
		}

		$this->logger->log( $wp_user_id, $email, 'expire', 'success', $result['code'], wp_json_encode( $result['body'] ), 1, $this->api->get_last_request_id() );
	}

	/**
	 * Handle email_change: update OJS user email.
	 *
	 * @param array $args { wp_user_id: int, old_email: string, new_email: string }
	 */
	public function handle_email_change( $args ) {
		$wp_user_id = isset( $args['wp_user_id'] ) ? (int) $args['wp_user_id'] : 0;
		$old_email  = isset( $args['old_email'] ) ? strtolower( $args['old_email'] ) : '';
		$new_email  = isset( $args['new_email'] ) ? strtolower( $args['new_email'] ) : '';

		if ( ! $old_email || ! $new_email ) {
			$this->logger->log( $wp_user_id, $old_email, 'email_change', 'fail', 0, 'Missing old/new email in args', 1, $this->api->get_last_request_id() );
			return; // Permanent: bad data, don't retry.
		}

		// Staleness check: if the user's current WP email no longer matches
		// new_email, a newer email change has superseded this one.
		$current_user = get_userdata( $wp_user_id );
		if ( $current_user && strtolower( $current_user->user_email ) !== $new_email ) {
			$this->logger->log( $wp_user_id, $old_email, 'email_change', 'success', 0,
				sprintf( 'Superseded by newer change (current email: %s)', $current_user->user_email ),
				1, $this->api->get_last_request_id()
			);
			return;
		}

		$ojs_user_id = $this->resolve_ojs_user_id( $wp_user_id, $old_email );
		if ( ! $ojs_user_id ) {
			$this->logger->log( $wp_user_id, $old_email, 'email_change', 'success', 0, 'User not found on OJS -- nothing to update', 1, $this->api->get_last_request_id() );
			return;
		}

		$result = $this->api->update_user_email( $ojs_user_id, $new_email );

		// 409 = new email already in use on OJS. Permanent fail, admin must resolve.
		if ( ! $result['success'] && $result['code'] === 409 ) {
			$this->send_admin_alert(
				'OJS Sync: Email Conflict',
				sprintf(
					"Email change for WP user #%d failed. New email '%s' is already in use on OJS.\nOld email: %s\nManual resolution required in OJS admin.",
					$wp_user_id,
					$new_email,
					$old_email
				)
			);
			$this->logger->log( $wp_user_id, $old_email, 'email_change', 'fail', 409, $result['error'], 1, $this->api->get_last_request_id() );
			return; // Don't retry 409.
		}

		if ( ! $result['success'] ) {
			$this->logger->log( $wp_user_id, $old_email, 'email_change', 'fail', $result['code'], $result['error'], 1, $this->api->get_last_request_id() );
			if ( $this->api->is_permanent_fail( $result['code'] ) ) {
				return;
			}
			throw new Exception( 'update_user_email failed: ' . $result['error'] );
		}

		$this->logger->log( $wp_user_id, $new_email, 'email_change', 'success', $result['code'], wp_json_encode( $result['body'] ), 1, $this->api->get_last_request_id() );
	}

	/**
	 * Handle delete_user: GDPR erasure.
	 *
	 * The WP user has already been deleted by the time this runs.
	 * The email and ojs_user_id were captured in pre_delete_user()
	 * and passed via AS args.
	 *
	 * @param array $args { wp_user_id: int, email: string, ojs_user_id: int|null }
	 */
	public function handle_delete_user( $args ) {
		$wp_user_id  = isset( $args['wp_user_id'] ) ? (int) $args['wp_user_id'] : 0;
		$email       = isset( $args['email'] ) ? $args['email'] : '';
		$ojs_user_id = isset( $args['ojs_user_id'] ) ? (int) $args['ojs_user_id'] : 0;

		if ( ! $ojs_user_id && $email ) {
			// Try API lookup by email as fallback.
			$lookup = $this->api->find_user( $email );
			if ( $lookup['success'] && ! empty( $lookup['body']['found'] ) ) {
				$ojs_user_id = (int) $lookup['body']['userId'];
			}
		}

		if ( ! $ojs_user_id ) {
			$this->logger->log( $wp_user_id, $email, 'delete_user', 'success', 0, 'User not found on OJS -- nothing to delete', 1, $this->api->get_last_request_id() );
			// Anonymize sync log entries even if user wasn't on OJS.
			$this->logger->anonymize_user_logs( $wp_user_id, $email );
			return;
		}

		$result = $this->api->delete_user( $ojs_user_id );

		if ( ! $result['success'] ) {
			$this->logger->log( $wp_user_id, $email, 'delete_user', 'fail', $result['code'], $result['error'], 1, $this->api->get_last_request_id() );
			if ( $this->api->is_permanent_fail( $result['code'] ) ) {
				$this->send_admin_alert(
					'OJS Sync: GDPR Delete Failed',
					sprintf( "Action: delete_user\nEmail: %s\nOJS User ID: %d\nHTTP %d: %s", $email, $ojs_user_id, $result['code'], $result['error'] )
				);
				// Anonymize even on failure — the WP user is already gone.
				$this->logger->anonymize_user_logs( $wp_user_id, $email );
				return;
			}
			throw new Exception( 'delete_user failed: ' . $result['error'] );
		}

		// User is already deleted from WP, so no usermeta to clean up.
		$this->logger->log( $wp_user_id, $email, 'delete_user', 'success', $result['code'], wp_json_encode( $result['body'] ), 1, $this->api->get_last_request_id() );
		// Anonymize all sync log entries for this user (GDPR).
		$this->logger->anonymize_user_logs( $wp_user_id, $email );
	}

	/**
	 * Handle password_change: push new WP password hash to OJS.
	 *
	 * Password hash is read fresh from the WP user at processing time,
	 * not stored in the queue args (avoids sensitive data in the AS table).
	 *
	 * @param array $args { wp_user_id: int }
	 */
	public function handle_password_change( $args ) {
		$wp_user_id = isset( $args['wp_user_id'] ) ? (int) $args['wp_user_id'] : 0;

		$user = get_userdata( $wp_user_id );
		if ( ! $user ) {
			$this->logger->log( $wp_user_id, '', 'password_change', 'fail', 0, 'WP user not found', 1, $this->api->get_last_request_id() );
			return;
		}

		$email         = strtolower( $user->user_email );
		$password_hash = $user->user_pass;

		$ojs_user_id = $this->resolve_ojs_user_id( $wp_user_id, $email );
		if ( ! $ojs_user_id ) {
			$this->logger->log( $wp_user_id, $email, 'password_change', 'success', 0, 'User not found on OJS -- nothing to update', 1, $this->api->get_last_request_id() );
			return;
		}

		$result = $this->api->update_user_password( $ojs_user_id, $password_hash );

		if ( ! $result['success'] ) {
			$this->logger->log( $wp_user_id, $email, 'password_change', 'fail', $result['code'], $result['error'], 1, $this->api->get_last_request_id() );
			if ( $this->api->is_permanent_fail( $result['code'] ) ) {
				return;
			}
			throw new Exception( 'update_user_password failed: ' . $result['error'] );
		}

		$this->logger->log( $wp_user_id, $email, 'password_change', 'success', $result['code'], wp_json_encode( $result['body'] ), 1, $this->api->get_last_request_id() );
	}

	/**
	 * Resolve OJS userId: check usermeta first, fall back to API lookup.
	 *
	 * @param int    $wp_user_id
	 * @param string $email
	 * @return int|null OJS userId or null if not found.
	 */
	public function resolve_ojs_user_id( $wp_user_id, $email ) {
		// Check usermeta cache first.
		$cached = get_user_meta( $wp_user_id, '_wpojs_user_id', true );
		if ( $cached ) {
			return (int) $cached;
		}

		// Fall back to API lookup.
		if ( ! $email ) {
			return null;
		}

		$result = $this->api->find_user( $email );
		if ( $result['success'] && ! empty( $result['body']['found'] ) ) {
			$ojs_user_id = (int) $result['body']['userId'];
			// Cache for future use.
			update_user_meta( $wp_user_id, '_wpojs_user_id', $ojs_user_id );
			return $ojs_user_id;
		}

		return null;
	}

	/**
	 * After a retryable find_or_create_user failure, check whether the user
	 * was actually created (common under Rosetta emulation where OJS commits
	 * the DB write but the HTTP response times out or 500s).
	 *
	 * Returns a synthetic success result if the user is found, or the
	 * original failure result unchanged.
	 */
	private function find_after_fail( $original_result, $email ) {
		if ( ! $this->api->is_retryable( $original_result['code'] ) ) {
			return $original_result;
		}

		// Brief pause — give OJS a moment to finish if it's still writing.
		usleep( 500000 ); // 500ms.

		$find = $this->api->find_user( $email );
		if ( $find['success'] && ! empty( $find['body']['found'] ) ) {
			return array(
				'success' => true,
				'code'    => $find['code'],
				'body'    => array(
					'userId'  => $find['body']['userId'],
					'created' => false,
				),
				'error'   => '',
			);
		}

		return $original_result;
	}

	/**
	 * Send an admin alert email.
	 */
	public function send_admin_alert( $subject, $message ) {
		$to = get_option( 'admin_email' );
		wp_mail( $to, $subject, $message );
	}

	/**
	 * Sync a single user directly (for CLI / manual use).
	 * Does not go through Action Scheduler -- calls OJS directly.
	 *
	 * @param int  $wp_user_id
	 * @param bool $dry_run
	 * @param bool $send_password_hash Whether to send WP password hash.
	 * @return array Result info.
	 */
	public function sync_user( $wp_user_id, $dry_run = false, $send_password_hash = false ) {
		$user = get_userdata( $wp_user_id );
		if ( ! $user ) {
			return array( 'success' => false, 'code' => 0, 'message' => 'WP user not found.' );
		}

		$sub_data = $this->resolver->resolve_subscription_data( $wp_user_id );
		if ( ! $sub_data ) {
			return array( 'success' => false, 'code' => 0, 'message' => 'User is not an active member.' );
		}

		if ( $dry_run ) {
			return array(
				'success' => true,
				'code'    => 0,
				'message' => sprintf(
					'Would sync: %s (type_id=%d, date_end=%s)',
					$user->user_email,
					$sub_data['type_id'],
					$sub_data['date_end'] ?? 'non-expiring'
				),
			);
		}

		// Bail before creating OJS user if type mapping is misconfigured.
		if ( ! $sub_data['type_id'] ) {
			return array( 'success' => false, 'code' => 0, 'message' => 'No subscription type resolved. Check type mapping settings.' );
		}

		$email      = strtolower( $user->user_email );
		$first_name = $user->first_name ?: $user->display_name;
		$last_name  = $user->last_name ?: '';

		// Send WP password hash so member can log into OJS with their WP password.
		// Only applied on user creation; existing users' passwords are not overwritten.
		$password_hash = $send_password_hash ? $user->user_pass : null;

		// Step 1: Find or create OJS user.
		$result = $this->api->find_or_create_user( $email, $first_name, $last_name, $password_hash );
		if ( ! $result['success'] ) {
			$result = $this->find_after_fail( $result, $email );
		}
		if ( ! $result['success'] ) {
			$this->logger->log( $wp_user_id, $email, 'activate', 'fail', $result['code'], $result['error'], 1, $this->api->get_last_request_id() );
			$ret = array( 'success' => false, 'code' => $result['code'], 'message' => 'Find-or-create failed: ' . $result['error'] );
			if ( isset( $result['retry_after'] ) ) {
				$ret['retry_after'] = $result['retry_after'];
			}
			return $ret;
		}

		$ojs_user_id = $result['body']['userId'] ?? null;
		if ( ! $ojs_user_id ) {
			$this->logger->log( $wp_user_id, $email, 'activate', 'fail', $result['code'], 'Unexpected API response: missing userId', 1, $this->api->get_last_request_id() );
			return array( 'success' => false, 'code' => $result['code'], 'message' => 'Find-or-create returned success but no userId' );
		}
		update_user_meta( $wp_user_id, '_wpojs_user_id', $ojs_user_id );

		if ( ! empty( $result['body']['created'] ) ) {
			$this->logger->log( $wp_user_id, $email, 'create_user', 'success', $result['code'], wp_json_encode( $result['body'] ), 1, $this->api->get_last_request_id() );
		}

		// Step 2: Create subscription.
		$sub_result = $this->api->create_subscription(
			$ojs_user_id,
			$sub_data['type_id'],
			$sub_data['date_start'],
			$sub_data['date_end']
		);

		if ( ! $sub_result['success'] ) {
			$this->logger->log( $wp_user_id, $email, 'activate', 'fail', $sub_result['code'], $sub_result['error'], 1, $this->api->get_last_request_id() );
			$ret = array( 'success' => false, 'code' => $sub_result['code'], 'message' => 'Create subscription failed: ' . $sub_result['error'] );
			if ( isset( $sub_result['retry_after'] ) ) {
				$ret['retry_after'] = $sub_result['retry_after'];
			}
			return $ret;
		}

		$this->logger->log( $wp_user_id, $email, 'activate', 'success', $sub_result['code'], wp_json_encode( $sub_result['body'] ), 1, $this->api->get_last_request_id() );

		return array(
			'success' => true,
			'code'    => $sub_result['code'],
			'message' => sprintf(
				'Synced: %s -> OJS user %d, subscription %s',
				$email,
				$ojs_user_id,
				isset( $sub_result['body']['subscriptionId'] ) ? $sub_result['body']['subscriptionId'] : '?'
			),
		);
	}
}
