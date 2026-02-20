<?php

if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

class SEA_OJS_Settings {

    /** @var SEA_OJS_API_Client */
    private $api;

    public function __construct( SEA_OJS_API_Client $api ) {
        $this->api = $api;
    }

    /**
     * Register admin menu and settings.
     */
    public function register() {
        add_action( 'admin_menu', array( $this, 'add_menu' ) );
        add_action( 'admin_init', array( $this, 'register_settings' ) );
        add_action( 'wp_ajax_sea_ojs_test_connection', array( $this, 'ajax_test_connection' ) );
    }

    public function add_menu() {
        add_menu_page(
            'OJS Sync',
            'OJS Sync',
            'manage_options',
            'sea-ojs-sync',
            array( $this, 'render_settings_page' ),
            'dashicons-update',
            80
        );
    }

    public function register_settings() {
        register_setting( 'sea_ojs_settings', 'sea_ojs_url', array(
            'sanitize_callback' => array( $this, 'sanitize_ojs_url' ),
        ) );
        register_setting( 'sea_ojs_settings', 'sea_ojs_type_mapping', array(
            'sanitize_callback' => array( $this, 'sanitize_type_mapping' ),
        ) );
        register_setting( 'sea_ojs_settings', 'sea_ojs_default_type_id', array(
            'sanitize_callback' => 'absint',
        ) );
        register_setting( 'sea_ojs_settings', 'sea_ojs_manual_roles', array(
            'sanitize_callback' => array( $this, 'sanitize_roles' ),
        ) );

        // Settings section.
        add_settings_section(
            'sea_ojs_main',
            'OJS Connection',
            null,
            'sea-ojs-sync'
        );

        add_settings_field(
            'sea_ojs_url',
            'OJS Base URL',
            array( $this, 'render_url_field' ),
            'sea-ojs-sync',
            'sea_ojs_main'
        );

        add_settings_field(
            'sea_ojs_type_mapping',
            'Subscription Type Mapping',
            array( $this, 'render_type_mapping_field' ),
            'sea-ojs-sync',
            'sea_ojs_main'
        );

        add_settings_field(
            'sea_ojs_default_type_id',
            'Default OJS Subscription Type ID',
            array( $this, 'render_default_type_field' ),
            'sea-ojs-sync',
            'sea_ojs_main'
        );

        add_settings_field(
            'sea_ojs_manual_roles',
            'Manual Member Roles',
            array( $this, 'render_manual_roles_field' ),
            'sea-ojs-sync',
            'sea_ojs_main'
        );
    }

