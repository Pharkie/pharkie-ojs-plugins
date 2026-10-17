<?php
/**
 * Tests for the sync fence (WPOJS_FENCE_EMAIL_PATTERN).
 *
 *   php plugins/wpojs-sync/tests/fence-test.php
 *
 * No WordPress and no PHPUnit: the WP functions the plugin calls are stubbed
 * below, and OJS is a fake that records every request it receives. A constant
 * can't be undefined, so each mode (fenced, unfenced, empty pattern, invalid
 * pattern) runs in its own process; with no argument this file runs them all.
 *
 * The fence exists because the WordPress test rig's sync writes to the LIVE
 * journal for a copy of real members. What these tests pin down: on a fenced
 * install no write for a real member's account is ever sent, however it is
 * reached; test accounts still sync; reads are unaffected; and an unfenced
 * install behaves exactly as before.
 */

const FENCE_PATTERN = '/^[^@\s]+@test\.invalid\z/i';
const MODES         = array( 'fenced', 'unfenced', 'empty', 'invalid' );

if ( ! isset( $argv[1] ) ) {
	$failed = 0;
	foreach ( MODES as $mode ) {
		passthru( escapeshellarg( PHP_BINARY ) . ' ' . escapeshellarg( __FILE__ ) . ' ' . $mode, $code );
		$failed += $code ? 1 : 0;
	}
	echo $failed ? "\nFAILED: $failed mode(s)\n" : "\nAll modes passed.\n";
	exit( $failed ? 1 : 0 );
}

$mode = $argv[1];
if ( ! in_array( $mode, MODES, true ) ) {
	fwrite( STDERR, "Unknown mode: $mode\n" );
	exit( 2 );
}
if ( $mode === 'fenced' ) {
	define( 'WPOJS_FENCE_EMAIL_PATTERN', FENCE_PATTERN );
} elseif ( $mode === 'empty' ) {
	define( 'WPOJS_FENCE_EMAIL_PATTERN', '' );
} elseif ( $mode === 'invalid' ) {
	define( 'WPOJS_FENCE_EMAIL_PATTERN', '/[' );
}

// =============================================================================
// WordPress stubs
// =============================================================================

define( 'ABSPATH', __DIR__ );
define( 'DAY_IN_SECONDS', 86400 );

class WP_Error {
	private $message;
	public function __construct( $code = '', $message = '' ) { $this->message = $message; }
	public function get_error_message() { return $this->message; }
}
function is_wp_error( $thing ) { return $thing instanceof WP_Error; }

$GLOBALS['filters'] = array();
function add_filter( $hook, $cb, $priority = 10, $accepted_args = 1 ) { $GLOBALS['filters'][ $hook ][] = $cb; }
function remove_filter( $hook, $cb, $priority = 10 ) {
	foreach ( $GLOBALS['filters'][ $hook ] ?? array() as $i => $f ) {
		if ( $f === $cb ) {
			unset( $GLOBALS['filters'][ $hook ][ $i ] );
		}
	}
}

function get_option( $name, $default = false ) {
	$options = array( 'wpojs_url' => 'http://ojs/index.php/ea', 'admin_email' => 'admin@example.org' );
	return $options[ $name ] ?? $default;
}
function untrailingslashit( $s ) { return rtrim( $s, '/\\' ); }
function absint( $n ) { return abs( (int) $n ); }
function wp_json_encode( $data ) { return json_encode( $data ); }
function wp_generate_uuid4() { return 'req-' . mt_rand(); }
function add_query_arg( $args, $url ) { return $url . '?' . http_build_query( $args ); }
function wp_remote_get( $url, $args = array() ) { return wp_http( 'GET', $url, $args ); }
function wp_remote_post( $url, $args = array() ) { return wp_http( 'POST', $url, $args ); }
function wp_remote_request( $url, $args = array() ) { return wp_http( $args['method'] ?? 'GET', $url, $args ); }
function wp_remote_retrieve_response_code( $r ) { return $r['response']['code']; }
function wp_remote_retrieve_body( $r ) { return $r['body']; }
function wp_remote_retrieve_header( $r, $h ) { return ''; }

