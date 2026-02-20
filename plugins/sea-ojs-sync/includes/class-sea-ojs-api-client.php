<?php

if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

class SEA_OJS_API_Client {

    private $base_url;
    private $api_key;
    private $timeout;

    public function __construct() {
        $this->base_url = untrailingslashit( get_option( 'sea_ojs_url', '' ) );
        $this->api_key  = defined( 'SEA_OJS_API_KEY' ) ? SEA_OJS_API_KEY : '';
        $this->timeout  = 30;
    }

    /**
     * Ping — no auth, pure reachability probe.
     */
    public function ping() {
        return $this->get( '/sea/ping', array(), false );
    }

    /**
     * Preflight — verify compatibility (requires auth).
     */
    public function preflight() {
        return $this->get( '/sea/preflight' );
    }

    /**
     * Find user by email. Returns ['success' => true, 'body' => ['found' => bool, ...]].
     */
    public function find_user( $email ) {
        return $this->get( '/sea/users', array( 'email' => $email ) );
    }

    /**
     * Find or create a user. Returns ['success' => true, 'body' => ['userId' => int, 'created' => bool]].
     */
    public function find_or_create_user( $email, $first_name, $last_name, $send_welcome_email = true ) {
        return $this->post( '/sea/users/find-or-create', array(
            'email'            => $email,
            'firstName'        => $first_name,
            'lastName'         => $last_name,
            'sendWelcomeEmail' => $send_welcome_email,
        ) );
    }

    /**
     * Update a user's email address. Returns 409 if new email already in use.
     */
    public function update_user_email( $user_id, $new_email ) {
        return $this->request( 'PUT', '/sea/users/' . absint( $user_id ) . '/email', array(
            'newEmail' => $new_email,
        ) );
    }

    /**
     * Delete (anonymise) a user. GDPR erasure.
     */
    public function delete_user( $user_id ) {
        return $this->request( 'DELETE', '/sea/users/' . absint( $user_id ) );
    }

    /**
     * Create or renew a subscription. Idempotent upsert.
     */
    public function create_subscription( $user_id, $type_id, $date_start, $date_end = null ) {
        $body = array(
            'userId'    => absint( $user_id ),
            'typeId'    => absint( $type_id ),
            'dateStart' => $date_start,
        );
        if ( $date_end !== null ) {
            $body['dateEnd'] = $date_end;
        } else {
            $body['dateEnd'] = null;
        }
        return $this->post( '/sea/subscriptions', $body );
    }

    /**
     * Expire subscription by user ID. 404 if no subscription found.
     */
    public function expire_subscription_by_user( $user_id ) {
        return $this->request( 'PUT', '/sea/subscriptions/expire-by-user/' . absint( $user_id ) );
    }

    /**
     * Send welcome email to an OJS user. Dedup: safe to call repeatedly.
     */
    public function send_welcome_email( $user_id ) {
        return $this->post( '/sea/welcome-email', array(
            'userId' => absint( $user_id ),
        ) );
    }

    /**
     * Get subscriptions by email or userId.
     */
    public function get_subscriptions( $args = array() ) {
        return $this->get( '/sea/subscriptions', $args );
    }

    /**
     * Test connection: ping then preflight. Returns diagnostic array.
     */
    public function test_connection() {
        $ping = $this->ping();
        if ( ! $ping['success'] ) {
            return array(
                'ok'      => false,
                'stage'   => 'ping',
                'message' => 'OJS not reachable: ' . $ping['error'],
            );
        }

        $preflight = $this->preflight();
        if ( ! $preflight['success'] ) {
            $code = $preflight['code'];
            if ( $code === 403 ) {
                $message = 'OJS reachable but access denied (IP not allowlisted or insufficient role).';
            } elseif ( $code === 401 ) {
                $message = 'OJS reachable but authentication failed (check API key).';
            } else {
                $message = 'OJS reachable but preflight failed: ' . $preflight['error'];
            }
            return array(
                'ok'      => false,
                'stage'   => 'preflight',
                'code'    => $code,
                'message' => $message,
            );
        }

        $body = $preflight['body'];
        if ( isset( $body['compatible'] ) && ! $body['compatible'] ) {
            $failed_checks = array();
            if ( isset( $body['checks'] ) ) {
                foreach ( $body['checks'] as $check ) {
                    if ( ! $check['ok'] ) {
                        $failed_checks[] = $check['name'];
                    }
                }
            }
            return array(
                'ok'            => false,
                'stage'         => 'compatibility',
                'message'       => 'OJS reachable and authenticated but incompatible. Failed checks: ' . implode( ', ', $failed_checks ),
                'failed_checks' => $failed_checks,
            );
        }

        return array(
            'ok'      => true,
            'message' => 'Connection successful. OJS is reachable, authenticated, and compatible.',
        );
    }

