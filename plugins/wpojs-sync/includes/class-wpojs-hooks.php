<?php

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

class WPOJS_Hooks {

	/** @var WPOJS_Resolver */
	private $resolver;

	/**
	 * Holds user data captured before deletion, keyed by user ID.
	 * Used to pass email and OJS user ID from the pre-delete hook
	 * to the post-delete hook, since usermeta is gone by then.
	 *
	 * @var array
	 */
	public static $pending_deletions = array();

	public function __construct( WPOJS_Resolver $resolver ) {
		$this->resolver = $resolver;
	}

	/**
	 * Register all hooks.
	 */
	public function register() {
		// WCS subscription lifecycle events.
		add_action( 'woocommerce_subscription_status_active', array( $this, 'on_subscription_active' ) );
		add_action( 'woocommerce_subscription_status_expired', array( $this, 'on_subscription_inactive' ) );
		add_action( 'woocommerce_subscription_status_cancelled', array( $this, 'on_subscription_inactive' ) );
		add_action( 'woocommerce_subscription_status_on-hold', array( $this, 'on_subscription_inactive' ) );

		// Deferred expiry check — runs 5s after an inactive event to avoid race conditions.
		add_action( 'wpojs_check_and_expire', array( $this, 'on_check_and_expire' ) );

		// WP profile update (email + password change detection).
		add_action( 'profile_update', array( $this, 'on_profile_update' ), 10, 3 );

		// Password reset via "Lost your password?" flow.
		add_action( 'after_password_reset', array( $this, 'on_password_reset' ), 10, 2 );

		// WP user deletion (GDPR).
		// pre-delete: capture data while it still exists.
		add_action( 'delete_user', array( $this, 'pre_delete_user' ) );
		// post-delete: schedule the OJS erasure action.
		add_action( 'deleted_user', array( $this, 'on_user_deleted' ), 10, 2 );
	}

	/**
	 * WCS subscription activated (new signup or reactivation).
	 * Schedule: find-or-create user + create/renew subscription.
	 *
	 * We only store the wp_user_id in the AS args. The sync processor
	 * re-resolves subscription data at processing time to get the
	 * latest state (correct for retries and delayed processing).
	 *
	 * @param WC_Subscription $subscription
	 */
	public function on_subscription_active( $subscription ) {
		$wp_user_id = $subscription->get_user_id();
		$user       = get_userdata( $wp_user_id );

		if ( ! $user ) {
			return;
		}

		$args = array( array( 'wp_user_id' => $wp_user_id ) );
		if ( ! as_has_scheduled_action( 'wpojs_sync_activate', $args, 'wpojs-sync' ) ) {
			as_schedule_single_action( time(), 'wpojs_sync_activate', $args, 'wpojs-sync' );
		}
	}

	/**
	 * WCS subscription expired, cancelled, or on-hold.
	 *
	 * Instead of checking membership inline, we defer the check by 5 seconds.
	 * This prevents a race condition: if two subscriptions expire within
	 * milliseconds, both handlers might see the other sub as still active
	 * (due to WCS cache/timing), causing neither to schedule an expire.
	 * The 5-second delay ensures all status transitions have settled before
	 * we check whether the user has any remaining active subscriptions.
	 *
	 * @param WC_Subscription $subscription
	 */
	public function on_subscription_inactive( $subscription ) {
		$wp_user_id = $subscription->get_user_id();
		$user       = get_userdata( $wp_user_id );

		if ( ! $user ) {
			return;
		}

		// Don't check inline — defer to avoid race with simultaneous expirations.
		$hook = 'wpojs_check_and_expire';
		$args = array( $wp_user_id );
		if ( ! as_has_scheduled_action( $hook, $args, 'wpojs-sync' ) ) {
			as_schedule_single_action( time() + 5, $hook, $args, 'wpojs-sync' );
		}
	}

	/**
	 * Deferred expiry check. Runs 5 seconds after an inactive subscription event.
	 *
	 * By this point all near-simultaneous status changes have settled, so
	 * is_active_member() gives an accurate answer without needing to exclude
	 * any specific subscription.
	 *
	 * @param int $wp_user_id
	 */
	public function on_check_and_expire( $wp_user_id ) {
		$wp_user_id = (int) $wp_user_id;

		// All subs have settled — no exclusion needed.
		if ( $this->resolver->is_active_member( $wp_user_id ) ) {
			return;
		}

		$args = array( array( 'wp_user_id' => $wp_user_id ) );
		if ( ! as_has_scheduled_action( 'wpojs_sync_expire', $args, 'wpojs-sync' ) ) {
			as_schedule_single_action( time(), 'wpojs_sync_expire', $args, 'wpojs-sync' );
		}
	}