/** WP_Http::request: pre_http_request filters first, then the network (here, fake OJS). */
function wp_http( $method, $url, $args ) {
	$args['method'] = $method;
	foreach ( $GLOBALS['filters']['pre_http_request'] ?? array() as $cb ) {
		$pre = $cb( false, $args, $url );
		if ( $pre !== false ) {
			return $pre;
		}
	}
	return fake_ojs( $method, $url, isset( $args['body'] ) ? json_decode( $args['body'], true ) : array() );
}

$GLOBALS['wp_users'] = array();
$GLOBALS['wp_meta']  = array();
$GLOBALS['mail']     = array();
$GLOBALS['as']       = array();
function wp_user( $id, $email ) {
	$GLOBALS['wp_users'][ $id ] = (object) array(
		'ID' => $id, 'user_email' => $email, 'user_login' => 'u' . $id, 'user_pass' => '$P$hash' . $id,
		'first_name' => 'First', 'last_name' => 'Last', 'display_name' => 'First Last',
	);
}
function get_userdata( $id ) { return isset( $GLOBALS['wp_users'][ $id ] ) ? clone $GLOBALS['wp_users'][ $id ] : false; }
function get_user_meta( $id, $key, $single = false ) { return $GLOBALS['wp_meta'][ $id ][ $key ] ?? ''; }
function update_user_meta( $id, $key, $value ) { $GLOBALS['wp_meta'][ $id ][ $key ] = $value; }
function get_users( $args ) {
	$out = array();
	foreach ( $GLOBALS['wp_users'] as $id => $user ) {
		if ( isset( $GLOBALS['wp_meta'][ $id ][ $args['meta_key'] ] ) ) {
			$out[] = (object) array( 'ID' => $id, 'user_email' => $user->user_email );
		}
	}
	return $out;
}
function wp_mail( $to, $subject, $message ) { $GLOBALS['mail'][] = $subject; return true; }
function admin_url( $path = '' ) { return 'http://wp/wp-admin/' . $path; }
function as_has_scheduled_action( $hook, $args, $group ) { return in_array( array( $hook, $args ), $GLOBALS['as'], true ); }
function as_schedule_single_action( $time, $hook, $args, $group ) { $GLOBALS['as'][] = array( $hook, $args ); }

class Fake_WPDB {
	public $usermeta = 'wp_usermeta';
	public function prepare( $sql, ...$args ) { return vsprintf( str_replace( '%d', '%d', $sql ), $args ); }
	public function get_col( $sql ) {
		if ( strpos( $sql, 'OFFSET 0' ) === false && strpos( $sql, 'OFFSET' ) !== false ) {
			return array();
		}
		$ids = array();
		foreach ( $GLOBALS['wp_meta'] as $id => $meta ) {
			if ( isset( $meta['_wpojs_user_id'] ) ) {
				$ids[] = (string) $id;
			}
		}
		return $ids;
	}
}
$GLOBALS['wpdb'] = new Fake_WPDB();

class WP_CLI_Error extends Exception {}
class WP_CLI {
	public static $out = array();
	public static function log( $m ) { self::$out[] = $m; }
	public static function success( $m ) { self::$out[] = 'Success: ' . $m; }
	public static function warning( $m ) { self::$out[] = 'Warning: ' . $m; }
	public static function error( $m ) { self::$out[] = 'Error: ' . $m; throw new WP_CLI_Error( $m ); }
	public static function add_command( $name, $cb ) {}
}

// =============================================================================
// Fake OJS: the wpojs API, recording every request.
// =============================================================================

