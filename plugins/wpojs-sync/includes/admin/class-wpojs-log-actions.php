<?php

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

class WPOJS_Log_Actions {

	/** @var WPOJS_Logger */
	private $logger;

	public function __construct( WPOJS_Logger $logger ) {
		$this->logger = $logger;
	}

	public function register() {
		add_action( 'wp_ajax_wpojs_retry_sync', array( $this, 'handle_retry' ) );
		add_action( 'wp_ajax_wpojs_bulk_retry', array( $this, 'handle_bulk_retry' ) );
		add_action( 'admin_enqueue_scripts', array( $this, 'enqueue_scripts' ) );
	}

	public function enqueue_scripts( $hook ) {
		if ( $hook !== 'ojs-sync_page_wpojs-sync-log' ) {
			return;
		}

		wp_add_inline_script( 'jquery-core', $this->get_inline_js() );
	}

	/**
	 * AJAX handler: retry a single failed log entry.
	 */
	public function handle_retry() {
		if ( ! current_user_can( 'manage_options' ) ) {
			wp_send_json_error( 'Insufficient permissions.' );
		}

		$log_id = isset( $_GET['log_id'] ) ? absint( $_GET['log_id'] ) : 0;
		if ( ! $log_id ) {
			wp_send_json_error( 'Invalid log ID.' );
		}

		check_ajax_referer( 'wpojs_retry_' . $log_id );

		$result = $this->schedule_retry( $log_id );
		if ( $result['success'] ) {
			wp_send_json_success( $result['message'] );
		} else {
			wp_send_json_error( $result['message'] );
		}
	}

	/**
	 * AJAX handler: retry multiple failed log entries (bulk action).
	 */
	public function handle_bulk_retry() {
		if ( ! current_user_can( 'manage_options' ) ) {
			wp_send_json_error( 'Insufficient permissions.' );
		}

		check_ajax_referer( 'wpojs_bulk_retry', '_wpojs_nonce' );

		$log_ids = isset( $_POST['log_ids'] ) ? array_map( 'absint', (array) $_POST['log_ids'] ) : array();
		if ( empty( $log_ids ) ) {
			wp_send_json_error( 'No entries selected.' );
		}

		$queued = 0;
		$errors = array();

		foreach ( $log_ids as $log_id ) {
			$result = $this->schedule_retry( $log_id );
			if ( $result['success'] ) {
				$queued++;
			} else {
				$errors[] = sprintf( '#%d: %s', $log_id, $result['message'] );
			}
		}

		if ( $queued > 0 ) {
			wp_send_json_success( sprintf( '%d entries queued for retry.', $queued ) );
		} else {
			wp_send_json_error( 'No entries queued. ' . implode( '; ', $errors ) );
		}
	}

	/**
	 * Read a failed log entry and schedule an Action Scheduler retry.
	 *
	 * @param int $log_id
	 * @return array { success: bool, message: string }
	 */
	private function schedule_retry( $log_id ) {
		global $wpdb;
		$table = $wpdb->prefix . 'wpojs_sync_log';

		$entry = $wpdb->get_row( $wpdb->prepare(
			"SELECT * FROM {$table} WHERE id = %d",
			$log_id
		) );

		if ( ! $entry ) {
			return array( 'success' => false, 'message' => 'Log entry not found.' );
		}

		if ( $entry->status !== 'fail' ) {
			return array( 'success' => false, 'message' => 'Entry is not a failure.' );
		}

		$wp_user_id = (int) $entry->wp_user_id;
		$action     = $entry->action;

		// Map log action to Action Scheduler hook.
		$action_map = array(
			'activate'           => 'wpojs_sync_activate',
			'expire'             => 'wpojs_sync_expire',
			'email_change'       => 'wpojs_sync_email_change',
			'delete_user'        => 'wpojs_sync_delete_user',
			'create_user'        => 'wpojs_sync_activate',
			'reconcile_activate' => 'wpojs_sync_activate',
			'reconcile_expire'   => 'wpojs_sync_expire',
		);

		$as_hook = isset( $action_map[ $action ] ) ? $action_map[ $action ] : null;
		if ( ! $as_hook ) {
			return array( 'success' => false, 'message' => 'Unknown action type: ' . $action );
		}

		// Build args. Email change and delete need extra data we don't have in the log
		// for full fidelity, but we can retry with what we have.
		$as_args = array( array( 'wp_user_id' => $wp_user_id ) );

		// For email_change, we can't reconstruct old/new email from the log entry alone.
		// Schedule an activate instead (it will ensure the user+subscription are correct).
		if ( $action === 'email_change' ) {
			$as_hook = 'wpojs_sync_activate';
		}

		// Don't double-queue.
		if ( as_has_scheduled_action( $as_hook, $as_args, 'wpojs-sync' ) ) {
			return array( 'success' => true, 'message' => 'Already queued.' );
		}

		as_schedule_single_action( time(), $as_hook, $as_args, 'wpojs-sync' );

		return array( 'success' => true, 'message' => 'Queued for retry.' );
	}

	private function get_inline_js() {
		return <<<'JS'
jQuery(function($) {
    // Single retry via row action.
    $(document).on('click', '.wpojs-retry-link', function(e) {
        e.preventDefault();
        var $link = $(this);
        var logId = $link.data('log-id');
        var nonce = $link.data('nonce');

        $link.text('Queuing...');

        $.get(ajaxurl, {
            action: 'wpojs_retry_sync',
            log_id: logId,
            _ajax_nonce: nonce
        }, function(response) {
            if (response.success) {
                $link.closest('.row-actions').html('<span style="color:#46b450;">Queued</span>');
            } else {
                $link.text('Retry');
                alert('Retry failed: ' + response.data);
            }
        }).fail(function() {
            $link.text('Retry');
            alert('Retry request failed.');
        });
    });

    // Bulk retry via form submit.
    $('form:has([name="log_ids[]"])').on('submit', function(e) {
        var action = $(this).find('select[name="action"]').val() || $(this).find('select[name="action2"]').val();
        if (action !== 'retry_selected') {
            return;
        }
        e.preventDefault();

        var $form = $(this);
        var logIds = [];
        $form.find('input[name="log_ids[]"]:checked').each(function() {
            logIds.push($(this).val());
        });

        if (logIds.length === 0) {
            alert('No failed entries selected.');
            return;
        }

        var $btn = $form.find('#doaction, #doaction2');
        $btn.prop('disabled', true).val('Queuing...');

        $.post(ajaxurl, {
            action: 'wpojs_bulk_retry',
            log_ids: logIds,
            _wpojs_nonce: $form.find('[name="_wpojs_nonce"]').val()
        }, function(response) {
            if (response.success) {
                alert(response.data);
                location.reload();
            } else {
                alert('Bulk retry failed: ' + response.data);
                $btn.prop('disabled', false).val('Apply');
            }
        }).fail(function() {
            alert('Bulk retry request failed.');
            $btn.prop('disabled', false).val('Apply');
        });
    });
});
JS;
	}
}
