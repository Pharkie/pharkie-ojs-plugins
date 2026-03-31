<?php
/**
 * Dismiss noisy admin notices in the dev environment.
 *
 * Suppresses: UM license warnings, exif notice, WC onboarding
 * wizard, WC "store coming soon" banner, WC admin notices.
 * Idempotent — safe to run repeatedly.
 *
 * Usage: wp eval-file dismiss-notices.php --allow-root
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit( 'Must be run via wp eval-file.' );
}

// Ultimate Member: dismiss license + exif notices.
$um_notices = get_option( 'um_hidden_admin_notices', array() );
$um_dismiss = array(
	'wrong_license_key_um',
	'wrong_license_key_um_notifications',
	'wrong_license_key_um_woocommerce',
	'exif',
);
$added = 0;
foreach ( $um_dismiss as $key ) {
	if ( empty( $um_notices[ $key ] ) ) {
		$um_notices[ $key ] = true;
		$added++;
	}
}
if ( $added > 0 ) {
	update_option( 'um_hidden_admin_notices', $um_notices );
}

// WooCommerce: clear admin notices, complete onboarding, disable "coming soon".
update_option( 'woocommerce_admin_notices', array() );
update_option( 'woocommerce_onboarding_profile', array( 'completed' => true ) );
update_option( 'woocommerce_coming_soon', 'no' );

WP_CLI::log( 'Admin notices suppressed.' );