function reset_ojs() {
	$GLOBALS['ojs'] = array(
		// 42 and 43 are real members; 77 and 78 are monitor test accounts.
		'users'    => array( 42 => 'member@example.org', 43 => 'lapsed@example.org', 77 => 'monitor-9@test.invalid', 78 => 'monitor-16@test.invalid' ),
		'active'   => array( 'lapsed@example.org', 'monitor-16@test.invalid' ),
		'next_id'  => 100,
		'requests' => array(),
	);
}
function ojs_id_for( $email ) {
	$id = array_search( strtolower( $email ), array_map( 'strtolower', $GLOBALS['ojs']['users'] ), true );
	return $id === false ? null : $id;
}
function fake_ojs( $method, $url, $body ) {
	$path  = parse_url( $url, PHP_URL_PATH );
	$path  = substr( $path, strpos( $path, '/api/v1' ) + 7 );
	parse_str( (string) parse_url( $url, PHP_URL_QUERY ), $query );
	$GLOBALS['ojs']['requests'][] = "$method $path";
	$ojs = &$GLOBALS['ojs'];

	$out = array();
	if ( $method === 'GET' && $path === '/wpojs/users' ) {
		$id  = ojs_id_for( $query['email'] );
		$out = $id ? array( 'found' => true, 'userId' => $id, 'email' => $ojs['users'][ $id ] ) : array( 'found' => false );
	} elseif ( $method === 'POST' && $path === '/wpojs/users/find-or-create' ) {
		$id      = ojs_id_for( $body['email'] );
		$created = ! $id;
		if ( $created ) {
			$id                  = $ojs['next_id']++;
			$ojs['users'][ $id ] = $body['email'];
		}
		$out = array( 'userId' => $id, 'created' => $created );
	} elseif ( $method === 'POST' && $path === '/wpojs/subscriptions/status-batch' ) {
		foreach ( $body['emails'] as $email ) {
			$out['results'][ $email ] = array( 'active' => in_array( strtolower( $email ), $ojs['active'], true ) );
		}
	} elseif ( $method === 'PUT' && preg_match( '#^/wpojs/users/(\d+)/email$#', $path, $m ) ) {
		$ojs['users'][ (int) $m[1] ] = $body['newEmail'];
		$out = array( 'userId' => (int) $m[1] );
	} elseif ( $method === 'DELETE' && preg_match( '#^/wpojs/users/(\d+)$#', $path, $m ) ) {
		$ojs['users'][ (int) $m[1] ] = 'deleted_' . $m[1] . '@anonymised.invalid';
		$out = array( 'userId' => (int) $m[1] );
	}
	return array( 'response' => array( 'code' => 200 ), 'body' => json_encode( $out ), 'headers' => array() );
}
/** Requests that would change something on OJS (status-batch is a POST but only reads). */
function writes() {
	return array_values( array_filter( $GLOBALS['ojs']['requests'], function ( $r ) {
		return strpos( $r, 'GET ' ) !== 0 && $r !== 'POST /wpojs/subscriptions/status-batch';
	} ) );
}
function requests() { return $GLOBALS['ojs']['requests']; }
function clear_requests() { $GLOBALS['ojs']['requests'] = array(); }

// =============================================================================
// The plugin
// =============================================================================

$inc = dirname( __DIR__ ) . '/includes/';
require_once $inc . 'class-wpojs-api-client.php';
require_once $inc . 'class-wpojs-logger.php';
require_once $inc . 'class-wpojs-resolver.php';
require_once $inc . 'class-wpojs-sync.php';
require_once $inc . 'class-wpojs-cron.php';
require_once $inc . 'class-wpojs-stats.php';
require_once $inc . 'cli/class-wpojs-cli.php';

class Fake_Logger extends WPOJS_Logger {
	public $rows = array();
	public function __construct() {}
	public function log( $wp_user_id, $email, $action, $status, $response_code = null, $response_body = '', $attempt_count = 1, $request_id = '' ) {
		$this->rows[] = compact( 'wp_user_id', 'email', 'action', 'status', 'response_body' );
	}
	public function anonymize_user_logs( $wp_user_id, $email ) {}
	public function statuses_for( $wp_user_id, $action ) {
		$out = array();
		foreach ( $this->rows as $row ) {
			if ( $row['wp_user_id'] === $wp_user_id && $row['action'] === $action ) {
				$out[] = $row['status'];
			}
		}
		return $out;
	}
}