    // -------------------------------------------------------------------------
    // HTTP helpers
    // -------------------------------------------------------------------------

    private function get( $endpoint, $query_args = array(), $auth = true ) {
        $url = $this->build_url( $endpoint );
        if ( ! empty( $query_args ) ) {
            $url = add_query_arg( $query_args, $url );
        }

        $args = array(
            'timeout' => $this->timeout,
            'headers' => $this->headers( $auth ),
        );

        $response = wp_remote_get( $url, $args );
        return $this->parse_response( $response );
    }

    private function post( $endpoint, $body = array() ) {
        $url  = $this->build_url( $endpoint );
        $args = array(
            'timeout' => $this->timeout,
            'headers' => array_merge( $this->headers(), array(
                'Content-Type' => 'application/json',
            ) ),
            'body' => wp_json_encode( $body ),
        );

        $response = wp_remote_post( $url, $args );
        return $this->parse_response( $response );
    }

    private function request( $method, $endpoint, $body = null ) {
        $url  = $this->build_url( $endpoint );
        $args = array(
            'method'  => $method,
            'timeout' => $this->timeout,
            'headers' => $this->headers(),
        );
        if ( $body !== null ) {
            $args['headers']['Content-Type'] = 'application/json';
            $args['body'] = wp_json_encode( $body );
        }

        $response = wp_remote_request( $url, $args );
        return $this->parse_response( $response );
    }

    private function build_url( $endpoint ) {
        // base_url includes journal path, e.g. https://journal.example.org/index.php/t1
        // API endpoints are at {base}/api/v1/sea/...
        return $this->base_url . '/api/v1' . $endpoint;
    }

    private function headers( $auth = true ) {
        $headers = array();
        if ( $auth && $this->api_key ) {
            $headers['Authorization'] = 'Bearer ' . $this->api_key;
        }
        return $headers;
    }

    /**
     * Parse wp_remote_* response into a standard result array.
     *
     * @return array ['success' => bool, 'code' => int, 'body' => array, 'error' => string]
     */
    private function parse_response( $response ) {
        if ( is_wp_error( $response ) ) {
            return array(
                'success' => false,
                'code'    => 0,
                'body'    => array(),
                'error'   => $response->get_error_message(),
            );
        }

        $code = wp_remote_retrieve_response_code( $response );
        $raw  = wp_remote_retrieve_body( $response );
        $body = json_decode( $raw, true );

        if ( ! is_array( $body ) ) {
            $body = array();
        }

        $success = $code >= 200 && $code < 300;
        $error   = '';
        if ( ! $success ) {
            $error = isset( $body['error'] ) ? $body['error'] : "HTTP $code";
        }

        return array(
            'success' => $success,
            'code'    => $code,
            'body'    => $body,
            'error'   => $error,
        );
    }

    /**
     * Is this a permanent failure (no point retrying)?
     * 4xx errors except 404 (which means "nothing to do").
     */
    public function is_permanent_fail( $code ) {
        return $code >= 400 && $code < 500 && $code !== 404;
    }

    /**
     * Is this a retryable failure?
     * 5xx server errors or 0 (network error / timeout).
     */
    public function is_retryable( $code ) {
        return $code === 0 || ( $code >= 500 && $code < 600 );
    }
}
