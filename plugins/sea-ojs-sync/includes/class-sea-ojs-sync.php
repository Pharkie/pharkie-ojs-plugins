<?php

if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

class SEA_OJS_Sync {

    /** @var SEA_OJS_API_Client */
    private $api;

    /** @var SEA_OJS_Queue */
    private $queue;

    /** @var SEA_OJS_Logger */
    private $logger;

    /** @var SEA_OJS_Resolver */
    private $resolver;

    /** Retry intervals in seconds: 5 min, 15 min, 1 hour. */
    const RETRY_INTERVALS = array( 300, 900, 3600 );

    /** Max retry attempts. */
    const MAX_RETRIES = 3;

    public function __construct( SEA_OJS_API_Client $api, SEA_OJS_Queue $queue, SEA_OJS_Logger $logger, SEA_OJS_Resolver $resolver ) {
        $this->api      = $api;
        $this->queue    = $queue;
        $this->logger   = $logger;
        $this->resolver = $resolver;
    }

    /**
     * Process a single queue item. Dispatches to the appropriate handler.
     *
     * @param object $item Queue row object.
     */
    public function process( $item ) {
        $this->queue->mark_processing( $item->id );

        $payload = json_decode( $item->payload, true ) ?: array();

        switch ( $item->action ) {
            case 'activate':
                $result = $this->handle_activate( $item, $payload );
                break;
            case 'expire':
                $result = $this->handle_expire( $item, $payload );
                break;
            case 'email_change':
                $result = $this->handle_email_change( $item, $payload );
                break;
            case 'delete_user':
                $result = $this->handle_delete_user( $item, $payload );
                break;
            default:
                $this->logger->log( $item->wp_user_id, $item->email, $item->action, 'fail', 0, 'Unknown action: ' . $item->action );
                $this->queue->mark_permanent_fail( $item->id );
                return;
        }

        $this->finalize( $item, $result );
    }

    /**
     * Handle activate: find-or-create user + create subscription.
     */
    private function handle_activate( $item, $payload ) {
        $user = get_userdata( $item->wp_user_id );
        if ( ! $user ) {
            return array( 'success' => false, 'code' => 0, 'error' => 'WP user not found', 'permanent' => true );
        }

        $email      = $user->user_email;
        $first_name = $user->first_name ?: $user->display_name;
        $last_name  = $user->last_name ?: '';

        // Step 1: Find or create OJS user.
        $result = $this->api->find_or_create_user( $email, $first_name, $last_name, true );
        if ( ! $result['success'] ) {
            return $result;
        }

        $ojs_user_id = $result['body']['userId'];

        // Cache OJS userId in usermeta.
        update_user_meta( $item->wp_user_id, '_sea_ojs_user_id', $ojs_user_id );

        // Log user creation if new.
        if ( ! empty( $result['body']['created'] ) ) {
            $this->logger->log( $item->wp_user_id, $email, 'create_user', 'success', $result['code'], wp_json_encode( $result['body'] ) );
        }

        // Step 2: Resolve subscription data and create subscription.
        $sub_data = $this->resolver->resolve_subscription_data( $item->wp_user_id );
        if ( ! $sub_data || ! $sub_data['type_id'] ) {
            // User is a member but we can't resolve a type — log and complete anyway (user was created).
            $this->logger->log( $item->wp_user_id, $email, 'activate', 'fail', 0, 'Could not resolve subscription type. Check type mapping settings.' );
            return array( 'success' => false, 'code' => 0, 'error' => 'No subscription type resolved', 'permanent' => true );
        }

        $sub_result = $this->api->create_subscription(
            $ojs_user_id,
            $sub_data['type_id'],
            $sub_data['date_start'],
            $sub_data['date_end']
        );

        return $sub_result;
    }

    /**
     * Handle expire: expire subscription by OJS userId.
     */
    private function handle_expire( $item, $payload ) {
        $ojs_user_id = $this->resolve_ojs_user_id( $item->wp_user_id, $item->email );
        if ( ! $ojs_user_id ) {
            // User never synced to OJS. Nothing to expire.
            $this->logger->log( $item->wp_user_id, $item->email, 'expire', 'success', 0, 'User not found on OJS — nothing to expire' );
            return array( 'success' => true, 'code' => 200, 'body' => array(), 'error' => '' );
        }

        $result = $this->api->expire_subscription_by_user( $ojs_user_id );

        // 404 = no subscription to expire — that's fine.
        if ( ! $result['success'] && $result['code'] === 404 ) {
            $this->logger->log( $item->wp_user_id, $item->email, 'expire', 'success', 404, 'No OJS subscription found — nothing to expire' );
            return array( 'success' => true, 'code' => 404, 'body' => array(), 'error' => '' );
        }

        return $result;
    }