class Fake_Resolver extends WPOJS_Resolver {
	public $active = array();
	public function resolve_subscription_data( $wp_user_id ) { return array( 'type_id' => 1, 'date_start' => '2026-01-01', 'date_end' => null ); }
	public function get_all_active_members() { return $this->active; }
}

/** A regression: an id-keyed write that forgot the fence and goes straight out. */
class Leaky_Client extends WPOJS_API_Client {
	public function expire_subscription_by_user( $user_id ) {
		$r = wp_remote_request( 'http://ojs/index.php/ea/api/v1/wpojs/subscriptions/expire-by-user/' . absint( $user_id ), array( 'method' => 'PUT' ) );
		return is_wp_error( $r ) ? array( 'success' => false, 'code' => 0, 'error' => $r->get_error_message() ) : array( 'success' => true, 'code' => 200 );
	}
}

function make( $api = null ) {
	$api      = $api ?: new WPOJS_API_Client();
	$logger   = new Fake_Logger();
	$resolver = new Fake_Resolver();
	return array( $api, new WPOJS_Sync( $api, $logger, $resolver ), $logger, $resolver );
}

/** The rig, in miniature. */
function reset_wp() {
	$GLOBALS['wp_users'] = $GLOBALS['wp_meta'] = $GLOBALS['mail'] = $GLOBALS['as'] = array();
	wp_user( 10, 'member@example.org' );      update_user_meta( 10, '_wpojs_user_id', 42 ); // a real member, linked
	wp_user( 11, 'monitor-9@test.invalid' );  update_user_meta( 11, '_wpojs_user_id', 77 ); // the monitor's user
	wp_user( 12, 'monitor-12@test.invalid' ); update_user_meta( 12, '_wpojs_user_id', 42 ); // edited to a test address, still linked to a real account
	wp_user( 13, 'monitor-13@test.invalid' );                                              // a new test user
	wp_user( 14, 'monitor-14@test.invalid' );                                              // active member, missing on OJS
	wp_user( 15, 'lapsed@example.org' );      update_user_meta( 15, '_wpojs_user_id', 43 ); // real, lapsed here, still active on OJS
	wp_user( 16, 'monitor-16@test.invalid' ); update_user_meta( 16, '_wpojs_user_id', 78 ); // test, lapsed here, still active on OJS
	reset_ojs();
}

// =============================================================================
// Assertions
// =============================================================================

$GLOBALS['passed'] = 0;
$GLOBALS['failed'] = array();
function check( $cond, $label ) {
	if ( $cond ) {
		$GLOBALS['passed']++;
	} else {
		$GLOBALS['failed'][] = $label;
	}
}
function same( $expected, $actual, $label ) {
	check( $expected === $actual, $label . ' -- expected ' . json_encode( $expected ) . ', got ' . json_encode( $actual ) );
}

// An invalid pattern warns on every check; that's intended, but not here.
set_error_handler( function ( $no, $str ) use ( $mode ) { return $mode === 'invalid' && strpos( $str, 'preg_match' ) !== false; }, E_WARNING );

// =============================================================================
// Tests
// =============================================================================

$real_writes = function ( WPOJS_API_Client $api ) {
	return array(
		'find-or-create' => $api->find_or_create_user( 'member@example.org', 'A', 'B' ),
		'subscription'   => $api->create_subscription( 42, 1, '2026-01-01' ),
		'expire'         => $api->expire_subscription_by_user( 42 ),
		'password'       => $api->update_user_password( 42, 'h' ),
		'email change'   => $api->update_user_email( 42, 'monitor-5@test.invalid' ),
		'delete'         => $api->delete_user( 42 ),
	);
};

