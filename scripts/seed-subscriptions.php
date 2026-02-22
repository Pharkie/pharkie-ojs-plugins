<?php
/**
 * Seed WooCommerce Subscription test data.
 *
 * Creates subscription products and active subscription records for test users
 * with um_custom_role_1–6, so the resolver finds them as active members.
 *
 * Uses direct SQL batch inserts (~1.5s for ~683 subscriptions) rather than
 * the WCS API (~10min — every save() fires hooks, recalculates totals, and
 * triggers WC Memberships/UM cascades). The tradeoff is that this script is
 * coupled to the WCS database schema (wp_posts, wp_postmeta,
 * wp_woocommerce_order_items, wp_woocommerce_order_itemmeta). If WCS changes
 * its storage model (e.g. HPOS migration), this script will need updating.
 *
 * This is test data seeding only — production has real WCS subscriptions.
 *
 * Usage:
 *   wp eval-file seed-subscriptions.php /data/test-users.csv --allow-root
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit( 'Must be run via wp eval-file.' );
}

global $wpdb;

$csv_path = $args[0] ?? '';
if ( ! $csv_path || ! file_exists( $csv_path ) ) {
	WP_CLI::error( "CSV file not found: $csv_path" );
}

// Role → product definition (roles 1–6 = WCS-linked membership tiers).
$product_defs = array(
	'um_custom_role_1' => array( 'name' => 'UK Membership (no listing)',            'sku' => 'wpojs-uk-no-listing',      'price' => '50' ),
	'um_custom_role_2' => array( 'name' => 'UK Membership (with listing)',           'sku' => 'wpojs-uk-listing',          'price' => '50' ),
	'um_custom_role_3' => array( 'name' => 'Student Membership (no listing)',        'sku' => 'wpojs-student-no-listing',  'price' => '35' ),
	'um_custom_role_4' => array( 'name' => 'Student Membership (with listing)',      'sku' => 'wpojs-student-listing',     'price' => '35' ),
	'um_custom_role_5' => array( 'name' => 'International Membership (no listing)',  'sku' => 'wpojs-intl-no-listing',     'price' => '60' ),
	'um_custom_role_6' => array( 'name' => 'International Membership (with listing)','sku' => 'wpojs-intl-listing',        'price' => '60' ),
);

$wcs_roles = array_keys( $product_defs );

// --- Part A: Create subscription products (idempotent by SKU) ---

WP_CLI::log( 'Creating subscription products...' );

$role_to_pid = array();
foreach ( $product_defs as $role => $def ) {
	$existing_id = wc_get_product_id_by_sku( $def['sku'] );
	if ( $existing_id ) {
		$role_to_pid[ $role ] = $existing_id;
		continue;
	}
	$product = new WC_Product_Simple();
	$product->set_name( $def['name'] );
	$product->set_sku( $def['sku'] );
	$product->set_regular_price( $def['price'] );
	$product->set_status( 'publish' );
	$product->set_virtual( true );
	$product->save();
	$role_to_pid[ $role ] = $product->get_id();
}

WP_CLI::log( sprintf( '  %d products ready.', count( $role_to_pid ) ) );

// --- Part B: Batch-insert WCS subscriptions (direct SQL) ---

$handle = fopen( $csv_path, 'r' );
if ( ! $handle ) {
	WP_CLI::error( "Cannot open CSV: $csv_path" );
}

$header    = fgetcsv( $handle );
$col_login = array_search( 'user_login', $header, true );
$col_role  = array_search( 'original_role', $header, true );

if ( $col_login === false || $col_role === false ) {
	fclose( $handle );
	WP_CLI::error( 'CSV must have user_login and original_role columns.' );
}

$members = array();
while ( ( $row = fgetcsv( $handle ) ) !== false ) {
	$role = $row[ $col_role ];
	if ( in_array( $role, $wcs_roles, true ) ) {
		$members[ $row[ $col_login ] ] = $role;
	}
}
fclose( $handle );

WP_CLI::log( sprintf( '  %d members with WCS roles in CSV.', count( $members ) ) );

if ( ! empty( $members ) ) {
	// Look up WP user IDs.
	$placeholders = implode( ',', array_fill( 0, count( $members ), '%s' ) );
	$user_rows    = $wpdb->get_results(
		$wpdb->prepare(
			"SELECT ID, user_login FROM {$wpdb->users} WHERE user_login IN ($placeholders)",
			...array_keys( $members )
		)
	);

	$user_map = array(); // user_id => role
	foreach ( $user_rows as $row ) {
		$user_map[ (int) $row->ID ] = $members[ $row->user_login ];
	}

	WP_CLI::log( sprintf( '  %d users found in WP.', count( $user_map ) ) );

	// Idempotency: skip if subscriptions already exist for these users.
	$user_ids_csv = implode( ',', array_map( 'intval', array_keys( $user_map ) ) );
	$existing     = (int) $wpdb->get_var(
		"SELECT COUNT(*) FROM {$wpdb->posts}
		 WHERE post_type = 'shop_subscription' AND post_author IN ($user_ids_csv)"
	);

	if ( $existing > 0 ) {
		WP_CLI::log( "  Subscriptions already exist ($existing found), skipping insert." );
	} else {
		$start_date = gmdate( 'Y-m-d H:i:s', strtotime( '-1 year' ) );
		$batch_size = 100;
		$user_ids   = array_keys( $user_map );

		// 1. Insert subscription posts.
		WP_CLI::log( '  Inserting subscription posts...' );
		foreach ( array_chunk( $user_ids, $batch_size ) as $chunk ) {
			$values = array();
			foreach ( $chunk as $uid ) {
				$values[] = $wpdb->prepare(
					"(%d, %s, %s, '', '', '', 'wc-active', 'open', 'closed', '', %s, '', '', %s, %s, '', 0, '', 0, 'shop_subscription', '', 0)",
					$uid, $start_date, $start_date, 'wpojs-sub-' . $uid, $start_date, $start_date
				);
			}
			$wpdb->query(
				"INSERT INTO {$wpdb->posts}
				 (post_author, post_date, post_date_gmt, post_content, post_title, post_excerpt,
				  post_status, comment_status, ping_status, post_password, post_name, to_ping, pinged,
				  post_modified, post_modified_gmt, post_content_filtered, post_parent, guid,
				  menu_order, post_type, post_mime_type, comment_count)
				 VALUES " . implode( ', ', $values )
			);
		}

		// 2. Map user_id → subscription post ID.
		$sub_rows = $wpdb->get_results(
			"SELECT ID, post_author FROM {$wpdb->posts}
			 WHERE post_type = 'shop_subscription' AND post_author IN ($user_ids_csv)"
		);
		$sub_map = array();
		foreach ( $sub_rows as $row ) {
			$sub_map[ (int) $row->post_author ] = (int) $row->ID;
		}

		// 3. Insert postmeta (_schedule_start, _schedule_end, _customer_user).
		WP_CLI::log( '  Inserting subscription meta...' );
		foreach ( array_chunk( $user_ids, $batch_size ) as $chunk ) {
			$values = array();
			foreach ( $chunk as $uid ) {
				$sid      = $sub_map[ $uid ];
				$values[] = $wpdb->prepare( '(%d, %s, %s)', $sid, '_schedule_start', $start_date );
				$values[] = $wpdb->prepare( '(%d, %s, %s)', $sid, '_schedule_end', '0' );
				$values[] = $wpdb->prepare( '(%d, %s, %d)', $sid, '_customer_user', $uid );
			}
			$wpdb->query(
				"INSERT INTO {$wpdb->postmeta} (post_id, meta_key, meta_value)
				 VALUES " . implode( ', ', $values )
			);
		}

		// 4. Insert order items (line items linking subscription to product).
		WP_CLI::log( '  Inserting order items...' );
		foreach ( array_chunk( $user_ids, $batch_size ) as $chunk ) {
			$values = array();
			foreach ( $chunk as $uid ) {
				$sid  = $sub_map[ $uid ];
				$name = $product_defs[ $user_map[ $uid ] ]['name'];
				$values[] = $wpdb->prepare( '(%s, %s, %d)', $name, 'line_item', $sid );
			}
			$wpdb->query(
				"INSERT INTO {$wpdb->prefix}woocommerce_order_items (order_item_name, order_item_type, order_id)
				 VALUES " . implode( ', ', $values )
			);
		}

		// 5. Map subscription_id → order item ID.
		$sub_ids_csv = implode( ',', array_map( 'intval', array_values( $sub_map ) ) );
		$item_rows   = $wpdb->get_results(
			"SELECT order_item_id, order_id FROM {$wpdb->prefix}woocommerce_order_items
			 WHERE order_id IN ($sub_ids_csv)"
		);
		$item_map = array();
		foreach ( $item_rows as $row ) {
			$item_map[ (int) $row->order_id ] = (int) $row->order_item_id;
		}

		// 6. Insert order item meta (_product_id).
		WP_CLI::log( '  Inserting order item meta...' );
		foreach ( array_chunk( $user_ids, $batch_size ) as $chunk ) {
			$values = array();
			foreach ( $chunk as $uid ) {
				$sid     = $sub_map[ $uid ];
				$item_id = $item_map[ $sid ];
				$pid     = $role_to_pid[ $user_map[ $uid ] ];
				$values[] = $wpdb->prepare( '(%d, %s, %d)', $item_id, '_product_id', $pid );
			}
			$wpdb->query(
				"INSERT INTO {$wpdb->prefix}woocommerce_order_itemmeta (order_item_id, meta_key, meta_value)
				 VALUES " . implode( ', ', $values )
			);
		}

		// 7. Populate WCS customer subscription cache (wp_usermeta).
		// WCS maintains a per-user cache of subscription IDs in the
		// _wcs_subscription_ids_cache user meta key. Direct SQL inserts
		// bypass the hooks that update this cache, so we must set it
		// explicitly. Without this, wcs_get_subscriptions(customer_id)
		// returns empty results for seeded users.
		//
		// The user_register hook (fired during wp user import-csv) sets
		// an empty cache for each new user. We must delete those first,
		// then insert the real cache values.
		WP_CLI::log( '  Populating WCS customer cache...' );
		$wpdb->query(
			"DELETE FROM {$wpdb->usermeta}
			 WHERE meta_key = '_wcs_subscription_ids_cache' AND user_id IN ($user_ids_csv)"
		);
		foreach ( array_chunk( $user_ids, $batch_size ) as $chunk ) {
			$values = array();
			foreach ( $chunk as $uid ) {
				$cache = serialize( array( $sub_map[ $uid ] ) );
				$values[] = $wpdb->prepare( '(%d, %s, %s)', $uid, '_wcs_subscription_ids_cache', $cache );
			}
			$wpdb->query(
				"INSERT INTO {$wpdb->usermeta} (user_id, meta_key, meta_value)
				 VALUES " . implode( ', ', $values )
			);
		}

		WP_CLI::log( sprintf( '  %d subscriptions created.', count( $user_ids ) ) );
	}
}

// --- Part C: Configure WP options ---

WP_CLI::log( 'Setting WP options...' );

$type_mapping = array();
foreach ( $role_to_pid as $pid ) {
	$type_mapping[ $pid ] = 1;
}
update_option( 'wpojs_type_mapping', $type_mapping );
update_option( 'wpojs_default_type_id', 1 );

update_option( 'wpojs_member_roles', array(
	'um_custom_role_1', 'um_custom_role_2', 'um_custom_role_3',
	'um_custom_role_4', 'um_custom_role_5', 'um_custom_role_6',
	'um_custom_role_7', 'um_custom_role_8', 'um_custom_role_9',
) );

update_option( 'wpojs_manual_roles', array(
	'um_custom_role_7', 'um_custom_role_8', 'um_custom_role_9',
) );

WP_CLI::success( 'Subscription seeding complete.' );
