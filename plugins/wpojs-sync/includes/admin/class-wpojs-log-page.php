<?php

if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

if ( ! class_exists( 'WP_List_Table' ) ) {
    require_once ABSPATH . 'wp-admin/includes/class-wp-list-table.php';
}

class WPOJS_Log_Page {

    /** @var WPOJS_Logger */
    private $logger;

    /** @var WPOJS_Stats */
    private $stats;

    public function __construct( WPOJS_Logger $logger, WPOJS_Stats $stats ) {
        $this->logger = $logger;
        $this->stats  = $stats;
    }

    public function register() {
        add_action( 'admin_menu', array( $this, 'add_submenu' ) );
    }

    public function add_submenu() {
        add_submenu_page(
            'wpojs-sync',
            'Sync Log',
            'Sync Log',
            'manage_options',
            'wpojs-sync-log',
            array( $this, 'render_page' )
        );
    }

    public function render_page() {
        if ( ! current_user_can( 'manage_options' ) ) {
            return;
        }

        $table = new WPOJS_Log_List_Table( $this->logger );
        $table->prepare_items();

        // Gather stats for summary cards.
        $active   = $this->stats->get_active_member_count();
        $synced   = $this->stats->get_synced_member_count();
        $fail_24h = $this->stats->get_failure_count_hours( 24 );
        $fail_7d  = $this->stats->get_failure_count_days( 7 );
        $rate_7d  = $this->stats->get_success_rate_days( 7 );
        $queue    = $this->stats->get_queue_counts();

        ?>
        <div class="wrap">
            <h1>OJS Sync Log</h1>

            <?php $this->render_stats_cards( $active, $synced, $fail_24h, $fail_7d, $rate_7d, $queue ); ?>

            <form method="get">
                <input type="hidden" name="page" value="wpojs-sync-log" />

                <div class="tablenav top" style="margin-bottom: 10px;">
                    <label>
                        Status:
                        <select name="status">
                            <option value="">All</option>
                            <option value="success" <?php selected( isset( $_GET['status'] ) ? $_GET['status'] : '', 'success' ); ?>>Success</option>
                            <option value="fail" <?php selected( isset( $_GET['status'] ) ? $_GET['status'] : '', 'fail' ); ?>>Fail</option>
                        </select>
                    </label>

                    <label style="margin-left: 10px;">
                        Email:
                        <input type="text" name="email" value="<?php echo esc_attr( isset( $_GET['email'] ) ? $_GET['email'] : '' ); ?>" placeholder="Search email..." />
                    </label>

                    <label style="margin-left: 10px;">
                        From:
                        <input type="date" name="date_from" value="<?php echo esc_attr( isset( $_GET['date_from'] ) ? $_GET['date_from'] : '' ); ?>" />
                    </label>

                    <label style="margin-left: 10px;">
                        To:
                        <input type="date" name="date_to" value="<?php echo esc_attr( isset( $_GET['date_to'] ) ? $_GET['date_to'] : '' ); ?>" />
                    </label>

                    <?php submit_button( 'Filter', 'secondary', 'filter', false ); ?>
                </div>
            </form>

            <form method="post">
                <?php wp_nonce_field( 'wpojs_bulk_retry', '_wpojs_nonce' ); ?>
                <?php $table->display(); ?>
            </form>
        </div>
        <?php
    }

    private function render_stats_cards( $active, $synced, $fail_24h, $fail_7d, $rate_7d, $queue ) {
        $synced_color = ( $active > 0 && $synced >= $active ) ? '#46b450' : ( $synced > 0 ? '#ffb900' : '#dc3232' );
        $fail_24h_color = $fail_24h > 0 ? '#dc3232' : '#46b450';
        $fail_7d_color  = $fail_7d > 5 ? '#dc3232' : ( $fail_7d > 0 ? '#ffb900' : '#46b450' );

        if ( $rate_7d === null ) {
            $rate_display = '—';
            $rate_color   = '#999';
        } else {
            $rate_display = $rate_7d . '%';
            $rate_color   = $rate_7d >= 95 ? '#46b450' : ( $rate_7d >= 80 ? '#ffb900' : '#dc3232' );
        }

        $queue_pending = $queue['pending'];
        $queue_failed  = $queue['failed'];
        $queue_display = $queue_pending . ' pending, ' . $queue_failed . ' failed';
        $queue_color   = $queue_failed > 0 ? '#dc3232' : ( $queue_pending > 5 ? '#ffb900' : '#46b450' );

        ?>
        <div class="wpojs-stats-cards" style="display:flex;gap:12px;margin:16px 0;flex-wrap:wrap;">
            <?php
            $cards = array(
                array( 'label' => 'Members Synced', 'value' => $synced . ' of ' . $active, 'color' => $synced_color ),
                array( 'label' => 'Failures (24h)',  'value' => $fail_24h, 'color' => $fail_24h_color ),
                array( 'label' => 'Failures (7d)',   'value' => $fail_7d, 'color' => $fail_7d_color ),
                array( 'label' => 'Success Rate (7d)', 'value' => $rate_display, 'color' => $rate_color ),
                array( 'label' => 'Queue', 'value' => $queue_display, 'color' => $queue_color ),
            );
            foreach ( $cards as $card ) :
            ?>
                <div style="background:#fff;border:1px solid #ccd0d4;border-left:4px solid <?php echo esc_attr( $card['color'] ); ?>;padding:12px 16px;min-width:140px;flex:1;">
                    <div style="font-size:24px;font-weight:600;color:#23282d;"><?php echo esc_html( $card['value'] ); ?></div>
                    <div style="font-size:13px;color:#666;margin-top:4px;"><?php echo esc_html( $card['label'] ); ?></div>
                </div>
            <?php endforeach; ?>
        </div>
        <?php
    }
}

