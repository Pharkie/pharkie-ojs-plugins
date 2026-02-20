<?php

if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

if ( ! class_exists( 'WP_List_Table' ) ) {
    require_once ABSPATH . 'wp-admin/includes/class-wp-list-table.php';
}

class SEA_OJS_Log_Page {

    /** @var SEA_OJS_Logger */
    private $logger;

    public function __construct( SEA_OJS_Logger $logger ) {
        $this->logger = $logger;
    }

    public function register() {
        add_action( 'admin_menu', array( $this, 'add_submenu' ) );
    }

    public function add_submenu() {
        add_submenu_page(
            'sea-ojs-sync',
            'Sync Log',
            'Sync Log',
            'manage_options',
            'sea-ojs-sync-log',
            array( $this, 'render_page' )
        );
    }

    public function render_page() {
        if ( ! current_user_can( 'manage_options' ) ) {
            return;
        }

        $table = new SEA_OJS_Log_List_Table( $this->logger );
        $table->prepare_items();

        ?>
        <div class="wrap">
            <h1>OJS Sync Log</h1>

            <form method="get">
                <input type="hidden" name="page" value="sea-ojs-sync-log" />

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
                <?php $table->display(); ?>
            </form>
        </div>
        <?php
    }
}

class SEA_OJS_Log_List_Table extends WP_List_Table {

    /** @var SEA_OJS_Logger */
    private $logger;

    public function __construct( SEA_OJS_Logger $logger ) {
        parent::__construct( array(
            'singular' => 'log_entry',
            'plural'   => 'log_entries',
            'ajax'     => false,
        ) );
        $this->logger = $logger;
    }

    public function get_columns() {
        return array(
            'created_at'        => 'Date',
            'email'             => 'Email',
            'action'            => 'Action',
            'status'            => 'Status',
            'ojs_response_code' => 'HTTP Code',
            'ojs_response_body' => 'Response',
            'attempt_count'     => 'Attempts',
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
                return esc_html( $item->email );
            case 'action':
                return esc_html( $item->action );
            case 'status':
                $color = $item->status === 'success' ? 'green' : 'red';
                return sprintf( '<span style="color:%s;">%s</span>', $color, esc_html( $item->status ) );
            case 'ojs_response_code':
                return $item->ojs_response_code ? esc_html( $item->ojs_response_code ) : '—';
            case 'ojs_response_body':
                $body = esc_html( $item->ojs_response_body );
                if ( strlen( $body ) > 100 ) {
                    return '<span title="' . esc_attr( $item->ojs_response_body ) . '">' . substr( $body, 0, 100 ) . '...</span>';
                }
                return $body;
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