    /**
     * Handle email_change: update OJS user email.
     */
    private function handle_email_change( $item, $payload ) {
        $old_email = isset( $payload['old_email'] ) ? $payload['old_email'] : '';
        $new_email = isset( $payload['new_email'] ) ? $payload['new_email'] : '';

        if ( ! $old_email || ! $new_email ) {
            return array( 'success' => false, 'code' => 0, 'error' => 'Missing old/new email in payload', 'permanent' => true );
        }

        $ojs_user_id = $this->resolve_ojs_user_id( $item->wp_user_id, $old_email );
        if ( ! $ojs_user_id ) {
            $this->logger->log( $item->wp_user_id, $old_email, 'email_change', 'success', 0, 'User not found on OJS — nothing to update' );
            return array( 'success' => true, 'code' => 200, 'body' => array(), 'error' => '' );
        }

        $result = $this->api->update_user_email( $ojs_user_id, $new_email );

        // 409 = new email already in use on OJS. Permanent fail, admin must resolve.
        if ( ! $result['success'] && $result['code'] === 409 ) {
            $this->send_admin_alert(
                'OJS Sync: Email Conflict',
                sprintf(
                    "Email change for WP user #%d failed. New email '%s' is already in use on OJS.\nOld email: %s\nManual resolution required in OJS admin.",
                    $item->wp_user_id,
                    $new_email,
                    $old_email
                )
            );
        }

        return $result;
    }

    /**
     * Handle delete_user: GDPR erasure.
     */
    private function handle_delete_user( $item, $payload ) {
        $ojs_user_id = $this->resolve_ojs_user_id( $item->wp_user_id, $item->email );
        if ( ! $ojs_user_id ) {
            $this->logger->log( $item->wp_user_id, $item->email, 'delete_user', 'success', 0, 'User not found on OJS — nothing to delete' );
            // Clean up usermeta just in case.
            delete_user_meta( $item->wp_user_id, '_sea_ojs_user_id' );
            return array( 'success' => true, 'code' => 200, 'body' => array(), 'error' => '' );
        }

        $result = $this->api->delete_user( $ojs_user_id );

        if ( $result['success'] ) {
            delete_user_meta( $item->wp_user_id, '_sea_ojs_user_id' );
        }

        return $result;
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
        $cached = get_user_meta( $wp_user_id, '_sea_ojs_user_id', true );
        if ( $cached ) {
            return (int) $cached;
        }

        // Fall back to API lookup.
        $result = $this->api->find_user( $email );
        if ( $result['success'] && ! empty( $result['body']['found'] ) ) {
            $ojs_user_id = (int) $result['body']['userId'];
            // Cache for future use.
            update_user_meta( $wp_user_id, '_sea_ojs_user_id', $ojs_user_id );
            return $ojs_user_id;
        }

        return null;
    }