class WPOJS_Log_List_Table extends WP_List_Table {

    /** @var WPOJS_Logger */
    private $logger;

    public function __construct( WPOJS_Logger $logger ) {
        parent::__construct( array(
            'singular' => 'log_entry',
            'plural'   => 'log_entries',
            'ajax'     => false,
        ) );
        $this->logger = $logger;
    }

    public function get_columns() {
        return array(
            'cb'                => '<input type="checkbox" />',
            'created_at'        => 'Date',
            'email'             => 'Email',
            'action'            => 'Action',
            'status'            => 'Status',
            'ojs_response_code' => 'HTTP Code',
            'ojs_response_body' => 'Response',
            'attempt_count'     => 'Attempts',
        );
    }

    public function column_cb( $item ) {
        if ( $item->status === 'fail' ) {
            return sprintf( '<input type="checkbox" name="log_ids[]" value="%d" />', $item->id );
        }
        return '';
    }

    public function get_bulk_actions() {
        return array(
            'retry_selected' => 'Retry Selected',
        );
    }

    public function get_sortable_columns() {
        return array(
            'created_at' => array( 'created_at', true ),
            'email'      => array( 'email', false ),
            'status'     => array( 'status', false ),
            'action'     => array( 'action', false ),
        );
    }

    public function prepare_items() {
        $per_page = 20;
        $current_page = $this->get_pagenum();

        $args = array(
            'status'    => isset( $_GET['status'] ) ? sanitize_text_field( $_GET['status'] ) : '',
            'email'     => isset( $_GET['email'] ) ? sanitize_text_field( $_GET['email'] ) : '',
            'date_from' => isset( $_GET['date_from'] ) ? sanitize_text_field( $_GET['date_from'] ) : '',
            'date_to'   => isset( $_GET['date_to'] ) ? sanitize_text_field( $_GET['date_to'] ) : '',
            'per_page'  => $per_page,
            'offset'    => ( $current_page - 1 ) * $per_page,
            'orderby'   => isset( $_GET['orderby'] ) ? sanitize_text_field( $_GET['orderby'] ) : 'created_at',
            'order'     => isset( $_GET['order'] ) ? sanitize_text_field( $_GET['order'] ) : 'DESC',
        );

        $result = $this->logger->get_entries( $args );

        $this->items = $result['items'];
        $this->set_pagination_args( array(
            'total_items' => $result['total'],
            'per_page'    => $per_page,
            'total_pages' => ceil( $result['total'] / $per_page ),
        ) );

        $this->_column_headers = array(
            $this->get_columns(),
            array(),
            $this->get_sortable_columns(),
        );
    }

    public function column_default( $item, $column_name ) {
        switch ( $column_name ) {
            case 'created_at':
                return esc_html( $item->created_at );
            case 'email':
                $output = esc_html( $item->email );
                if ( $item->status === 'fail' && $item->action !== 'delete_user' ) {
                    $output .= '<div class="row-actions"><span class="retry"><a href="#" class="wpojs-retry-link" data-log-id="' . esc_attr( $item->id ) . '" data-nonce="' . esc_attr( wp_create_nonce( 'wpojs_retry_' . $item->id ) ) . '">Retry</a></span></div>';
                } elseif ( $item->status === 'fail' && $item->action === 'delete_user' ) {
                    $output .= '<div class="row-actions"><span style="color:#999;">Resolve manually in OJS</span></div>';
                }
                return $output;
            case 'action':
                return esc_html( $item->action );
            case 'status':
                $color = $item->status === 'success' ? 'green' : 'red';
                return sprintf( '<span style="color:%s;">%s</span>', $color, esc_html( $item->status ) );
            case 'ojs_response_code':
                return $item->ojs_response_code ? esc_html( $item->ojs_response_code ) : '—';
            case 'ojs_response_body':
                $raw = $item->ojs_response_body;
                if ( strlen( $raw ) > 100 ) {
                    return '<span title="' . esc_attr( $raw ) . '">' . esc_html( substr( $raw, 0, 100 ) ) . '...</span>';
                }
                return esc_html( $raw );
            case 'attempt_count':
                return esc_html( $item->attempt_count );
            default:
                return '';
        }
    }

    public function no_items() {
        echo 'No sync log entries found.';
    }
}