if ( $mode === 'fenced' ) {
	// --- Which emails are inside -------------------------------------------
	$api = new WPOJS_API_Client();
	check( $api->is_fenced(), 'install reports fenced' );
	foreach ( array( 'monitor-1760700000@test.invalid', 'smoke-test-1@test.invalid', 'MONITOR-1@TEST.INVALID' ) as $e ) {
		check( $api->fence_admits_email( $e ), "admits $e" );
	}
	foreach ( array( 'member@example.org', 'x@test.invalid.example.org', "monitor-1@test.invalid\n", 'a@b@test.invalid', 'x@example.invalid', '' ) as $e ) {
		check( ! $api->fence_admits_email( $e ), 'refuses ' . json_encode( $e ) );
	}

	// --- Client: a real member's account can't be written, by any method ----
	reset_wp();
	$api = new WPOJS_API_Client();
	foreach ( $real_writes( $api ) as $what => $result ) {
		check( ! empty( $result['fenced'] ) && $result['code'] === 403, "client refuses $what for a real account" );
	}
	same( array(), requests(), 'no request at all for refused writes' );

	check( ! $api->vouch( 42, 'member@example.org' ), 'a real account cannot be vouched for' );
	same( array(), requests(), 'vouching a real email makes no request' );

	// The rig user whose email was edited to a test address but whose cached
	// id is still the real member's: OJS says that email is not account 42.
	check( ! $api->vouch( 42, 'monitor-9@test.invalid' ), 'a real id is not vouched for by a test email that is not its own' );
	same( array( 'GET /wpojs/users' ), requests(), '...and finding that out is a read' );
	check( ! empty( $api->expire_subscription_by_user( 42 )['fenced'] ), '...and the real account stays refused' );

	clear_requests();
	$api->find_user( 'member@example.org' );
	$api->get_subscription_status_batch( array( 'member@example.org' ) );
	$api->get_subscriptions( array( 'email' => 'member@example.org' ) );
	same( 3, count( requests() ), 'reads for real members are unaffected' );
	check( ! empty( $api->expire_subscription_by_user( 42 )['fenced'] ), 'reading a real account does not vouch for it' );

	// --- Client: test accounts sync --------------------------------------------
	reset_wp();
	$api    = new WPOJS_API_Client();
	$result = $api->find_or_create_user( 'monitor-1@test.invalid', 'Monitor', 'Test' );
	check( $result['success'], 'find-or-create for a test account is sent' );
	$id = $result['body']['userId'];
	check( $api->create_subscription( $id, 1, '2026-01-01' )['success'], 'its subscription is sent' );
	check( $api->expire_subscription_by_user( $id )['success'], 'its expiry is sent' );

	check( $api->vouch( 77, 'monitor-9@test.invalid' ), 'an existing test account is vouched for' );
	check( $api->update_user_password( 77, 'h' )['success'], '...its password change is sent' );
	check( ! empty( $api->update_user_email( 77, 'someone@example.org' )['fenced'] ), '...but not a change of its email to a real address' );
	check( $api->update_user_email( 77, 'monitor-10@test.invalid' )['success'], '...a change to another test address is sent' );
	check( $api->delete_user( 77 )['success'], '...its delete is sent' );
	check( ! empty( $api->expire_subscription_by_user( 77 )['fenced'] ), 'once deleted (anonymised) it is no longer vouched for' );

	$fresh = new WPOJS_API_Client();
	check( ! empty( $fresh->expire_subscription_by_user( $id )['fenced'] ), 'vouches do not outlive the client: a new one must ask OJS again' );

	// --- Sync handlers: real members ------------------------------------------
	reset_wp();
	list( $api, $sync, $logger ) = make();
	$sync->handle_activate( array( 'wp_user_id' => 10 ) );
	$sync->handle_expire( array( 'wp_user_id' => 10 ) );
	$sync->handle_password_change( array( 'wp_user_id' => 10 ) );
	$sync->handle_delete_user( array( 'wp_user_id' => 10, 'email' => 'member@example.org', 'ojs_user_id' => 42 ) );
	$GLOBALS['wp_users'][10]->user_email = 'monitor-50@test.invalid'; // email change, real -> test address
	$sync->handle_email_change( array( 'wp_user_id' => 10, 'old_email' => 'member@example.org', 'new_email' => 'monitor-50@test.invalid' ) );
	$GLOBALS['wp_users'][10]->user_email = 'member@example.org';
	$sync->handle_expire( array( 'wp_user_id' => 12 ) ); // test address, real account id
	same( array(), writes(), 'no handler sends a write for a real account' );
	foreach ( array( 'activate', 'expire', 'password_change', 'delete_user', 'email_change' ) as $action ) {
		same( array( 'fenced' ), $logger->statuses_for( 10, $action ), "$action for a real member is logged as fenced" );
	}
	same( array( 'fenced' ), $logger->statuses_for( 12, 'expire' ), 'expire via a stale cached id is logged as fenced' );
	same( array(), $GLOBALS['mail'], 'fenced refusals send no admin alert' );

	$r = $sync->sync_user( 10 );
	check( ! empty( $r['fenced'] ) && ! $r['success'], 'CLI sync of a real member is refused' );
	same( array(), writes(), '...without a request' );

	// --- Sync handlers: the monitor's round-trip ----------------------------
	clear_requests();
	$sync->handle_activate( array( 'wp_user_id' => 13 ) );
	same( array( 'POST /wpojs/users/find-or-create', 'POST /wpojs/subscriptions' ), writes(), 'activating a test user creates it and its subscription' );
	same( array( 'success' ), $logger->statuses_for( 13, 'activate' ), '...and logs success' );
	check( (int) get_user_meta( 13, '_wpojs_user_id', true ) >= 100, '...and caches its new OJS id' );

	list( $api, $sync, $logger ) = make(); // a separate Action Scheduler run
	clear_requests();
	$sync->handle_expire( array( 'wp_user_id' => 11 ) );
	$sync->handle_delete_user( array( 'wp_user_id' => 11, 'email' => 'monitor-9@test.invalid', 'ojs_user_id' => 77 ) );
	same( array( 'GET /wpojs/users', 'PUT /wpojs/subscriptions/expire-by-user/77', 'DELETE /wpojs/users/77' ), requests(), 'a test user is expired and deleted, after OJS confirms who it is' );

	list( $api, $sync, $logger ) = make();
	clear_requests();
	$r = $sync->sync_user( 13, false, true );
	check( $r['success'], 'CLI sync of a test user (the monitor\'s D1) succeeds' );

	// --- Reconciliation -------------------------------------------------------
	reset_wp();
	list( $api, $sync, $logger, $resolver ) = make();
	$resolver->active = array( 10, 14 ); // both missing an active OJS subscription
	( new WPOJS_Cron( $sync, $resolver, $api, $logger ) )->daily_reconcile();
	same(
		array( array( 'wpojs_sync_activate', array( array( 'wp_user_id' => 14 ) ) ), array( 'wpojs_sync_expire', array( array( 'wp_user_id' => 16 ) ) ) ),
		$GLOBALS['as'],
		'reconcile queues test accounts only'
	);
	same( array(), writes(), 'reconcile itself writes nothing' );
	check( in_array( 'POST /wpojs/subscriptions/status-batch', requests(), true ), 'reconcile still checks every member against OJS' );
	$summary = end( $logger->rows )['response_body'];
	check( strpos( $summary, '2 withheld by the fence' ) !== false, 'reconcile reports what the fence withheld: ' . $summary );

	// --- wp ojs-sync fence ----------------------------------------------------
	reset_wp();
	list( $api, $sync, $logger, $resolver ) = make();
	$cli         = new WPOJS_CLI( $sync, $resolver, $api, $logger, new WPOJS_Stats( $resolver, $logger ) );
	WP_CLI::$out = array();
	try {
		$cli->fence( array(), array() );
	} catch ( WP_CLI_Error $e ) {
		// Recorded in WP_CLI::$out; the check below fails on it.
	}
	$out = implode( "\n", WP_CLI::$out );
	check( strpos( $out, 'Success: Fence holds: 6 of 6 writes refused before any request; 2 of 5 linked journal accounts are outside the fence.' ) !== false, "fence command passes:\n$out" );
	check( strpos( $out, 'cannot be vouched for' ) !== false, 'fence command checks a real linked account' );
	same( array(), requests(), 'the fence command sends nothing to OJS' );
	same( array(), $GLOBALS['filters']['pre_http_request'] ?? array(), 'the fence command removes its HTTP block' );

	// It catches a write that forgets the fence, and still blocks it.
	reset_wp();
	list( $api, $sync, $logger, $resolver ) = make( new Leaky_Client() );
	$cli         = new WPOJS_CLI( $sync, $resolver, $api, $logger, new WPOJS_Stats( $resolver, $logger ) );
	WP_CLI::$out = array();
	$error       = null;
	try {
		$cli->fence( array(), array() );
	} catch ( WP_CLI_Error $e ) {
		$error = $e->getMessage();
	}
	check( $error !== null && strpos( $error, 'Fence LEAKS' ) !== false && strpos( $error, 'expire' ) !== false, 'fence command fails on a leaking write: ' . json_encode( $error ) );
	same( array(), requests(), '...and the leaking write never reached OJS' );
}