    /**
     * Finalize a queue item based on the API result.
     *
     * @param object $item   Queue row.
     * @param array  $result API result array.
     */
    private function finalize( $item, $result ) {
        $code     = isset( $result['code'] ) ? $result['code'] : 0;
        $error    = isset( $result['error'] ) ? $result['error'] : '';
        $body_str = isset( $result['body'] ) ? wp_json_encode( $result['body'] ) : '';
        $attempts = (int) $item->attempts + 1;

        if ( ! empty( $result['success'] ) ) {
            // Success.
            $this->queue->mark_completed( $item->id );
            $this->logger->log( $item->wp_user_id, $item->email, $item->action, 'success', $code, $body_str, $attempts );
            return;
        }

        // Permanent fail?
        $is_permanent = ! empty( $result['permanent'] ) || $this->api->is_permanent_fail( $code );

        if ( $is_permanent ) {
            $this->queue->mark_permanent_fail( $item->id );
            $this->logger->log( $item->wp_user_id, $item->email, $item->action, 'fail', $code, $error . ' | ' . $body_str, $attempts );
            $this->send_admin_alert(
                'OJS Sync: Permanent Failure',
                sprintf(
                    "Action: %s\nEmail: %s\nWP User ID: %d\nHTTP %d: %s\n\nThis item will not be retried. Check OJS Sync settings or resolve manually.",
                    $item->action,
                    $item->email,
                    $item->wp_user_id,
                    $code,
                    $error
                )
            );
            return;
        }

        // Retryable?
        if ( $attempts < self::MAX_RETRIES ) {
            $interval      = self::RETRY_INTERVALS[ $attempts - 1 ] ?? self::RETRY_INTERVALS[2];
            $next_retry_at = gmdate( 'Y-m-d H:i:s', time() + $interval );
            $this->queue->mark_failed( $item->id, $next_retry_at );
            $this->logger->log( $item->wp_user_id, $item->email, $item->action, 'fail', $code, $error . ' | retry scheduled', $attempts );
        } else {
            // Max retries exhausted.
            $this->queue->mark_permanent_fail( $item->id );
            $this->logger->log( $item->wp_user_id, $item->email, $item->action, 'fail', $code, $error . ' | max retries exhausted', $attempts );
            $this->send_admin_alert(
                'OJS Sync: Max Retries Exhausted',
                sprintf(
                    "Action: %s\nEmail: %s\nWP User ID: %d\nHTTP %d: %s\nAttempts: %d\n\nMax retries exhausted. Check OJS availability and review the sync queue.",
                    $item->action,
                    $item->email,
                    $item->wp_user_id,
                    $code,
                    $error,
                    $attempts
                )
            );
        }
    }

    /**
     * Send an admin alert email.
     */
    private function send_admin_alert( $subject, $message ) {
        $to = get_option( 'admin_email' );
        wp_mail( $to, $subject, $message );
    }

    /**
     * Sync a single user directly (for CLI / manual use).
     * Does not go through the queue — calls OJS directly.
     *
     * @param int  $wp_user_id
     * @param bool $dry_run
     * @param bool $send_welcome_email Whether to send welcome email on user creation.
     * @return array Result info.
     */
    public function sync_user( $wp_user_id, $dry_run = false, $send_welcome_email = false ) {
        $user = get_userdata( $wp_user_id );
        if ( ! $user ) {
            return array( 'success' => false, 'message' => 'WP user not found.' );
        }

        $sub_data = $this->resolver->resolve_subscription_data( $wp_user_id );
        if ( ! $sub_data ) {
            return array( 'success' => false, 'message' => 'User is not an active member.' );
        }

        if ( $dry_run ) {
            return array(
                'success' => true,
                'message' => sprintf(
                    'Would sync: %s (type_id=%d, date_end=%s)',
                    $user->user_email,
                    $sub_data['type_id'],
                    $sub_data['date_end'] ?? 'non-expiring'
                ),
            );
        }

        $email      = $user->user_email;
        $first_name = $user->first_name ?: $user->display_name;
        $last_name  = $user->last_name ?: '';

        // Step 1: Find or create OJS user.
        $result = $this->api->find_or_create_user( $email, $first_name, $last_name, $send_welcome_email );
        if ( ! $result['success'] ) {
            $this->logger->log( $wp_user_id, $email, 'activate', 'fail', $result['code'], $result['error'] );
            return array( 'success' => false, 'message' => 'Find-or-create failed: ' . $result['error'] );
        }

        $ojs_user_id = $result['body']['userId'];
        update_user_meta( $wp_user_id, '_sea_ojs_user_id', $ojs_user_id );

        if ( ! empty( $result['body']['created'] ) ) {
            $this->logger->log( $wp_user_id, $email, 'create_user', 'success', $result['code'], wp_json_encode( $result['body'] ) );
        }

        // Step 2: Create subscription.
        $sub_result = $this->api->create_subscription(
            $ojs_user_id,
            $sub_data['type_id'],
            $sub_data['date_start'],
            $sub_data['date_end']
        );

        if ( ! $sub_result['success'] ) {
            $this->logger->log( $wp_user_id, $email, 'activate', 'fail', $sub_result['code'], $sub_result['error'] );
            return array( 'success' => false, 'message' => 'Create subscription failed: ' . $sub_result['error'] );
        }

        $this->logger->log( $wp_user_id, $email, 'activate', 'success', $sub_result['code'], wp_json_encode( $sub_result['body'] ) );

        return array(
            'success' => true,
            'message' => sprintf(
                'Synced: %s → OJS user %d, subscription %s',
                $email,
                $ojs_user_id,
                isset( $sub_result['body']['subscriptionId'] ) ? $sub_result['body']['subscriptionId'] : '?'
            ),
        );
    }
}
