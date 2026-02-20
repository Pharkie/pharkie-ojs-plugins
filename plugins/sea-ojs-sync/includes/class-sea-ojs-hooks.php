<?php

if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

class SEA_OJS_Hooks {

    /** @var SEA_OJS_Queue */
    private $queue;

    /** @var SEA_OJS_Resolver */
    private $resolver;

    public function __construct( SEA_OJS_Queue $queue, SEA_OJS_Resolver $resolver ) {
        $this->queue    = $queue;
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
        add_action( 'deleted_user', array( $this, 'on_user_deleted' ), 10, 2 );
    }

    /**
     * WCS subscription activated (new signup or reactivation).
     * Queue: find-or-create user + create/renew subscription + welcome email.
     *
     * @param WC_Subscription $subscription
     */
    public function on_subscription_active( $subscription ) {
        $wp_user_id = $subscription->get_user_id();
        $user       = get_userdata( $wp_user_id );

        if ( ! $user ) {
            return;
        }

        $sub_data = $this->resolver->resolve_subscription_data( $wp_user_id );
        $payload  = array(
            'subscription_id' => $subscription->get_id(),
        );
        if ( $sub_data ) {
            $payload['type_id']    = $sub_data['type_id'];
            $payload['date_start'] = $sub_data['date_start'];
            $payload['date_end']   = $sub_data['date_end'];
        }

        $this->queue->enqueue( $wp_user_id, $user->user_email, 'activate', $payload );
    }

    /**
     * WCS subscription expired, cancelled, or on-hold.
     * Queue: expire OJS subscription.
     *
     * But first check: does the user still have another active subscription?
     * If yes, don't expire — the user is still a member.
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
        if ( $this->resolver->is_active_member( $wp_user_id ) ) {
            return;
        }

        $this->queue->enqueue( $wp_user_id, $user->user_email, 'expire', array(
            'subscription_id' => $subscription->get_id(),
        ) );
    }

    /**
     * WP profile updated. Detect email changes.
     *
     * @param int     $user_id
     * @param WP_User $old_userdata
     * @param array   $userdata
     */
    public function on_profile_update( $user_id, $old_userdata, $userdata = array() ) {
        // $old_userdata is a WP_User object, $userdata is the new data array.
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

        $this->queue->enqueue( $user_id, $old_email, 'email_change', array(
            'old_email' => $old_email,
            'new_email' => $new_email,
        ) );
    }

    /**
     * WP user deleted (GDPR erasure propagation).
     *
     * @param int      $user_id
     * @param int|null $reassign User ID to reassign posts to, or null.
     */
    public function on_user_deleted( $user_id, $reassign = null ) {
        // We need the email, but the user is already deleted at this point.
        // Retrieve from usermeta or payload.
        $ojs_user_id = get_user_meta( $user_id, '_sea_ojs_user_id', true );

        // We stored the email in the queue at enqueue time. Use a pre-deletion hook
        // to capture the email if needed. For now, try to get it from the queue
        // or log a placeholder.
        // Best approach: hook into 'delete_user' (before deletion) to capture the email.
        // Since we hook 'deleted_user' (after), we'll use the pre-captured email.
        $email = get_user_meta( $user_id, '_sea_ojs_delete_email', true );

        if ( ! $email ) {
            // Fallback: if we didn't capture the email, we can't do much.
            // But we still have the OJS user ID cached, so we can try.
            $email = 'unknown-' . $user_id . '@deleted.local';
        }

        $this->queue->enqueue( $user_id, $email, 'delete_user', array(
            'ojs_user_id' => $ojs_user_id ? (int) $ojs_user_id : null,
        ) );
    }
}

/**
 * Capture user email before deletion (for GDPR queue item).
 * This fires before the user is deleted, so we can still read their data.
 */
function sea_ojs_pre_delete_user( $user_id ) {
    $user = get_userdata( $user_id );
    if ( $user ) {
        update_user_meta( $user_id, '_sea_ojs_delete_email', $user->user_email );
    }
}
add_action( 'delete_user', 'sea_ojs_pre_delete_user' );
