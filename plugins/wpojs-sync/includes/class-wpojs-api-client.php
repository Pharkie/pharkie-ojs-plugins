<?php

if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

class WPOJS_API_Client {

    private $base_url;
    private $api_key;
    private $timeout;
    private $last_request_id = '';

    public function __construct() {
        $this->base_url = untrailingslashit( get_option( 'wpojs_url', '' ) );
        $this->api_key  = defined( 'WPOJS_API_KEY' ) ? WPOJS_API_KEY : '';
        $this->timeout  = 30;
    }

    /**
     * Ping — no auth, pure reachability probe.
     */
    public function ping() {
        return $this->get( '/wpojs/ping', array(), false );
    }

    /**
     * Preflight — verify compatibility (requires auth).
     */
    public function preflight() {
        return $this->get( '/wpojs/preflight' );
    }

    /**
     * Get subscription types from OJS.
     */
    public function get_subscription_types() {
        return $this->get( '/wpojs/subscription-types' );
    }

    /**
     * Find user by email. Returns ['success' => true, 'body' => ['found' => bool, ...]].
     */
    public function find_user( $email ) {
        return $this->get( '/wpojs/users', array( 'email' => $email ) );
    }

    /**
     * Find or create a user. Returns ['success' => true, 'body' => ['userId' => int, 'created' => bool]].
     *
     * @param string      $email
     * @param string      $first_name
     * @param string      $last_name
     * @param string|null $password_hash Optional WP password hash to store on OJS.
     */
    public function find_or_create_user( $email, $first_name, $last_name, $password_hash = null ) {
        $body = array(
            'email'    => $email,
            'firstName' => $first_name,
            'lastName'  => $last_name,
        );
        if ( $password_hash !== null ) {
            $body['passwordHash'] = $password_hash;
        }
        return $this->post( '/wpojs/users/find-or-create', $body );
    }

    /**
     * Update a user's email address. Returns 409 if new email already in use.
     */
    public function update_user_email( $user_id, $new_email ) {
        return $this->request( 'PUT', '/wpojs/users/' . absint( $user_id ) . '/email', array(
            'newEmail' => $new_email,
        ) );
    }

    /**
     * Update a user's password hash on OJS.
     */
    public function update_user_password( $user_id, $password_hash ) {
        return $this->request( 'PUT', '/wpojs/users/' . absint( $user_id ) . '/password', array(
            'passwordHash' => $password_hash,
        ) );
    }

    /**
     * Delete (anonymise) a user. GDPR erasure.
     */
    public function delete_user( $user_id ) {
        return $this->request( 'DELETE', '/wpojs/users/' . absint( $user_id ) );
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
        return $this->post( '/wpojs/subscriptions', $body );
    }

    /**
     * Expire subscription by user ID. 404 if no subscription found.
     */
    public function expire_subscription_by_user( $user_id ) {
        return $this->request( 'PUT', '/wpojs/subscriptions/expire-by-user/' . absint( $user_id ) );
    }

    /**
     * Get subscriptions by email or userId.
     */
    public function get_subscriptions( $args = array() ) {
        return $this->get( '/wpojs/subscriptions', $args );
    }

    /**
     * Batch subscription status check. Accepts up to 500 emails.
     *
     * @param array $emails Array of email addresses.
     * @return array ['success' => bool, 'body' => ['results' => ['email' => ['active' => bool, ...]]]]
     */
    public function get_subscription_status_batch( $emails ) {
        return $this->post( '/wpojs/subscriptions/status-batch', array(
            'emails' => $emails,
        ) );
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
        if ( empty( $this->base_url ) ) {
            return array( 'success' => false, 'code' => 0, 'body' => array(), 'error' => 'OJS URL not configured' );
        }
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
        if ( empty( $this->base_url ) ) {
            return array( 'success' => false, 'code' => 0, 'body' => array(), 'error' => 'OJS URL not configured' );
        }
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
        if ( empty( $this->base_url ) ) {
            return array( 'success' => false, 'code' => 0, 'body' => array(), 'error' => 'OJS URL not configured' );
        }
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
        // API endpoints are at {base}/api/v1/wpojs/...
        return $this->base_url . '/api/v1' . $endpoint;
    }

    private function headers( $auth = true ) {
        $headers = array();
        if ( $auth && $this->api_key ) {
            $headers['Authorization'] = 'Bearer ' . $this->api_key;
        }
        $this->last_request_id    = $this->generate_request_id();
        $headers['X-Request-ID']  = $this->last_request_id;
        return $headers;
    }

    /**
     * Get the request ID from the most recent API call.
     *
     * @return string UUID v4 or empty string if no request has been made.
     */
    public function get_last_request_id() {
        return $this->last_request_id;
    }

    /**
     * Generate a UUID v4 for request tracing.
     *
     * @return string
     */
    private function generate_request_id() {
        if ( function_exists( 'wp_generate_uuid4' ) ) {
            return wp_generate_uuid4();
        }
        // Fallback for older WP versions.
        $data    = random_bytes( 16 );
        $data[6] = chr( ord( $data[6] ) & 0x0f | 0x40 );
        $data[8] = chr( ord( $data[8] ) & 0x3f | 0x80 );
        return vsprintf( '%s%s-%s-%s-%s-%s%s%s', str_split( bin2hex( $data ), 4 ) );
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

        // If json_decode failed, try extracting JSON from the end of
        // the response body. PHP deprecation notices (display_errors=On)
        // can prepend HTML to the JSON, breaking the parser.
        if ( ! is_array( $body ) && ! empty( $raw ) ) {
            $json_start = strrpos( $raw, '{' );
            if ( $json_start !== false ) {
                $body = json_decode( substr( $raw, $json_start ), true );
            }
        }

        if ( ! is_array( $body ) ) {
            $body = array();
        }

        $success = $code >= 200 && $code < 300;
        $error   = '';
        if ( ! $success ) {
            $error = isset( $body['error'] ) ? $body['error'] : "HTTP $code";
        }

        $result = array(
            'success' => $success,
            'code'    => $code,
            'body'    => $body,
            'error'   => $error,
        );

        // Capture Retry-After header for 429 responses.
        if ( $code === 429 ) {
            $retry_after = wp_remote_retrieve_header( $response, 'retry-after' );
            $result['retry_after'] = $retry_after ? (int) $retry_after : 60;
        }

        return $result;
    }

    /**
     * Is this a permanent failure (no point retrying)?
     * 4xx errors except 404 (which means "nothing to do").
     */
    public function is_permanent_fail( $code ) {
        return $code >= 400 && $code < 500 && $code !== 404 && $code !== 429;
    }

    /**
     * Is this a retryable failure?
     * 5xx server errors or 0 (network error / timeout).
     */
    public function is_retryable( $code ) {
        return $code === 0 || $code === 429 || ( $code >= 500 && $code < 600 );
    }
}
