<?php

if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

class WPOJS_Dashboard {

    /** @var WPOJS_Resolver */
    private $resolver;

    /** @var WPOJS_Logger|null */
    private $logger;

    /** @var string */
    private $ojs_url;

    public function __construct( WPOJS_Resolver $resolver, ?WPOJS_Logger $logger = null ) {
        $this->resolver = $resolver;
        $this->logger   = $logger;
        $this->ojs_url  = untrailingslashit( get_option( 'wpojs_url', '' ) );
    }

    /**
     * Register frontend hooks.
     */
    public function register() {
        // WooCommerce My Account dashboard (main content area).
        add_action( 'woocommerce_account_dashboard', array( $this, 'render_journal_access_card' ) );
    }

    /**
     * Render the journal access card on My Account dashboard.
     */
    public function render_journal_access_card() {
        $user_id = get_current_user_id();
        if ( ! $user_id ) {
            return;
        }

        $is_member = $this->resolver->is_active_member( $user_id );
        $sub_data  = $is_member ? $this->resolver->resolve_subscription_data( $user_id ) : null;

        // Build status text.
        if ( $is_member && $sub_data ) {
            if ( $sub_data['date_end'] === null ) {
                $status_text = __( 'Active', 'wpojs-sync' );
                $status_class = 'wpojs-status--active';
            } else {
                $expires = date_i18n( get_option( 'date_format' ), strtotime( $sub_data['date_end'] ) );
                $status_text = sprintf( __( 'Active until %s', 'wpojs-sync' ), $expires );
                $status_class = 'wpojs-status--active';
            }
        } else {
            $status_text = __( 'No active access', 'wpojs-sync' );
            $status_class = 'wpojs-status--inactive';
        }

        // Query last successful sync timestamp for this user.
        $last_synced = null;
        if ( $this->logger ) {
            global $wpdb;
            $table = $wpdb->prefix . 'wpojs_sync_log';
            $last_synced = $wpdb->get_var( $wpdb->prepare(
                "SELECT created_at FROM {$table} WHERE wp_user_id = %d AND status = 'success' ORDER BY created_at DESC LIMIT 1",
                $user_id
            ) );
        }

        $journal_url  = $this->ojs_url ?: '#';
        $journal_name = get_option( 'wpojs_journal_name', 'Journal' );
        ?>
        <div class="wpojs-journal-access" style="background:#f8f9fa;border:1px solid #dee2e6;border-radius:6px;padding:20px;margin-bottom:24px;">
            <h3 style="margin:0 0 8px;font-size:16px;"><?php esc_html_e( 'Journal Access', 'wpojs-sync' ); ?></h3>
            <p style="margin:0 0 12px;font-size:14px;color:#555;">
                <span class="<?php echo esc_attr( $status_class ); ?>" style="display:inline-block;padding:2px 8px;border-radius:3px;font-size:12px;font-weight:600;<?php
                    echo $is_member
                        ? 'background:#d4edda;color:#155724;'
                        : 'background:#f8d7da;color:#721c24;';
                ?>">
                    <?php echo esc_html( $status_text ); ?>
                </span>
            </p>
            <?php if ( $is_member && $last_synced ) : ?>
                <p style="margin:0 0 12px;font-size:12px;color:#888;">
                    <?php echo esc_html( sprintf( __( 'Last synced: %s', 'wpojs-sync' ), date_i18n( get_option( 'date_format' ) . ' ' . get_option( 'time_format' ), strtotime( $last_synced ) ) ) ); ?>
                </p>
            <?php endif; ?>
            <?php if ( $is_member && $this->ojs_url ) : ?>
                <p style="margin:0 0 12px;font-size:14px;color:#555;">
                    <?php echo esc_html( sprintf( __( 'Your membership includes access to %s.', 'wpojs-sync' ), $journal_name ) ); ?>
                </p>
                <a href="<?php echo esc_url( $journal_url ); ?>" target="_blank" rel="noopener"
                   style="display:inline-block;background:#0073aa;color:#fff;padding:8px 20px;border-radius:4px;text-decoration:none;font-size:14px;font-weight:500;">
                    <?php echo esc_html( sprintf( __( 'Read %s', 'wpojs-sync' ), $journal_name ) ); ?> &rarr;
                </a>
            <?php elseif ( ! $is_member ) : ?>
                <p style="margin:0;font-size:13px;color:#666;">
                    <?php esc_html_e( 'If you have a membership and can\'t access journal content, please contact support.', 'wpojs-sync' ); ?>
                </p>
            <?php endif; ?>
        </div>
        <?php
    }
}
