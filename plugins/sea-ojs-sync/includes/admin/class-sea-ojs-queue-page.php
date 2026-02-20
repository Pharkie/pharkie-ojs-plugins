<?php

if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

if ( ! class_exists( 'WP_List_Table' ) ) {
    require_once ABSPATH . 'wp-admin/includes/class-wp-list-table.php';
}

class SEA_OJS_Queue_Page {

    /** @var SEA_OJS_Queue */
    private $queue;

    public function __construct( SEA_OJS_Queue $queue ) {
        $this->queue = $queue;
    }

    public function register() {
        add_action( 'admin_menu', array( $this, 'add_submenu' ) );
        add_action( 'admin_init', array( $this, 'handle_actions' ) );
    }

    public function add_submenu() {
        add_submenu_page(
            'sea-ojs-sync',
            'Sync Queue',
            'Sync Queue',
            'manage_options',
            'sea-ojs-sync-queue',
            array( $this, 'render_page' )
        );
    }

    /**
     * Handle retry action.
     */
    public function handle_actions() {
        if ( ! isset( $_GET['page'] ) || $_GET['page'] !== 'sea-ojs-sync-queue' ) {
            return;
        }

        if ( isset( $_GET['sea_ojs_action'] ) && $_GET['sea_ojs_action'] === 'retry' && isset( $_GET['item_id'] ) ) {
            check_admin_referer( 'sea_ojs_retry_' . $_GET['item_id'] );

            if ( ! current_user_can( 'manage_options' ) ) {
                return;
            }

            $this->queue->retry_item( absint( $_GET['item_id'] ) );

            wp_redirect( remove_query_arg( array( 'sea_ojs_action', 'item_id', '_wpnonce' ) ) );
            exit;
        }
    }

    public function render_page() {
        if ( ! current_user_can( 'manage_options' ) ) {
            return;
        }

        $table = new SEA_OJS_Queue_List_Table( $this->queue );
        $table->prepare_items();

        // Stats summary.
        $stats = $this->queue->get_stats();

        ?>
        <div class="wrap">
            <h1>OJS Sync Queue</h1>

            <div class="notice notice-info inline" style="margin-bottom: 15px; padding: 10px;">
                <strong>Queue Summary:</strong>
                Pending: <?php echo esc_html( $stats['pending'] ); ?> |
                Processing: <?php echo esc_html( $stats['processing'] ); ?> |
                Failed (retrying): <?php echo esc_html( $stats['failed'] ); ?> |
                <span style="color: <?php echo $stats['permanent_fail'] > 0 ? 'red' : 'inherit'; ?>;">
                    Permanent failures: <?php echo esc_html( $stats['permanent_fail'] ); ?>
                </span> |
                Completed: <?php echo esc_html( $stats['completed'] ); ?>
            </div>

            <form method="get">
                <input type="hidden" name="page" value="sea-ojs-sync-queue" />

                <div class="tablenav top" style="margin-bottom: 10px;">
                    <label>
                        Status:
                        <select name="status">
                            <option value="">All</option>
                            <option value="pending" <?php selected( isset( $_GET['status'] ) ? $_GET['status'] : '', 'pending' ); ?>>Pending</option>
                            <option value="processing" <?php selected( isset( $_GET['status'] ) ? $_GET['status'] : '', 'processing' ); ?>>Processing</option>
                            <option value="failed" <?php selected( isset( $_GET['status'] ) ? $_GET['status'] : '', 'failed' ); ?>>Failed</option>
                            <option value="permanent_fail" <?php selected( isset( $_GET['status'] ) ? $_GET['status'] : '', 'permanent_fail' ); ?>>Permanent Fail</option>
                            <option value="completed" <?php selected( isset( $_GET['status'] ) ? $_GET['status'] : '', 'completed' ); ?>>Completed</option>
                        </select>
                    </label>

                    <label style="margin-left: 10px;">
                        Email:
                        <input type="text" name="email" value="<?php echo esc_attr( isset( $_GET['email'] ) ? $_GET['email'] : '' ); ?>" placeholder="Search email..." />
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

class SEA_OJS_Queue_List_Table extends WP_List_Table {

    /** @var SEA_OJS_Queue */
    private $queue;

    public function __construct( SEA_OJS_Queue $queue ) {
        parent::__construct( array(
            'singular' => 'queue_item',
            'plural'   => 'queue_items',
            'ajax'     => false,
        ) );
        $this->queue = $queue;
    }

    public function get_columns() {
        return array(
            'created_at'   => 'Queued',
            'email'        => 'Email',
            'action'       => 'Action',
            'status'       => 'Status',
            'attempts'     => 'Attempts',
            'next_retry_at' => 'Next Retry',
            'actions'      => '',
        );
    }

    public function get_sortable_columns() {
        return array(
            'created_at' => array( 'created_at', true ),
            'email'      => array( 'email', false ),
            'status'     => array( 'status', false ),
            'action'     => array( 'action', false ),
            'attempts'   => array( 'attempts', false ),
        );
    }

    public function prepare_items() {
        $per_page = 20;
        $current_page = $this->get_pagenum();

        $args = array(
            'status'   => isset( $_GET['status'] ) ? sanitize_text_field( $_GET['status'] ) : '',
            'email'    => isset( $_GET['email'] ) ? sanitize_text_field( $_GET['email'] ) : '',
            'per_page' => $per_page,
            'offset'   => ( $current_page - 1 ) * $per_page,
            'orderby'  => isset( $_GET['orderby'] ) ? sanitize_text_field( $_GET['orderby'] ) : 'created_at',
            'order'    => isset( $_GET['order'] ) ? sanitize_text_field( $_GET['order'] ) : 'DESC',
        );

        $result = $this->queue->get_items( $args );

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
                $colors = array(
                    'pending'        => '#666',
                    'processing'     => 'blue',
                    'failed'         => 'orange',
                    'permanent_fail' => 'red',
                    'completed'      => 'green',
                );
                $color = isset( $colors[ $item->status ] ) ? $colors[ $item->status ] : '#333';
                $label = str_replace( '_', ' ', $item->status );
                return sprintf( '<strong style="color:%s;">%s</strong>', $color, esc_html( ucfirst( $label ) ) );
            case 'attempts':
                return esc_html( $item->attempts );
            case 'next_retry_at':
                return $item->next_retry_at ? esc_html( $item->next_retry_at ) : '—';
            case 'actions':
                if ( in_array( $item->status, array( 'failed', 'permanent_fail' ), true ) ) {
                    $url = wp_nonce_url(
                        add_query_arg( array(
                            'sea_ojs_action' => 'retry',
                            'item_id'        => $item->id,
                        ) ),
                        'sea_ojs_retry_' . $item->id
                    );
                    return sprintf( '<a href="%s" class="button button-small">Retry</a>', esc_url( $url ) );
                }
                return '';
            default:
                return '';
        }
    }

    public function no_items() {
        echo 'No items in the sync queue.';
    }
}