	/**
	 * WP profile updated. Detect email and password changes.
	 *
	 * @param int     $user_id
	 * @param WP_User $old_userdata
	 * @param array   $userdata
	 */
	public function on_profile_update( $user_id, $old_userdata, $userdata = array() ) {
		$new_user = get_userdata( $user_id );
		if ( ! $new_user ) {
			return;
		}

		// Detect email change.
		$old_email = $old_userdata->user_email;
		$new_email = $new_user->user_email;

		if ( $old_email !== $new_email ) {
			// Cancel any pending email change actions for this user — a newer change
			// supersedes them. Without this, rapid changes A→B→C would queue two
			// actions with different args (dedup doesn't catch them because args differ).
			$store = ActionScheduler::store();
			$pending = $store->query_actions( array(
				'hook'   => 'wpojs_sync_email_change',
				'status' => \ActionScheduler_Store::STATUS_PENDING,
				'group'  => 'wpojs-sync',
			) );
			foreach ( $pending as $action_id ) {
				$action = $store->fetch_action( $action_id );
				$action_args = $action->get_args();
				if ( isset( $action_args[0]['wp_user_id'] ) && (int) $action_args[0]['wp_user_id'] === $user_id ) {
					$store->cancel_action( $action_id );
				}
			}

			$args = array( array(
				'wp_user_id' => $user_id,
				'old_email'  => $old_email,
				'new_email'  => $new_email,
			) );
			as_schedule_single_action( time(), 'wpojs_sync_email_change', $args, 'wpojs-sync' );
		}

		// Detect password change.
		if ( $old_userdata->user_pass !== $new_user->user_pass ) {
			$this->schedule_password_sync( $user_id );
		}
	}

	/**
	 * Password reset via "Lost your password?" flow.
	 * By the time this fires, $user->user_pass is already the new hash.
	 *
	 * @param WP_User $user
	 * @param string  $new_pass The new plaintext password (unused — we send the hash).
	 */
	public function on_password_reset( $user, $new_pass ) {
		$this->schedule_password_sync( $user->ID );
	}

	/**
	 * Schedule a password hash sync to OJS.
	 * Cancel-and-reschedule pattern handles rapid successive changes.
	 */
	private function schedule_password_sync( $user_id ) {
		// Cancel any pending password change actions for this user.
		$store = ActionScheduler::store();
		$pending = $store->query_actions( array(
			'hook'   => 'wpojs_sync_password_change',
			'status' => \ActionScheduler_Store::STATUS_PENDING,
			'group'  => 'wpojs-sync',
		) );
		foreach ( $pending as $action_id ) {
			$action = $store->fetch_action( $action_id );
			$action_args = $action->get_args();
			if ( isset( $action_args[0]['wp_user_id'] ) && (int) $action_args[0]['wp_user_id'] === $user_id ) {
				$store->cancel_action( $action_id );
			}
		}

		as_schedule_single_action( time(), 'wpojs_sync_password_change', array( array(
			'wp_user_id' => $user_id,
		) ), 'wpojs-sync' );
	}

	/**
	 * Capture user data before deletion (fires before user row + meta are removed).
	 *
	 * @param int $user_id
	 */
	public function pre_delete_user( $user_id ) {
		$user = get_userdata( $user_id );
		if ( $user ) {
			self::$pending_deletions[ $user_id ] = array(
				'email'       => $user->user_email,
				'ojs_user_id' => get_user_meta( $user_id, '_wpojs_user_id', true ),
			);
		}
	}

	/**
	 * WP user deleted (GDPR erasure propagation).
	 * By this point, all user data and usermeta have been removed from the DB.
	 * We rely on pre_delete_user() having captured the email and OJS user ID.
	 *
	 * @param int      $user_id
	 * @param int|null $reassign User ID to reassign posts to, or null.
	 */
	public function on_user_deleted( $user_id, $reassign = null ) {
		if ( ! isset( self::$pending_deletions[ $user_id ] ) ) {
			return;
		}

		$data = self::$pending_deletions[ $user_id ];
		unset( self::$pending_deletions[ $user_id ] );

		as_schedule_single_action( time(), 'wpojs_sync_delete_user', array( array(
			'wp_user_id'  => $user_id,
			'email'       => $data['email'],
			'ojs_user_id' => $data['ojs_user_id'] ? (int) $data['ojs_user_id'] : null,
		) ), 'wpojs-sync' );
	}
}
