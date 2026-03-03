<?php

if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

class WPOJS_Settings {

    /** @var WPOJS_API_Client */
    private $api;

    /** @var WPOJS_Logger|null */
    private $logger;

    public function __construct( WPOJS_API_Client $api, ?WPOJS_Logger $logger = null ) {
        $this->api    = $api;
        $this->logger = $logger;
    }

    /**
     * Register admin menu and settings.
     */
    public function register() {
        add_action( 'admin_menu', array( $this, 'add_menu' ) );
        add_action( 'admin_init', array( $this, 'register_settings' ) );
        add_action( 'wp_ajax_wpojs_test_connection', array( $this, 'ajax_test_connection' ) );
        add_action( 'admin_notices', array( $this, 'render_failure_notice' ) );
    }

    public function add_menu() {
        add_menu_page(
            'OJS Sync',
            'OJS Sync',
            'manage_options',
            'wpojs-sync',
            array( $this, 'render_settings_page' ),
            'dashicons-update',
            80
        );
    }

    public function register_settings() {
        register_setting( 'wpojs_settings', 'wpojs_url', array(
            'sanitize_callback' => array( $this, 'sanitize_ojs_url' ),
        ) );
        register_setting( 'wpojs_settings', 'wpojs_type_mapping', array(
            'sanitize_callback' => array( $this, 'sanitize_type_mapping' ),
        ) );
        register_setting( 'wpojs_settings', 'wpojs_default_type_id', array(
            'sanitize_callback' => 'absint',
        ) );
        register_setting( 'wpojs_settings', 'wpojs_manual_roles', array(
            'sanitize_callback' => array( $this, 'sanitize_roles' ),
        ) );
        register_setting( 'wpojs_settings', 'wpojs_journal_name', array(
            'sanitize_callback' => 'sanitize_text_field',
        ) );

        // Settings section.
        add_settings_section(
            'wpojs_main',
            'OJS Connection',
            null,
            'wpojs-sync'
        );

        add_settings_field(
            'wpojs_url',
            'OJS Base URL',
            array( $this, 'render_url_field' ),
            'wpojs-sync',
            'wpojs_main'
        );

        add_settings_field(
            'wpojs_type_mapping',
            'Subscription Type Mapping',
            array( $this, 'render_type_mapping_field' ),
            'wpojs-sync',
            'wpojs_main'
        );

        add_settings_field(
            'wpojs_default_type_id',
            'Default OJS Subscription Type ID',
            array( $this, 'render_default_type_field' ),
            'wpojs-sync',
            'wpojs_main'
        );

        add_settings_field(
            'wpojs_manual_roles',
            'Manual Member Roles',
            array( $this, 'render_manual_roles_field' ),
            'wpojs-sync',
            'wpojs_main'
        );

        add_settings_field(
            'wpojs_journal_name',
            'Journal Display Name',
            array( $this, 'render_journal_name_field' ),
            'wpojs-sync',
            'wpojs_main'
        );
    }

    public function sanitize_ojs_url( $value ) {
        $url = esc_url_raw( trim( $value ) );

        if ( $url && strpos( $url, 'https://' ) !== 0 ) {
            add_settings_error( 'wpojs_url', 'invalid_url', 'OJS URL must use HTTPS.' );
            return get_option( 'wpojs_url', '' );
        }

        return untrailingslashit( $url );
    }

    public function sanitize_type_mapping( $value ) {
        if ( ! is_array( $value ) ) {
            return array();
        }

        $clean = array();
        foreach ( $value as $product_id => $type_id ) {
            $pid = absint( $product_id );
            $tid = absint( $type_id );
            if ( $pid && $tid ) {
                $clean[ $pid ] = $tid;
            }
        }
        return $clean;
    }

    public function sanitize_roles( $value ) {
        if ( ! is_array( $value ) ) {
            return array();
        }
        return array_map( 'sanitize_text_field', $value );
    }

    // -------------------------------------------------------------------------
    // Render fields
    // -------------------------------------------------------------------------

    public function render_url_field() {
        $value = get_option( 'wpojs_url', '' );
        printf(
            '<input type="url" name="wpojs_url" value="%s" class="regular-text" placeholder="https://journal.example.org/index.php/t1" />' .
            '<p class="description">Include the journal path. API endpoints will be at {base}/api/v1/wpojs/...</p>',
            esc_attr( $value )
        );
    }

