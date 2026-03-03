<?php

if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

class WPOJS_Resolver {

    /**
     * Resolve what OJS subscription data a WP user should have.
     *
     * Checks all active WCS subscriptions + manual member roles.
     * Returns null if user is not an active member.
     *
     * @param int $wp_user_id
     * @return array|null ['type_id' => int, 'date_start' => string, 'date_end' => string|null]
     */
    public function resolve_subscription_data( $wp_user_id ) {
        $wcs_data    = $this->resolve_from_wcs( $wp_user_id );
        $manual_data = $this->resolve_from_manual_roles( $wp_user_id );

        // Not a member via either path.
        if ( ! $wcs_data && ! $manual_data ) {
            return null;
        }

        // Manual role only (no WCS subscription).
        if ( ! $wcs_data && $manual_data ) {
            return $manual_data;
        }

        // WCS subscription only (no manual role).
        if ( $wcs_data && ! $manual_data ) {
            return $wcs_data;
        }

        // Both: use WCS data but if manual role is non-expiring, override date_end to null.
        if ( $manual_data['date_end'] === null ) {
            $wcs_data['date_end'] = null;
        }

        return $wcs_data;
    }

    /**
     * Resolve subscription data from WooCommerce Subscriptions.
     *
     * @return array|null
     */
    private function resolve_from_wcs( $wp_user_id ) {
        if ( ! function_exists( 'wcs_get_subscriptions' ) ) {
            return null;
        }

        $subscriptions = wcs_get_subscriptions( array(
            'subscription_status' => 'active',
            'customer_id'         => $wp_user_id,
        ) );

        if ( empty( $subscriptions ) ) {
            return null;
        }

        $type_mapping   = get_option( 'wpojs_type_mapping', array() );
        $default_type   = (int) get_option( 'wpojs_default_type_id', 0 );
        $latest_end     = '';
        $type_id        = $default_type;
        $non_expiring   = false;
        $found_type     = false;
        $earliest_start = null;

        foreach ( $subscriptions as $sub ) {
            $end = $sub->get_date( 'end' );

            // Non-expiring (0 or empty string) wins.
            if ( $end === 0 || $end === '0' || $end === '' ) {
                $non_expiring = true;
            } elseif ( ! $non_expiring && ( $latest_end === '' || $end > $latest_end ) ) {
                $latest_end = $end;
            }

            // Track earliest start date across all active subscriptions.
            $start = $sub->get_date( 'start' );
            if ( $start && ( $earliest_start === null || $start < $earliest_start ) ) {
                $earliest_start = $start;
            }

            // Resolve type_id from the subscription's product.
            // Break out of both loops once found.
            if ( ! $found_type ) {
                $items = $sub->get_items();
                foreach ( $items as $item ) {
                    $product_id = $item->get_product_id();
                    if ( isset( $type_mapping[ $product_id ] ) ) {
                        $type_id    = (int) $type_mapping[ $product_id ];
                        $found_type = true;
                        break;
                    }
                }
            }
        }

        $date_end = $non_expiring ? null : $latest_end;

        // Format date_end as Y-m-d if it has a time component.
        if ( $date_end && strlen( $date_end ) > 10 ) {
            $date_end = substr( $date_end, 0, 10 );
        }

        // Use the subscription's actual start date, not today.
        if ( $earliest_start ) {
            $date_start = gmdate( 'Y-m-d', strtotime( $earliest_start ) );
        } else {
            $date_start = gmdate( 'Y-m-d' );
        }

        return array(
            'type_id'    => $type_id ?: $default_type,
            'date_start' => $date_start,
            'date_end'   => $date_end,
        );
    }

    /**
     * Resolve subscription data from manual member roles.
     * Manual roles are always non-expiring.
     *
     * @return array|null
     */
    private function resolve_from_manual_roles( $wp_user_id ) {
        $member_roles = $this->get_manual_member_roles();
        if ( empty( $member_roles ) ) {
            return null;
        }

        $user = get_userdata( $wp_user_id );
        if ( ! $user ) {
            return null;
        }

        $has_manual_role = ! empty( array_intersect( $user->roles, $member_roles ) );
        if ( ! $has_manual_role ) {
            return null;
        }

        $default_type = (int) get_option( 'wpojs_default_type_id', 0 );

        return array(
            'type_id'    => $default_type,
            'date_start' => gmdate( 'Y-m-d', strtotime( $user->user_registered ) ),
            'date_end'   => null, // Manual roles are always non-expiring.
        );
    }

    /**
     * Get all active members: union of active WCS subscribers + users with manual member roles.
     *
     * @return array Array of WP user IDs.
     */
    public function get_all_active_members() {
        $user_ids = array();

        // WCS subscribers — paginated to avoid memory issues with large member counts.
        if ( function_exists( 'wcs_get_subscriptions' ) ) {
            $page = 1;
            do {
                $subscriptions = wcs_get_subscriptions( array(
                    'subscription_status'    => 'active',
                    'subscriptions_per_page' => 500,
                    'paged'                  => $page,
                ) );

                foreach ( $subscriptions as $sub ) {
                    $user_ids[] = $sub->get_user_id();
                }

                $page++;
            } while ( count( $subscriptions ) === 500 );
        }

        // Manual role members.
        $manual_roles = $this->get_manual_member_roles();
        if ( ! empty( $manual_roles ) ) {
            foreach ( $manual_roles as $role ) {
                $users = get_users( array( 'role' => $role, 'fields' => 'ID' ) );
                $user_ids = array_merge( $user_ids, $users );
            }
        }

        return array_unique( array_map( 'intval', $user_ids ) );
    }

    /**
     * Check if a WP user is an active member (via WCS or manual role).
     *
     * @param int $wp_user_id
     * @param int $exclude_subscription_id Optional subscription ID to exclude from the check.
     *                                     Used when a subscription is being cancelled/expired
     *                                     to avoid stale cache returning it as still active.
     * @return bool
     */
    public function is_active_member( $wp_user_id, $exclude_subscription_id = 0 ) {
        // Check WCS subscriptions.
        if ( function_exists( 'wcs_get_subscriptions' ) ) {
            $subs = wcs_get_subscriptions( array(
                'subscription_status' => 'active',
                'customer_id'         => $wp_user_id,
            ) );
            foreach ( $subs as $sub ) {
                if ( $exclude_subscription_id && $sub->get_id() === $exclude_subscription_id ) {
                    continue;
                }
                // Found an active subscription that isn't the excluded one.
                return true;
            }
        }

        // Check manual roles.
        $manual_roles = $this->get_manual_member_roles();
        if ( ! empty( $manual_roles ) ) {
            $user = get_userdata( $wp_user_id );
            if ( $user && ! empty( array_intersect( $user->roles, $manual_roles ) ) ) {
                return true;
            }
        }

        return false;
    }

    /**
     * Get configured manual member roles (admin-assigned roles that grant OJS access).
     *
     * @return array Array of WP role slugs.
     */
    private function get_manual_member_roles() {
        return get_option( 'wpojs_manual_roles', array() );
    }

    /**
     * Get all configured member roles (WCS-linked + manual).
     *
     * @return array
     */
    public function get_all_member_roles() {
        return get_option( 'wpojs_member_roles', array() );
    }
}