if ( $mode === 'unfenced' ) {
	reset_wp();
	$api = new WPOJS_API_Client();
	check( ! $api->is_fenced(), 'install reports unfenced' );
	check( $api->fence_admits_email( 'member@example.org' ), 'every email is admitted' );
	check( $api->vouch( 42, 'member@example.org' ), 'every account is vouched for' );
	same( array(), requests(), '...with no extra request' );
	foreach ( $real_writes( $api ) as $what => $result ) {
		check( $result['success'] && empty( $result['fenced'] ), "client sends $what as before" );
	}
	same( 6, count( writes() ), 'all six writes sent' );

	reset_wp();
	list( $api, $sync, $logger, $resolver ) = make();
	$sync->handle_expire( array( 'wp_user_id' => 10 ) );
	same( array( 'PUT /wpojs/subscriptions/expire-by-user/42' ), requests(), 'a real member is expired as before, with no extra lookup' );

	reset_wp();
	list( $api, $sync, $logger, $resolver ) = make();
	$resolver->active = array( 10, 14 );
	( new WPOJS_Cron( $sync, $resolver, $api, $logger ) )->daily_reconcile();
	same( 4, count( $GLOBALS['as'] ), 'reconcile queues everyone as before' );
	$summary = end( $logger->rows )['response_body'];
	check( strpos( $summary, 'fence' ) === false, 'reconcile summary unchanged: ' . $summary );

	$cli         = new WPOJS_CLI( $sync, $resolver, $api, $logger, new WPOJS_Stats( $resolver, $logger ) );
	WP_CLI::$out = array();
	$cli->fence( array(), array() );
	check( strpos( implode( "\n", WP_CLI::$out ), 'Fence: OFF' ) !== false, 'fence command reports OFF' );
}

if ( $mode === 'empty' || $mode === 'invalid' ) {
	reset_wp();
	$api = new WPOJS_API_Client();
	check( $api->is_fenced(), "$mode pattern still fences" );
	check( ! $api->fence_admits_email( 'monitor-1@test.invalid' ), "$mode pattern admits nothing, not even a test account" );
	check( ! empty( $api->find_or_create_user( 'monitor-1@test.invalid', 'M', 'T' )['fenced'] ), "$mode pattern refuses a test account's find-or-create" );
	foreach ( $real_writes( $api ) as $what => $result ) {
		check( ! empty( $result['fenced'] ), "$mode pattern refuses $what" );
	}
	same( array(), requests(), "$mode pattern sends nothing" );
}

restore_error_handler();
$total = $GLOBALS['passed'] + count( $GLOBALS['failed'] );
printf( "%-9s %d/%d passed\n", $mode, $GLOBALS['passed'], $total );
foreach ( $GLOBALS['failed'] as $f ) {
	echo "  FAIL: $f\n";
}
exit( $GLOBALS['failed'] ? 1 : 0 );