    public function render_type_mapping_field() {
        $mapping = get_option( 'wpojs_type_mapping', array() );
        echo '<div id="wpojs-type-mapping">';
        if ( empty( $mapping ) ) {
            $mapping = array( '' => '' );
        }
        foreach ( $mapping as $product_id => $type_id ) {
            printf(
                '<div class="wpojs-mapping-row" style="margin-bottom:5px;">' .
                '<input type="number" name="wpojs_type_mapping[%s]" value="%s" placeholder="OJS Type ID" style="width:120px;" />' .
                ' <span class="description">← WC Product ID: %s</span>' .
                ' <button type="button" class="button wpojs-remove-mapping" style="margin-left:5px;">Remove</button>' .
                '</div>',
                esc_attr( $product_id ),
                esc_attr( $type_id ),
                esc_html( $product_id ?: '(new)' )
            );
        }
        echo '</div>';
        echo '<button type="button" class="button" id="wpojs-add-mapping">+ Add Mapping</button>';
        echo '<p class="description">Map WooCommerce Product IDs to OJS Subscription Type IDs.</p>';

        // Inline JS for add/remove mapping rows.
        ?>
        <script>
        jQuery(function($) {
            $('#wpojs-add-mapping').on('click', function() {
                var html = '<div class="wpojs-mapping-row" style="margin-bottom:5px;">' +
                    '<input type="number" class="wpojs-mapping-pid" value="" placeholder="WC Product ID" style="width:120px;" /> → ' +
                    '<input type="number" class="wpojs-mapping-tid" value="" placeholder="OJS Type ID" style="width:120px;" />' +
                    ' <button type="button" class="button wpojs-remove-mapping" style="margin-left:5px;">Remove</button></div>';
                $('#wpojs-type-mapping').append(html);
            });
            $(document).on('click', '.wpojs-remove-mapping', function() {
                $(this).closest('.wpojs-mapping-row').remove();
            });
            // On submit, rewrite new-row inputs to name="wpojs_type_mapping[{pid}]"
            // so they match the format the sanitize callback expects.
            $('form').on('submit', function() {
                $('#wpojs-type-mapping .wpojs-mapping-row').each(function() {
                    var $pid = $(this).find('.wpojs-mapping-pid');
                    var $tid = $(this).find('.wpojs-mapping-tid');
                    if ($pid.length && $tid.length) {
                        var pid = $pid.val();
                        if (pid) {
                            $tid.attr('name', 'wpojs_type_mapping[' + pid + ']');
                        }
                        $pid.removeAttr('name');
                    }
                });
            });
        });
        </script>
        <?php
    }

    public function render_default_type_field() {
        $value = get_option( 'wpojs_default_type_id', '' );
        printf(
            '<input type="number" name="wpojs_default_type_id" value="%s" class="small-text" />' .
            '<p class="description">OJS Subscription Type ID for manual role members (no WC product mapping).</p>',
            esc_attr( $value )
        );
    }

    public function render_manual_roles_field() {
        $selected = get_option( 'wpojs_manual_roles', array() );
        $all_roles = wp_roles()->get_names();

        echo '<fieldset>';
        foreach ( $all_roles as $slug => $name ) {
            printf(
                '<label style="display:block;margin-bottom:3px;"><input type="checkbox" name="wpojs_manual_roles[]" value="%s" %s /> %s</label>',
                esc_attr( $slug ),
                checked( in_array( $slug, $selected, true ), true, false ),
                esc_html( $name )
            );
        }
        echo '</fieldset>';
        echo '<p class="description">Roles that grant OJS access without a WCS subscription (e.g. Exco/life members).</p>';
    }

    public function render_journal_name_field() {
        $value = get_option( 'wpojs_journal_name', '' );
        printf(
            '<input type="text" name="wpojs_journal_name" value="%s" class="regular-text" placeholder="Journal" />' .
            '<p class="description">Shown in the My Account dashboard widget (e.g. \'Journal of Example Studies\').</p>',
            esc_attr( $value )
        );
    }

    // -------------------------------------------------------------------------
    // Settings page
    // -------------------------------------------------------------------------

