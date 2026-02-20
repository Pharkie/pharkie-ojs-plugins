<?php

if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

class SEA_OJS_Queue {

    private $table;

    public function __construct() {
        global $wpdb;
        $this->table = $wpdb->prefix . 'sea_ojs_sync_queue';
    }

    /**
     * Add an item to the queue. Dedup: skip if identical pending/processing item exists.
     *
     * @param int    $wp_user_id
     * @param string $email
     * @param string $action     activate|expire|email_change|delete_user
     * @param array  $payload    Data needed for the OJS call.
     * @return int|false Inserted row ID, or false if deduped.
     */
    public function enqueue( $wp_user_id, $email, $action, $payload = array() ) {
        global $wpdb;

        // Dedup: skip if an identical pending/processing item already exists for this user+action.
        $existing = $wpdb->get_var( $wpdb->prepare(
            "SELECT id FROM {$this->table}
             WHERE wp_user_id = %d AND action = %s AND status IN ('pending', 'processing')
             LIMIT 1",
            $wp_user_id,
            $action
        ) );

        if ( $existing ) {
            return false;
        }

        $wpdb->insert(
            $this->table,
            array(
                'wp_user_id' => absint( $wp_user_id ),
                'email'      => sanitize_email( $email ),
                'action'     => sanitize_text_field( $action ),
                'payload'    => wp_json_encode( $payload ),
                'status'     => 'pending',
                'attempts'   => 0,
                'created_at' => current_time( 'mysql', true ),
            ),
            array( '%d', '%s', '%s', '%s', '%s', '%d', '%s' )
        );

        return $wpdb->insert_id ?: false;
    }

    /**
     * Get items due for processing.
     * Pending items, or failed items past their retry time.
     */
    public function get_due_items( $limit = 10 ) {
        global $wpdb;

        $now = current_time( 'mysql', true );

        return $wpdb->get_results( $wpdb->prepare(
            "SELECT * FROM {$this->table}
             WHERE status = 'pending'
                OR (status = 'failed' AND next_retry_at <= %s)
             ORDER BY created_at ASC
             LIMIT %d",
            $now,
            $limit
        ) );
    }

    /**
     * Mark item as currently being processed.
     */
    public function mark_processing( $id ) {
        global $wpdb;
        $wpdb->update(
            $this->table,
            array( 'status' => 'processing' ),
            array( 'id' => absint( $id ) ),
            array( '%s' ),
            array( '%d' )
        );
    }

    /**
     * Mark item as successfully completed.
     */
    public function mark_completed( $id ) {
        global $wpdb;
        $wpdb->update(
            $this->table,
            array(
                'status'       => 'completed',
                'completed_at' => current_time( 'mysql', true ),
            ),
            array( 'id' => absint( $id ) ),
            array( '%s', '%s' ),
            array( '%d' )
        );
    }

    /**
     * Mark item as failed with a retry time.
     */
    public function mark_failed( $id, $next_retry_at ) {
        global $wpdb;
        $wpdb->update(
            $this->table,
            array(
                'status'        => 'failed',
                'attempts'      => $this->get_attempts( $id ) + 1,
                'next_retry_at' => $next_retry_at,
            ),
            array( 'id' => absint( $id ) ),
            array( '%s', '%d', '%s' ),
            array( '%d' )
        );
    }

    /**
     * Mark item as permanently failed (will not be retried).
     */
    public function mark_permanent_fail( $id ) {
        global $wpdb;
        $wpdb->update(
            $this->table,
            array(
                'status'       => 'permanent_fail',
                'attempts'     => $this->get_attempts( $id ) + 1,
                'completed_at' => current_time( 'mysql', true ),
            ),
            array( 'id' => absint( $id ) ),
            array( '%s', '%d', '%s' ),
            array( '%d' )
        );
    }

    /**
     * Get current attempt count for an item.
     */
    private function get_attempts( $id ) {
        global $wpdb;
        return (int) $wpdb->get_var( $wpdb->prepare(
            "SELECT attempts FROM {$this->table} WHERE id = %d",
            $id
        ) );
    }

    /**
     * Get a single queue item by ID.
     */
    public function get_item( $id ) {
        global $wpdb;
        return $wpdb->get_row( $wpdb->prepare(
            "SELECT * FROM {$this->table} WHERE id = %d",
            $id
        ) );
    }

    /**
     * Reset a failed/permanent_fail item back to pending for manual retry.
     */
    public function retry_item( $id ) {
        global $wpdb;
        $wpdb->update(
            $this->table,
            array(
                'status'        => 'pending',
                'next_retry_at' => null,
                'completed_at'  => null,
            ),
            array( 'id' => absint( $id ) ),
            array( '%s', '%s', '%s' ),
            array( '%d' )
        );
    }

    /**
     * Get counts grouped by status.
     */
    public function get_stats() {
        global $wpdb;

        $rows = $wpdb->get_results(
            "SELECT status, COUNT(*) as count FROM {$this->table} GROUP BY status"
        );

        $stats = array(
            'pending'        => 0,
            'processing'     => 0,
            'failed'         => 0,
            'permanent_fail' => 0,
            'completed'      => 0,
        );

        foreach ( $rows as $row ) {
            $stats[ $row->status ] = (int) $row->count;
        }

        return $stats;
    }

    /**
     * Get paginated queue items with filters for admin page.
     */
    public function get_items( $args = array() ) {
        global $wpdb;

        $defaults = array(
            'status'   => '',
            'email'    => '',
            'per_page' => 20,
            'offset'   => 0,
            'orderby'  => 'created_at',
            'order'    => 'DESC',
        );
        $args = wp_parse_args( $args, $defaults );

        $where = array( '1=1' );
        $values = array();

        if ( $args['status'] ) {
            $where[]  = 'status = %s';
            $values[] = $args['status'];
        }

        if ( $args['email'] ) {
            $where[]  = 'email LIKE %s';
            $values[] = '%' . $wpdb->esc_like( $args['email'] ) . '%';
        }

        $allowed_orderby = array( 'created_at', 'status', 'email', 'action', 'attempts' );
        $orderby = in_array( $args['orderby'], $allowed_orderby, true ) ? $args['orderby'] : 'created_at';
        $order   = strtoupper( $args['order'] ) === 'ASC' ? 'ASC' : 'DESC';

        $where_clause = implode( ' AND ', $where );

        $count_sql = "SELECT COUNT(*) FROM {$this->table} WHERE {$where_clause}";
        $sql       = "SELECT * FROM {$this->table} WHERE {$where_clause} ORDER BY {$orderby} {$order} LIMIT %d OFFSET %d";

        $values[] = $args['per_page'];
        $values[] = $args['offset'];

        if ( ! empty( $values ) ) {
            $total = $wpdb->get_var( $wpdb->prepare( $count_sql, array_slice( $values, 0, -2 ) ?: null ) );
            $items = $wpdb->get_results( $wpdb->prepare( $sql, $values ) );
        } else {
            $total = $wpdb->get_var( $count_sql );
            $items = $wpdb->get_results( $sql );
        }

        return array(
            'items' => $items,
            'total' => (int) $total,
        );
    }
}
