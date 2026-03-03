<?php
/**
 * Create Ultimate Member core pages if missing.
 *
 * UM shows an admin notice until these pages exist with their
 * IDs stored in um_core_* options. Idempotent — skips pages
 * that already have valid IDs.
 *
 * Usage: wp eval-file create-um-pages.php --allow-root
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit( 'Must be run via wp eval-file.' );
}

$pages = array(
	'um_core_user'           => array( 'title' => 'User',           'content' => '[ultimatemember form_id="0"]' ),
	'um_core_account'        => array( 'title' => 'Account',        'content' => '[ultimatemember_account]' ),
	'um_core_login'          => array( 'title' => 'Login',          'content' => '[ultimatemember form_id="0" /]' ),
	'um_core_register'       => array( 'title' => 'Register',       'content' => '[ultimatemember form_id="0" /]' ),
	'um_core_members'        => array( 'title' => 'Members',        'content' => '[ultimatemember form_id="0" /]' ),
	'um_core_logout'         => array( 'title' => 'Logout',         'content' => '' ),
	'um_core_password-reset' => array( 'title' => 'Password Reset', 'content' => '[ultimatemember_password /]' ),
);

$created = 0;
foreach ( $pages as $option => $def ) {
	$existing = get_option( $option );
	if ( $existing && get_post( $existing ) ) {
		continue;
	}
	$id = wp_insert_post( array(
		'post_title'   => $def['title'],
		'post_content' => $def['content'],
		'post_status'  => 'publish',
		'post_type'    => 'page',
	) );
	update_option( $option, $id );
	$created++;
}

if ( $created > 0 ) {
	WP_CLI::log( "Created $created UM core page(s)." );
} else {
	WP_CLI::log( 'UM core pages already exist.' );
}
