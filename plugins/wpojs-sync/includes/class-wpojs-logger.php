<?php

if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

class WPOJS_Logger {

    private $table;

    public function __construct() {
        global $wpdb;
        $this->table = $wpdb->prefix . 'wpojs_sync_log';
    }

    /**
     * Write a log entry.
     *
     * @param int    $wp_user_id
     * @param string $email
     * @param string $action          activate|expire|create_user|email_change|welcome_email|delete_user
     * @param string $status          success|fail
     * @param int    $response_code   HTTP status code from OJS.
     * @param string $response_body   Truncated response body.
     * @param int    $attempt_count
     * @param string $request_id      UUID v4 request ID for cross-system tracing.
     */
    public function log( $wp_user_id, $email, $action, $status, $response_code = null, $response_body = '', $attempt_count = 1, $request_id = '' ) {
        global $wpdb;

        // Truncate response body to 1000 chars for storage.
        if ( strlen( $response_body ) > 1000 ) {
            $response_body = substr( $response_body, 0, 1000 ) . '...(truncated)';
        }

        $wpdb->insert(
            $this->table,
            array(
                'wp_user_id'        => absint( $wp_user_id ),
                'email'             => sanitize_text_field( $email ),
                'action'            => sanitize_text_field( $action ),
                'status'            => sanitize_text_field( $status ),
                'ojs_response_code' => $response_code ? absint( $response_code ) : null,
                'ojs_response_body' => $response_body,
                'attempt_count'     => absint( $attempt_count ),
                'request_id'        => $request_id ? sanitize_text_field( $request_id ) : null,
                'created_at'        => current_time( 'mysql', true ),
            ),
            array( '%d', '%s', '%s', '%s', '%d', '%s', '%d', '%s', '%s' )
        );
    }

    /**
     * Get paginated log entries with filters.
     *
     * @param array $args {
     *     @type string $status    Filter by status (success|fail).
     *     @type string $email     Filter by email (partial match).
     *     @type string $date_from Filter: entries on or after this date (Y-m-d).
     *     @type string $date_to   Filter: entries on or before this date (Y-m-d).
     *     @type int    $per_page  Results per page. Default 20.
     *     @type int    $offset    Offset. Default 0.
     * }
     * @return array ['items' => array, 'total' => int]
     */
    public function get_entries( $args = array() ) {
        global $wpdb;

        $defaults = array(
            'status'    => '',
            'email'     => '',
            'date_from' => '',
            'date_to'   => '',
            'per_page'  => 20,
            'offset'    => 0,
            'orderby'   => 'created_at',
            'order'     => 'DESC',
        );
        $args = wp_parse_args( $args, $defaults );

        $where  = array( '1=1' );
        $values = array();

        if ( $args['status'] ) {
            $where[]  = 'status = %s';
            $values[] = $args['status'];
        }

        if ( $args['email'] ) {
            $where[]  = 'email LIKE %s';
            $values[] = '%' . $wpdb->esc_like( $args['email'] ) . '%';
        }

        if ( $args['date_from'] ) {
            $where[]  = 'created_at >= %s';
            $values[] = sanitize_text_field( $args['date_from'] ) . ' 00:00:00';
        }

        if ( $args['date_to'] ) {
            $where[]  = 'created_at <= %s';
            $values[] = sanitize_text_field( $args['date_to'] ) . ' 23:59:59';
        }

        $allowed_orderby = array( 'created_at', 'status', 'email', 'action' );
        $orderby = in_array( $args['orderby'], $allowed_orderby, true ) ? $args['orderby'] : 'created_at';
        $order   = strtoupper( $args['order'] ) === 'ASC' ? 'ASC' : 'DESC';

        $where_clause = implode( ' AND ', $where );

        $count_sql = "SELECT COUNT(*) FROM {$this->table} WHERE {$where_clause}";
        $sql       = "SELECT * FROM {$this->table} WHERE {$where_clause} ORDER BY {$orderby} {$order} LIMIT %d OFFSET %d";

        $values[] = $args['per_page'];
        $values[] = $args['offset'];

        if ( count( $values ) > 2 ) {
            $total = $wpdb->get_var( $wpdb->prepare( $count_sql, array_slice( $values, 0, -2 ) ) );
            $items = $wpdb->get_results( $wpdb->prepare( $sql, $values ) );
        } else {
            $total = $wpdb->get_var( $count_sql );
            $items = $wpdb->get_results( $wpdb->prepare( $sql, $values ) );
        }

        return array(
            'items' => $items,
            'total' => (int) $total,
        );
    }

    /**
     * Count failures since a given datetime (for daily digest).
     */
    public function get_failure_count_since( $datetime ) {
        global $wpdb;
        return (int) $wpdb->get_var( $wpdb->prepare(
            "SELECT COUNT(*) FROM {$this->table} WHERE status = 'fail' AND created_at >= %s",
            $datetime
        ) );
    }

    /**
     * Anonymize log entries for a deleted user (GDPR).
     * Replaces email with a pseudonym so logs remain useful for diagnostics
     * without retaining PII.
     *
     * @param int    $wp_user_id
     * @param string $email The user's email to search for.
     */
    public function anonymize_user_logs( $wp_user_id, $email ) {
        global $wpdb;

        $anon_email = sprintf( 'deleted-user-%d@anonymised.invalid', $wp_user_id );

        $wpdb->update(
            $this->table,
            array( 'email' => $anon_email ),
            array( 'wp_user_id' => $wp_user_id ),
            array( '%s' ),
            array( '%d' )
        );

        // Also catch any entries logged with this email but a different user ID (edge case).
        if ( $email ) {
            $wpdb->update(
                $this->table,
                array( 'email' => $anon_email ),
                array( 'email' => $email ),
                array( '%s' ),
                array( '%s' )
            );
        }
    }

    /**
     * Delete log entries older than N days.
     */
    public function cleanup_old( $days = 90 ) {
        global $wpdb;
        $cutoff = gmdate( 'Y-m-d H:i:s', time() - ( $days * DAY_IN_SECONDS ) );
        return $wpdb->query( $wpdb->prepare(
            "DELETE FROM {$this->table} WHERE created_at < %s",
            $cutoff
        ) );
    }
}