    public function sanitize_ojs_url( $value ) {
        $url = esc_url_raw( trim( $value ) );

        if ( $url && strpos( $url, 'https://' ) !== 0 ) {
            add_settings_error( 'sea_ojs_url', 'invalid_url', 'OJS URL must use HTTPS.' );
            return get_option( 'sea_ojs_url', '' );
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
        $value = get_option( 'sea_ojs_url', '' );
        printf(
            '<input type="url" name="sea_ojs_url" value="%s" class="regular-text" placeholder="https://journal.example.org/index.php/t1" />' .
            '<p class="description">Include the journal path. API endpoints will be at {base}/api/v1/sea/...</p>',
            esc_attr( $value )
        );
    }

    public function render_type_mapping_field() {
        $mapping = get_option( 'sea_ojs_type_mapping', array() );
        echo '<div id="sea-ojs-type-mapping">';
        if ( empty( $mapping ) ) {
            $mapping = array( '' => '' );
        }
        foreach ( $mapping as $product_id => $type_id ) {
            printf(
                '<div class="sea-ojs-mapping-row" style="margin-bottom:5px;">' .
                '<input type="number" name="sea_ojs_type_mapping[%s]" value="%s" placeholder="OJS Type ID" style="width:120px;" />' .
                ' <span class="description">← WC Product ID: %s</span>' .
                ' <button type="button" class="button sea-ojs-remove-mapping" style="margin-left:5px;">Remove</button>' .
                '</div>',
                esc_attr( $product_id ),
                esc_attr( $type_id ),
                esc_html( $product_id ?: '(new)' )
            );
        }
        echo '</div>';
        echo '<button type="button" class="button" id="sea-ojs-add-mapping">+ Add Mapping</button>';
        echo '<p class="description">Map WooCommerce Product IDs to OJS Subscription Type IDs.</p>';

        // Inline JS for add/remove mapping rows.
        ?>
        <script>
        jQuery(function($) {
            var counter = 100;
            $('#sea-ojs-add-mapping').on('click', function() {
                var key = 'new_' + counter++;
                var html = '<div class="sea-ojs-mapping-row" style="margin-bottom:5px;">' +
                    '<input type="number" name="sea_ojs_type_mapping_keys[]" value="" placeholder="WC Product ID" style="width:120px;" /> → ' +
                    '<input type="number" name="sea_ojs_type_mapping_vals[]" value="" placeholder="OJS Type ID" style="width:120px;" />' +
                    ' <button type="button" class="button sea-ojs-remove-mapping" style="margin-left:5px;">Remove</button></div>';
                $('#sea-ojs-type-mapping').append(html);
            });
            $(document).on('click', '.sea-ojs-remove-mapping', function() {
                $(this).closest('.sea-ojs-mapping-row').remove();
            });
        });
        </script>
        <?php
    }

    public function render_default_type_field() {
        $value = get_option( 'sea_ojs_default_type_id', '' );
        printf(
            '<input type="number" name="sea_ojs_default_type_id" value="%s" class="small-text" />' .
            '<p class="description">OJS Subscription Type ID for manual role members (no WC product mapping).</p>',
            esc_attr( $value )
        );
    }

    public function render_manual_roles_field() {
        $selected = get_option( 'sea_ojs_manual_roles', array() );
        $all_roles = wp_roles()->get_names();

        echo '<fieldset>';
        foreach ( $all_roles as $slug => $name ) {
            printf(
                '<label style="display:block;margin-bottom:3px;"><input type="checkbox" name="sea_ojs_manual_roles[]" value="%s" %s /> %s</label>',
                esc_attr( $slug ),
                checked( in_array( $slug, $selected, true ), true, false ),
                esc_html( $name )
            );
        }
        echo '</fieldset>';
        echo '<p class="description">Roles that grant OJS access without a WCS subscription (e.g. Exco/life members).</p>';
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
                settings_fields( 'sea_ojs_settings' );
                do_settings_sections( 'sea-ojs-sync' );
                submit_button();
                ?>
            </form>

            <hr />

            <h2>Connection Test</h2>
            <p>
                <button type="button" class="button button-secondary" id="sea-ojs-test-connection">Test Connection</button>
                <span id="sea-ojs-test-result" style="margin-left:10px;"></span>
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
                    </td>
                </tr>
                <tr>
                    <th>API Key</th>
                    <td>
                        <?php
                        if ( defined( 'SEA_OJS_API_KEY' ) && SEA_OJS_API_KEY ) {
                            echo '<span style="color:green;">&#10003; Defined in wp-config.php</span>';
                        } else {
                            echo '<span style="color:red;">&#10007; Not defined. Add <code>define(\'SEA_OJS_API_KEY\', \'your-key\');</code> to wp-config.php</span>';
                        }
                        ?>
                    </td>
                </tr>
                <tr>
                    <th>Queue Status</th>
                    <td>
                        <?php
                        $queue = new SEA_OJS_Queue();
                        $stats = $queue->get_stats();
                        printf(
                            'Pending: %d | Processing: %d | Failed: %d | Permanent: %d | Completed: %d',
                            $stats['pending'],
                            $stats['processing'],
                            $stats['failed'],
                            $stats['permanent_fail'],
                            $stats['completed']
                        );
                        ?>
                    </td>
                </tr>
            </table>

            <script>
            jQuery(function($) {
                $('#sea-ojs-test-connection').on('click', function() {
                    var $btn = $(this);
                    var $result = $('#sea-ojs-test-result');
                    $btn.prop('disabled', true);
                    $result.text('Testing...');

                    $.post(ajaxurl, {
                        action: 'sea_ojs_test_connection',
                        _wpnonce: '<?php echo wp_create_nonce( 'sea_ojs_test_connection' ); ?>'
                    }, function(response) {
                        $btn.prop('disabled', false);
                        if (response.success) {
                            $result.html('<span style="color:green;">&#10003; ' + response.data.message + '</span>');
                        } else {
                            $result.html('<span style="color:red;">&#10007; ' + response.data.message + '</span>');
                        }
                    }).fail(function() {
                        $btn.prop('disabled', false);
                        $result.html('<span style="color:red;">AJAX request failed.</span>');
                    });
                });
            });
            </script>
        </div>
        <?php
    }

    /**
     * AJAX handler for test connection.
     */
    public function ajax_test_connection() {
        check_ajax_referer( 'sea_ojs_test_connection' );

        if ( ! current_user_can( 'manage_options' ) ) {
            wp_send_json_error( array( 'message' => 'Insufficient permissions.' ) );
        }

        // Create a fresh client to pick up any just-saved settings.
        $client = new SEA_OJS_API_Client();
        $result = $client->test_connection();

        if ( $result['ok'] ) {
            wp_send_json_success( $result );
        } else {
            wp_send_json_error( $result );
        }
    }
}
