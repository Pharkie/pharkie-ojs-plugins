<?php
/**
 * Apply original UM/WCS roles from the anonymised CSV.
 *
 * wp user import-csv can't assign UM/WCS roles (validates before UM registers
 * them), so users are imported as 'subscriber'. This script reads the CSV and
 * updates wp_usermeta directly — fast (~2s for 1400 users) vs wp user set-role
 * (~10min, fires hooks per user).
 *
 * This is test data seeding only — production members already exist in WP.
 *
 * Usage:
 *   wp eval-file apply-roles.php /data/test-users.csv --allow-root
 */

if ( ! defined( 'ABSPATH' ) ) {
    exit( 'Must be run via wp eval-file.' );
}

global $wpdb;

// Register UM custom roles so they appear in wp_roles()->get_names().
// Display names match production (Ultimate Member > User Roles).
$um_roles = array(
    'um_custom_role_1' => 'SEA UK member (no listing)',
    'um_custom_role_2' => 'SEA UK member (with listing)',
    'um_custom_role_3' => 'SEA student member (no listing)',
    'um_custom_role_4' => 'SEA student member (with listing)',
    'um_custom_role_5' => 'SEA international member (no listing)',
    'um_custom_role_6' => 'SEA international member (with listing)',
    'um_custom_role_7' => 'Manually set UK listing (for Exco/life members)',
    'um_custom_role_8' => 'Manually set international listing (for Exco/life members)',
    'um_custom_role_9' => 'Manually set student listing (for Exco/life members)',
);
$registered = 0;
foreach ( $um_roles as $slug => $name ) {
    if ( ! wp_roles()->is_role( $slug ) ) {
        add_role( $slug, $name, array( 'read' => true ) );
        $registered++;
    }
}
if ( $registered ) {
    WP_CLI::log( "Registered $registered UM roles." );
}

$csv_path = $args[0] ?? '';
if ( ! $csv_path || ! file_exists( $csv_path ) ) {
    WP_CLI::error( "CSV file not found: $csv_path" );
}

$handle = fopen( $csv_path, 'r' );
if ( ! $handle ) {
    WP_CLI::error( "Cannot open CSV: $csv_path" );
}

$header = fgetcsv( $handle );
$col_login = array_search( 'user_login', $header, true );
$col_role  = array_search( 'original_role', $header, true );

if ( $col_login === false || $col_role === false ) {
    fclose( $handle );
    WP_CLI::error( 'CSV must have user_login and original_role columns.' );
}

$updated = 0;
$skipped = 0;
$errors  = 0;

while ( ( $row = fgetcsv( $handle ) ) !== false ) {
    $login = $row[ $col_login ];
    $role  = $row[ $col_role ];

    // Skip if already subscriber (no change needed).
    if ( $role === 'subscriber' || $role === '' ) {
        $skipped++;
        continue;
    }

    // Look up user ID.
    $user_id = $wpdb->get_var( $wpdb->prepare(
        "SELECT ID FROM {$wpdb->users} WHERE user_login = %s",
        $login
    ) );

    if ( ! $user_id ) {
        $errors++;
        continue;
    }

    // Build serialized capabilities value: a:1:{s:LEN:"ROLE";b:1;}
    $caps = serialize( array( $role => true ) );

    $wpdb->update(
        $wpdb->usermeta,
        array( 'meta_value' => $caps ),
        array(
            'user_id'  => $user_id,
            'meta_key' => $wpdb->prefix . 'capabilities',
        )
    );

    $updated++;
}

fclose( $handle );

WP_CLI::success( "Roles applied: $updated updated, $skipped already subscriber, $errors not found." );
