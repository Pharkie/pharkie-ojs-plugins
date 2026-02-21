<?php

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

class SEA_OJS_Hooks {

	/** @var SEA_OJS_Resolver */
	private $resolver;

	/**
	 * Holds user data captured before deletion, keyed by user ID.
	 * Used to pass email and OJS user ID from the pre-delete hook
	 * to the post-delete hook, since usermeta is gone by then.
	 *
	 * @var array
	 */
	public static $pending_deletions = array();

	public function __construct( SEA_OJS_Resolver $resolver ) {
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

		// WP profile update (email change detection).
		add_action( 'profile_update', array( $this, 'on_profile_update' ), 10, 3 );

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

		$args = array( 'wp_user_id' => $wp_user_id );
		if ( ! as_has_scheduled_action( 'sea_ojs_sync_activate', $args, 'sea-ojs-sync' ) ) {
			as_schedule_single_action( time(), 'sea_ojs_sync_activate', $args, 'sea-ojs-sync' );
		}
	}

	/**
	 * WCS subscription expired, cancelled, or on-hold.
	 * Schedule: expire OJS subscription.
	 *
	 * But first check: does the user still have another active subscription?
	 * If yes, don't expire -- the user is still a member.
	 * We exclude the current subscription from the check (C4) to avoid
	 * stale cache issues where the just-cancelled sub still appears active.
	 *
	 * @param WC_Subscription $subscription
	 */
	public function on_subscription_inactive( $subscription ) {
		$wp_user_id = $subscription->get_user_id();
		$user       = get_userdata( $wp_user_id );

		if ( ! $user ) {
			return;
		}

		// Check if user is still an active member via other subscriptions or manual roles.
		// Exclude the current subscription to avoid stale cache returning it as active.
		if ( $this->resolver->is_active_member( $wp_user_id, $subscription->get_id() ) ) {
			return;
		}

		$args = array( 'wp_user_id' => $wp_user_id );
		if ( ! as_has_scheduled_action( 'sea_ojs_sync_expire', $args, 'sea-ojs-sync' ) ) {
			as_schedule_single_action( time(), 'sea_ojs_sync_expire', $args, 'sea-ojs-sync' );
		}
	}

	/**
	 * WP profile updated. Detect email changes.
	 *
	 * @param int     $user_id
	 * @param WP_User $old_userdata
	 * @param array   $userdata
	 */
	public function on_profile_update( $user_id, $old_userdata, $userdata = array() ) {
		$old_email = $old_userdata->user_email;
		$new_user  = get_userdata( $user_id );

		if ( ! $new_user ) {
			return;
		}

		$new_email = $new_user->user_email;

		// Only act on actual email changes.
		if ( $old_email === $new_email ) {
			return;
		}

		$args = array(
			'wp_user_id' => $user_id,
			'old_email'  => $old_email,
			'new_email'  => $new_email,
		);
		if ( ! as_has_scheduled_action( 'sea_ojs_sync_email_change', $args, 'sea-ojs-sync' ) ) {
			as_schedule_single_action( time(), 'sea_ojs_sync_email_change', $args, 'sea-ojs-sync' );
		}
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
				'ojs_user_id' => get_user_meta( $user_id, '_sea_ojs_user_id', true ),
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

		as_schedule_single_action( time(), 'sea_ojs_sync_delete_user', array(
			'wp_user_id'  => $user_id,
			'email'       => $data['email'],
			'ojs_user_id' => $data['ojs_user_id'] ? (int) $data['ojs_user_id'] : null,
		), 'sea-ojs-sync' );
	}
}
