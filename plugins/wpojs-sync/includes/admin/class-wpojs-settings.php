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
            'OJS Sync Settings',
            'OJS Sync',
            'manage_options',
            'wpojs-sync',
            array( $this, 'render_settings_page' ),
            'dashicons-update',
            80
        );

        // Override the auto-generated first submenu item label (defaults to parent menu title).
        add_submenu_page(
            'wpojs-sync',
            'OJS Sync Settings',
            'Settings',
            'manage_options',
            'wpojs-sync'
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

        // --- Section: Display ---
        add_settings_section(
            'wpojs_display',
            'Display',
            null,
            'wpojs-sync'
        );

        add_settings_field(
            'wpojs_journal_name',
            'Journal Name',
            array( $this, 'render_journal_name_field' ),
            'wpojs-sync',
            'wpojs_display'
        );

        // --- Section: OJS Connection ---
        add_settings_section(
            'wpojs_connection',
            'OJS Connection',
            null,
            'wpojs-sync'
        );

        add_settings_field(
            'wpojs_url',
            'OJS Base URL',
            array( $this, 'render_url_field' ),
            'wpojs-sync',
            'wpojs_connection'
        );

        add_settings_field(
            'wpojs_test_connection',
            'Test Connection',
            array( $this, 'render_test_connection_field' ),
            'wpojs-sync',
            'wpojs_connection'
        );

        // --- Section: Product-Based Access ---
        add_settings_section(
            'wpojs_product_access',
            'Product-Based Access',
            array( $this, 'render_product_access_intro' ),
            'wpojs-sync'
        );

        add_settings_field(
            'wpojs_type_mapping',
            'WC Product &rarr; OJS Type',
            array( $this, 'render_type_mapping_field' ),
            'wpojs-sync',
            'wpojs_product_access'
        );

        // --- Section: Role-Based Access ---
        add_settings_section(
            'wpojs_role_access',
            'WordPress Role-Based Access',
            array( $this, 'render_role_access_intro' ),
            'wpojs-sync'
        );

        add_settings_field(
            'wpojs_manual_roles',
            'WordPress Roles',
            array( $this, 'render_manual_roles_field' ),
            'wpojs-sync',
            'wpojs_role_access'
        );

        add_settings_field(
            'wpojs_default_type_id',
            'OJS Type',
            array( $this, 'render_default_type_field' ),
            'wpojs-sync',
            'wpojs_role_access'
        );
    }

    public function render_product_access_intro() {
        echo '<p>Members who purchase a WooCommerce Subscription product get an OJS journal subscription automatically. Map each product to an OJS subscription type below.</p>';

        $ojs_types = $this->get_ojs_type_names();
        if ( ! empty( $ojs_types ) ) {
            $parts = array();
            foreach ( $ojs_types as $id => $name ) {
                $parts[] = sprintf( '<strong>%d</strong> = %s', $id, esc_html( $name ) );
            }
            echo '<div style="background:#f0f6fc;border-left:4px solid #2271b1;padding:8px 12px;margin:8px 0 4px;">';
            echo 'Available OJS subscription types: ' . implode( ', ', $parts );
            echo '</div>';
        }
    }

    public function render_role_access_intro() {
        echo '<p>Members with certain WordPress roles can get OJS access without purchasing a product. Useful for committee members, life members, or other honorary access. All ticked roles receive the same OJS type. Changes take effect at the next daily reconciliation or individual sync event.</p>';
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

    /**
     * Fetch OJS subscription type names, cached for 5 minutes.
     * Returns [ type_id => name, ... ] or empty array on failure.
     */
    private function get_ojs_type_names() {
        $cached = get_transient( 'wpojs_ojs_type_names' );
        if ( is_array( $cached ) ) {
            return $cached;
        }

        $result = $this->api->get_subscription_types();
        $names  = array();
        if ( $result['success'] && ! empty( $result['body']['types'] ) ) {
            foreach ( $result['body']['types'] as $type ) {
                $names[ (int) $type['id'] ] = $type['name'];
            }
        }
        set_transient( 'wpojs_ojs_type_names', $names, 5 * MINUTE_IN_SECONDS );
        return $names;
    }

    public function render_type_mapping_field() {
        $mapping    = get_option( 'wpojs_type_mapping', array() );
        $ojs_types  = $this->get_ojs_type_names();

        echo '<div id="wpojs-type-mapping">';
        if ( ! empty( $mapping ) ) {
            foreach ( $mapping as $product_id => $type_id ) {
                // WC product label.
                $product_label = '';
                if ( $product_id && function_exists( 'wc_get_product' ) ) {
                    $product = wc_get_product( (int) $product_id );
                    if ( $product ) {
                        $product_label = sprintf(
                            '&ldquo;%s&rdquo; (#%d)',
                            esc_html( $product->get_name() ),
                            (int) $product_id
                        );
                    } else {
                        $product_label = sprintf(
                            '<span style="color:#d63638;">Product #%d not found &#9888;</span>',
                            (int) $product_id
                        );
                    }
                } elseif ( $product_id ) {
                    $product_label = sprintf( 'Product #%d', (int) $product_id );
                }

                // OJS type label.
                $type_label = '';
                if ( $type_id && isset( $ojs_types[ (int) $type_id ] ) ) {
                    $type_label = sprintf(
                        '<span class="description">&ldquo;%s&rdquo;</span>',
                        esc_html( $ojs_types[ (int) $type_id ] )
                    );
                } elseif ( $type_id && ! empty( $ojs_types ) ) {
                    $type_label = '<span style="color:#d63638;">not found in OJS &#9888;</span>';
                }

                printf(
                    '<div class="wpojs-mapping-row" style="margin-bottom:5px;">' .
                    '<span class="description">WC Product: %s &rarr; OJS Type:</span> ' .
                    '<input type="number" name="wpojs_type_mapping[%s]" value="%s" placeholder="Type ID" style="width:80px;" />' .
                    ' %s' .
                    ' <button type="button" class="button wpojs-remove-mapping" style="margin-left:5px;">Remove</button>' .
                    '</div>',
                    $product_label,
                    esc_attr( $product_id ),
                    esc_attr( $type_id ),
                    $type_label
                );
            }
        }
        echo '</div>';
        echo '<button type="button" class="button" id="wpojs-add-mapping">+ Add Mapping</button>';
        // Inline JS for add/remove mapping rows.
        ?>
        <script>
        jQuery(function($) {
            $('#wpojs-add-mapping').on('click', function() {
                var html = '<div class="wpojs-mapping-row" style="margin-bottom:5px;">' +
                    'WC Product ID: <input type="number" class="wpojs-mapping-pid" value="" placeholder="e.g. 17" style="width:80px;" />' +
                    ' &rarr; OJS Type ID: <input type="number" class="wpojs-mapping-tid" value="" placeholder="e.g. 1" style="width:80px;" />' +
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
        $value     = get_option( 'wpojs_default_type_id', '' );
        $ojs_types = $this->get_ojs_type_names();

        $type_label = '';
        if ( $value && isset( $ojs_types[ (int) $value ] ) ) {
            $type_label = sprintf( ' <span class="description">&ldquo;%s&rdquo;</span>', esc_html( $ojs_types[ (int) $value ] ) );
        } elseif ( $value && ! empty( $ojs_types ) ) {
            $type_label = ' <span style="color:#d63638;">not found in OJS &#9888;</span>';
        }

        printf(
            '<input type="number" name="wpojs_default_type_id" value="%s" class="small-text" />%s' .
            '<p class="description">The OJS subscription type assigned to all members with the roles ticked above.</p>',
            esc_attr( $value ),
            $type_label
        );
    }

    public function render_manual_roles_field() {
        $selected  = get_option( 'wpojs_manual_roles', array() );
        $all_roles = wp_roles()->get_names();

        // Standard WP/WooCommerce roles — shown separately so the list isn't overwhelming.
        $standard_roles = array(
            'administrator', 'editor', 'author', 'contributor',
            'subscriber', 'customer', 'shop_manager',
        );

        $custom_roles  = array();
        $builtin_roles = array();
        foreach ( $all_roles as $slug => $name ) {
            if ( in_array( $slug, $standard_roles, true ) ) {
                $builtin_roles[ $slug ] = $name;
            } else {
                $custom_roles[ $slug ] = $name;
            }
        }

        echo '<fieldset style="display:flex;gap:40px;flex-wrap:wrap;">';

        if ( ! empty( $custom_roles ) ) {
            echo '<div>';
            echo '<strong style="display:block;margin-bottom:6px;">Custom / Membership Roles</strong>';
            foreach ( $custom_roles as $slug => $name ) {
                printf(
                    '<label style="display:block;margin-bottom:3px;"><input type="checkbox" name="wpojs_manual_roles[]" value="%s" %s /> %s</label>',
                    esc_attr( $slug ),
                    checked( in_array( $slug, $selected, true ), true, false ),
                    esc_html( $name )
                );
            }
            echo '</div>';
        }

        echo '<div>';
        echo '<strong style="display:block;margin-bottom:6px;">Standard WordPress Roles</strong>';
        foreach ( $builtin_roles as $slug => $name ) {
            printf(
                '<label style="display:block;margin-bottom:3px;"><input type="checkbox" name="wpojs_manual_roles[]" value="%s" %s /> %s</label>',
                esc_attr( $slug ),
                checked( in_array( $slug, $selected, true ), true, false ),
                esc_html( $name )
            );
        }
        echo '</div>';

        echo '</fieldset>';
    }

    public function render_test_connection_field() {
        echo '<button type="button" class="button button-secondary" id="wpojs-test-connection">Test Connection WP to OJS</button>';
        echo '<span id="wpojs-test-result" style="margin-left:10px;"></span>';
        echo '<p class="description">Tests connectivity to OJS and validates the API key and subscription type IDs.</p>';
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
            <p>Synchronises WooCommerce Subscriptions membership data to OJS (Open Journal Systems). When members purchase or renew a subscription in WooCommerce, their OJS journal access is updated automatically.</p>

            <form method="post" action="options.php">
                <?php
                settings_fields( 'wpojs_settings' );
                do_settings_sections( 'wpojs-sync' );
                submit_button();
                ?>
            </form>

            <hr />

            <h2>Status</h2>
            <table class="form-table">
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
                    <th>WP Server IP</th>
                    <td>
                        <?php
                        $ip = isset( $_SERVER['SERVER_ADDR'] ) ? sanitize_text_field( $_SERVER['SERVER_ADDR'] ) : 'Unknown';
                        echo esc_html( $ip );
                        ?>
                        <p class="description">Ensure this IP is in the OJS plugin's allowed IP list. If connection tests fail with an IP error, check the OJS request log for the actual IP seen by OJS.</p>
                    </td>
                </tr>
                <tr>
                    <th>Sync Queue</th>
                    <td>
                        <a href="<?php echo esc_url( admin_url( 'admin.php?page=action-scheduler&status=pending&s=wpojs' ) ); ?>">
                            View pending OJS Sync actions &rarr;
                        </a>
                        <p class="description">Queued sync actions (activate, expire, delete). Empty when idle.</p>
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
                    $warnings[] = sprintf( 'WC Product #%d in type mapping does not exist.', $product_id );
                }
            }
        }

        // Validate OJS type IDs against what OJS actually has.
        if ( $result['ok'] ) {
            $types_result = $client->get_subscription_types();
            if ( $types_result['success'] && ! empty( $types_result['body']['types'] ) ) {
                $valid_ids = array_map( function ( $t ) {
                    return (int) $t['id'];
                }, $types_result['body']['types'] );

                foreach ( $mapping as $product_id => $type_id ) {
                    if ( ! in_array( (int) $type_id, $valid_ids, true ) ) {
                        $warnings[] = sprintf( 'OJS Type ID %d (mapped from Product #%d) does not exist in OJS — please fix and resave.', $type_id, $product_id );
                    }
                }
                if ( $default_tid && ! in_array( (int) $default_tid, $valid_ids, true ) ) {
                    $warnings[] = sprintf( 'Default OJS Type ID %d does not exist in OJS — please fix and resave.', $default_tid );
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
