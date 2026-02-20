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

    /** @var SEA_OJS_Queue */
    private $queue;

    /** @var SEA_OJS_Logger */
    private $logger;

    public function __construct( SEA_OJS_Sync $sync, SEA_OJS_Resolver $resolver, SEA_OJS_API_Client $api, SEA_OJS_Queue $queue, SEA_OJS_Logger $logger ) {
        $this->sync     = $sync;
        $this->resolver = $resolver;
        $this->api      = $api;
        $this->queue    = $queue;
        $this->logger   = $logger;
    }

    /**
     * Register cron action hooks.
     */
    public function register() {
        add_action( 'sea_ojs_process_queue', array( $this, 'process_queue' ) );
        add_action( 'sea_ojs_daily_reconcile', array( $this, 'daily_reconcile' ) );
        add_action( 'sea_ojs_daily_digest', array( $this, 'daily_digest' ) );
    }

    /**
     * Process pending/retry queue items. Runs every minute.
     * Respect a 1-minute window — stop if we've been running too long.
     */
    public function process_queue() {
        $start = time();
        $max_runtime = 50; // seconds — leave 10s buffer within the 1-minute interval.

        $items = $this->queue->get_due_items( 10 );

        foreach ( $items as $item ) {
            // Bail if we're running out of time.
            if ( ( time() - $start ) >= $max_runtime ) {
                break;
            }

            $this->sync->process( $item );
        }
    }

    /**
     * Daily reconciliation: compare WP active members vs OJS subscriptions.
     * Queue any drift (missing or expired subscriptions on OJS).
     */
    public function daily_reconcile() {
        $active_members = $this->resolver->get_all_active_members();
        $queued = 0;
        $errors = 0;

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
                continue;
            }

            $subscriptions = $result['body'];

            // If no active subscription on OJS, queue an activate.
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
                $this->queue->enqueue( $wp_user_id, $email, 'activate', array(
                    'source' => 'reconciliation',
                ) );
                $queued++;
            }
        }

        // Also check for OJS subscriptions that should be expired.
        // This is handled by checking users who are NOT active members but have OJS subscriptions.
        // For efficiency, we rely on the normal expire hooks for this.
        // The reconciliation focuses on ensuring active WP members have active OJS subscriptions.

        $this->logger->log(
            0,
            'system',
            'reconcile',
            'success',
            0,
            sprintf( 'Checked %d members, queued %d activations, %d API errors', count( $active_members ), $queued, $errors )
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

        $stats   = $this->queue->get_stats();
        $to      = get_option( 'admin_email' );
        $subject = sprintf( 'OJS Sync Daily Digest: %d failure(s) in the last 24 hours', $count );
        $message = sprintf(
            "OJS Sync Daily Digest\n" .
            "=====================\n\n" .
            "Failures in last 24 hours: %d\n\n" .
            "Queue status:\n" .
            "  Pending: %d\n" .
            "  Processing: %d\n" .
            "  Failed (retrying): %d\n" .
            "  Permanent failures: %d\n" .
            "  Completed: %d\n\n" .
            "Review failures: %s",
            $count,
            $stats['pending'],
            $stats['processing'],
            $stats['failed'],
            $stats['permanent_fail'],
            $stats['completed'],
            admin_url( 'admin.php?page=sea-ojs-sync-log&status=fail' )
        );

        wp_mail( $to, $subject, $message );
    }
}
