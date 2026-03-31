<?php
/**
 * Create Ultimate Member core forms and pages if missing.
 *
 * UM's activation hook creates forms via install_default_forms(), but it
 * requires current_user_can('manage_options') which is false in WP-CLI.
 * This script does the same thing reliably: creates the um_form posts,
 * then creates the pages with correct form_id shortcodes.
 *
 * Idempotent — skips forms/pages that already exist.
 *
 * Usage: wp eval-file create-um-pages.php --allow-root
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit( 'Must be run via wp eval-file.' );
}

// --- Step 1: Create UM core forms (um_form post type) ---
// UM's install_default_forms() guards with current_user_can('manage_options')
// which fails in WP-CLI. We set the current user to admin and call it, or
// create the forms directly if that still doesn't work.

wp_set_current_user( 1 ); // admin user — needed for current_user_can check

$core_forms = get_option( 'um_core_forms', array() );

// Check if forms actually exist (option may reference deleted posts).
$forms_valid = true;
foreach ( array( 'login', 'register', 'profile' ) as $form_key ) {
	if ( empty( $core_forms[ $form_key ] ) || ! get_post( $core_forms[ $form_key ] ) ) {
		$forms_valid = false;
		break;
	}
}

$forms_created = 0;
if ( ! $forms_valid && function_exists( 'UM' ) ) {
	// Reset um_is_installed so UM's install routine will run.
	delete_option( 'um_is_installed' );
	delete_option( 'um_core_forms' );

	$setup = new um\core\Setup();
	$setup->install_default_forms();

	$core_forms = get_option( 'um_core_forms', array() );

	// Count how many were created.
	foreach ( array( 'login', 'register', 'profile' ) as $form_key ) {
		if ( ! empty( $core_forms[ $form_key ] ) && get_post( $core_forms[ $form_key ] ) ) {
			$forms_created++;
		}
	}
}

if ( $forms_created > 0 ) {
	WP_CLI::log( "Created $forms_created UM core form(s)." );
} else {
	WP_CLI::log( 'UM core forms already exist.' );
}

// --- Step 2: Create UM core pages with correct form IDs ---

$login_form_id    = ! empty( $core_forms['login'] ) ? $core_forms['login'] : '0';
$register_form_id = ! empty( $core_forms['register'] ) ? $core_forms['register'] : '0';
$profile_form_id  = ! empty( $core_forms['profile'] ) ? $core_forms['profile'] : '0';

$pages = array(
	'um_core_user'           => array( 'title' => 'User',           'content' => '[ultimatemember form_id="' . $profile_form_id . '"]' ),
	'um_core_account'        => array( 'title' => 'Account',        'content' => '[ultimatemember_account]' ),
	'um_core_login'          => array( 'title' => 'Login',          'content' => '[ultimatemember form_id="' . $login_form_id . '" /]' ),
	'um_core_register'       => array( 'title' => 'Register',       'content' => '[ultimatemember form_id="' . $register_form_id . '" /]' ),
	'um_core_members'        => array( 'title' => 'Members',        'content' => '[ultimatemember_members /]' ),
	'um_core_logout'         => array( 'title' => 'Logout',         'content' => '' ),
	'um_core_password-reset' => array( 'title' => 'Password Reset', 'content' => '[ultimatemember_password /]' ),
);

$pages_created = 0;
foreach ( $pages as $option => $def ) {
	$existing = get_option( $option );
	if ( $existing && get_post( $existing ) ) {
		// Page exists — but check if shortcode has form_id="0" that needs fixing.
		$post = get_post( $existing );
		if ( $post && strpos( $post->post_content, 'form_id="0"' ) !== false && isset( $def['content'] ) && strpos( $def['content'], 'form_id="0"' ) === false ) {
			wp_update_post( array(
				'ID'           => $post->ID,
				'post_content' => $def['content'],
			) );
			WP_CLI::log( "Updated {$def['title']} page shortcode with form ID." );
		}
		continue;
	}
	$id = wp_insert_post( array(
		'post_title'   => $def['title'],
		'post_content' => $def['content'],
		'post_status'  => 'publish',
		'post_type'    => 'page',
	) );
	update_option( $option, $id );
	$pages_created++;
}

// --- Step 3: Wire UM options to page IDs ---

if ( function_exists( 'UM' ) ) {
	$options = get_option( 'um_options', array() );
	$page_map = array(
		'core_login'          => 'um_core_login',
		'core_register'       => 'um_core_register',
		'core_user'           => 'um_core_user',
		'core_account'        => 'um_core_account',
		'core_members'        => 'um_core_members',
		'core_logout'         => 'um_core_logout',
		'core_password-reset' => 'um_core_password-reset',
	);
	$options_updated = false;
	foreach ( $page_map as $um_key => $wp_option ) {
		$page_id = get_option( $wp_option );
		if ( $page_id && ( empty( $options[ $um_key ] ) || $options[ $um_key ] != $page_id ) ) {
			$options[ $um_key ] = $page_id;
			$options_updated = true;
		}
	}
	if ( $options_updated ) {
		update_option( 'um_options', $options );
		WP_CLI::log( 'UM page settings updated.' );
	}
}

if ( $pages_created > 0 ) {
	WP_CLI::log( "Created $pages_created UM core page(s)." );
} else {
	WP_CLI::log( 'UM core pages already exist.' );
}