    public function render_settings_page() {
        if ( ! current_user_can( 'manage_options' ) ) {
            return;
        }
        ?>
        <div class="wrap">
            <h1>OJS Sync Settings</h1>

            <form method="post" action="options.php">
                <?php
                settings_fields( 'wpojs_settings' );
                do_settings_sections( 'wpojs-sync' );
                submit_button();
                ?>
            </form>

            <hr />

            <h2>Connection Test</h2>
            <p>
                <button type="button" class="button button-secondary" id="wpojs-test-connection">Test Connection</button>
                <span id="wpojs-test-result" style="margin-left:10px;"></span>
            </p>

            <h2>Server Info</h2>
            <table class="form-table">
                <tr>
                    <th>WP Server IP</th>
                    <td>
                        <?php
                        $ip = isset( $_SERVER['SERVER_ADDR'] ) ? sanitize_text_field( $_SERVER['SERVER_ADDR'] ) : 'Unknown';
                        echo esc_html( $ip );
                        ?>
                        <p class="description">Add this IP to the OJS plugin's allowed IP list.</p>
                        <p class="description">This is the server's local IP. Behind a load balancer or NAT, the outbound IP may differ. Verify with your hosting provider or use <code>curl -s https://api.ipify.org</code> from the server.</p>
                    </td>
                </tr>
                <tr>
                    <th>API Key</th>
                    <td>
                        <?php
                        if ( defined( 'WPOJS_API_KEY' ) && WPOJS_API_KEY ) {
                            echo '<span style="color:green;">&#10003; Defined in wp-config.php</span>';
                        } else {
                            echo '<span style="color:red;">&#10007; Not defined. Add <code>define(\'WPOJS_API_KEY\', \'your-key\');</code> to wp-config.php</span>';
                        }
                        ?>
                    </td>
                </tr>
                <tr>
                    <th>Sync Queue</th>
                    <td>
                        <a href="<?php echo esc_url( admin_url( 'admin.php?page=action-scheduler&status=pending&group=wpojs-sync' ) ); ?>">
                            View scheduled actions &rarr;
                        </a>
                        <p class="description">Sync actions are managed by Action Scheduler (Tools &rarr; Scheduled Actions).</p>
                    </td>
                </tr>
            </table>

            <script>
            jQuery(function($) {
                $('#wpojs-test-connection').on('click', function() {
                    var $btn = $(this);
                    var $result = $('#wpojs-test-result');
                    $btn.prop('disabled', true);
                    $result.text('Testing...');

                    $.post(ajaxurl, {
                        action: 'wpojs_test_connection',
                        _wpnonce: '<?php echo wp_create_nonce( 'wpojs_test_connection' ); ?>'
                    }, function(response) {
                        $btn.prop('disabled', false);
                        if (response.success) {
                            $result.empty().append($('<span>').css('color','green').text('\u2713 ' + response.data.message));
                        } else {
                            $result.empty().append($('<span>').css('color','red').text('\u2717 ' + response.data.message));
                        }
                    }).fail(function() {
                        $btn.prop('disabled', false);
                        $result.empty().append($('<span>').css('color','red').text('\u2717 AJAX request failed.'));
                    });
                });
            });
            </script>
        </div>
        <?php
    }

    /**
     * Show admin notice when recent sync failures exceed threshold.
     */
    public function render_failure_notice() {
        if ( ! $this->logger || ! current_user_can( 'manage_options' ) ) {
            return;
        }

        // Only show on dashboard and OJS Sync pages.
        $screen = get_current_screen();
        if ( ! $screen ) {
            return;
        }
        $show_on = array( 'dashboard', 'toplevel_page_wpojs-sync' );
        if ( ! in_array( $screen->id, $show_on, true ) && strpos( $screen->id, 'wpojs' ) === false ) {
            return;
        }

        // Cache the failure count for 15 minutes to avoid repeated DB queries.
        $count = get_transient( 'wpojs_failure_count_24h' );
        if ( $count === false ) {
            $since = gmdate( 'Y-m-d H:i:s', time() - DAY_IN_SECONDS );
            $count = $this->logger->get_failure_count_since( $since );
            set_transient( 'wpojs_failure_count_24h', $count, 15 * MINUTE_IN_SECONDS );
        }

        if ( (int) $count < 5 ) {
            return;
        }

        $log_url = admin_url( 'admin.php?page=wpojs-sync-log&status=fail' );
        printf(
            '<div class="notice notice-warning"><p><strong>OJS Sync:</strong> %d sync failures in the last 24 hours. <a href="%s">View sync log</a></p></div>',
            (int) $count,
            esc_url( $log_url )
        );
    }

    /**
     * AJAX handler for test connection.
     */
    public function ajax_test_connection() {
        check_ajax_referer( 'wpojs_test_connection' );

        if ( ! current_user_can( 'manage_options' ) ) {
            wp_send_json_error( array( 'message' => 'Insufficient permissions.' ) );
        }

        // Create a fresh client to pick up any just-saved settings.
        $client = new WPOJS_API_Client();
        $result = $client->test_connection();

        // Validate type mapping configuration.
        $warnings = array();
        $mapping     = get_option( 'wpojs_type_mapping', array() );
        $default_tid = get_option( 'wpojs_default_type_id', 0 );

        if ( empty( $mapping ) && empty( $default_tid ) ) {
            $warnings[] = 'No subscription type mapping configured and no default type set. Sync will fail for all members.';
        }

        if ( ! empty( $mapping ) && function_exists( 'wc_get_product' ) ) {
            foreach ( $mapping as $product_id => $type_id ) {
                $product = wc_get_product( (int) $product_id );
                if ( ! $product ) {
                    $warnings[] = sprintf( 'WC Product ID %d in type mapping does not exist.', $product_id );
                }
            }
        }

        if ( ! empty( $warnings ) ) {
            $result['warnings'] = $warnings;
        }

        if ( $result['ok'] ) {
            if ( ! empty( $warnings ) ) {
                $result['message'] .= ' Warnings: ' . implode( ' ', $warnings );
            }
            wp_send_json_success( $result );
        } else {
            wp_send_json_error( $result );
        }
    }
}
