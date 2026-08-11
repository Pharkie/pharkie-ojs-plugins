<?php

if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

class WPOJS_Resolver {

    /**
     * Resolve what OJS subscription data a WP user should have.
     *
     * Three paths grant access: an active WooCommerce Subscription, an active
     * WooCommerce Memberships plan, or a manual member role. Returns null if
     * the user is not an active member by any of them.
     *
     * @param int $wp_user_id
     * @return array|null ['type_id' => int, 'date_start' => string, 'date_end' => string|null]
     */
    public function resolve_subscription_data( $wp_user_id ) {
        // Priority order: a WCS subscription is the most specific source (its
        // product can map to a particular OJS type), then a membership plan,
        // then a manual role. The first one present supplies the type and the
        // dates; any of them being non-expiring makes the whole grant
        // non-expiring, which is what a life membership is.
        $candidates = array_values( array_filter( array(
            $this->resolve_from_wcs( $wp_user_id ),
            $this->resolve_from_memberships( $wp_user_id ),
            $this->resolve_from_manual_roles( $wp_user_id ),
        ) ) );

        if ( empty( $candidates ) ) {
            return null;
        }

        $data = array_shift( $candidates );
        foreach ( $candidates as $other ) {
            if ( $other['date_end'] === null ) {
                $data['date_end'] = null;
            }
        }

        return $data;
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
     * Resolve subscription data from WooCommerce Memberships plans.
     *
     * The plan is the only record of the members who never buy anything: a life
     * member holds an active `LIFE MEMBER` membership with no end date and no
     * subscription at all, so nothing on the WCS path ever sees them. Honorary
     * and committee grants work the same way.
     *
     * Queried directly rather than through `wc_memberships_get_user_memberships()`
     * because the reconciliation runs in cron/CLI contexts, and because the
     * member is the post_author of a `wc_user_membership` post — the same shape
     * the batch query in get_all_active_members() needs.
     *
     * @return array|null
     */
    private function resolve_from_memberships( $wp_user_id ) {
        $plan_ids = $this->get_member_plans();
        if ( empty( $plan_ids ) ) {
            return null;
        }

        global $wpdb;

        $statuses      = $this->active_membership_post_statuses();
        $status_places = implode( ',', array_fill( 0, count( $statuses ), '%s' ) );
        $plan_places   = implode( ',', array_fill( 0, count( $plan_ids ), '%d' ) );

        $rows = $wpdb->get_results(
            $wpdb->prepare(
                "SELECT sd.meta_value AS start_date, ed.meta_value AS end_date
                FROM {$wpdb->posts} um
                LEFT JOIN {$wpdb->postmeta} sd ON sd.post_id = um.ID AND sd.meta_key = '_start_date'
                LEFT JOIN {$wpdb->postmeta} ed ON ed.post_id = um.ID AND ed.meta_key = '_end_date'
                WHERE um.post_type = 'wc_user_membership'
                AND um.post_author = %d
                AND um.post_status IN ($status_places)
                AND um.post_parent IN ($plan_places)",
                array_merge( array( (int) $wp_user_id ), $statuses, $plan_ids )
            ),
            ARRAY_A
        );

        if ( empty( $rows ) ) {
            return null;
        }

        $default_type   = (int) get_option( 'wpojs_default_type_id', 0 );
        $non_expiring   = false;
        $latest_end     = '';
        $earliest_start = null;

        foreach ( $rows as $row ) {
            // No end date = unlimited membership (life members). Unlimited wins
            // over any dated membership the same person also holds.
            $end = isset( $row['end_date'] ) ? trim( (string) $row['end_date'] ) : '';
            if ( $end === '' || $end === '0' || strpos( $end, '0000-00-00' ) === 0 ) {
                $non_expiring = true;
            } elseif ( ! $non_expiring && ( $latest_end === '' || $end > $latest_end ) ) {
                $latest_end = $end;
            }

            $start = isset( $row['start_date'] ) ? trim( (string) $row['start_date'] ) : '';
            if ( $start !== '' && ( $earliest_start === null || $start < $earliest_start ) ) {
                $earliest_start = $start;
            }
        }

        // Dates are stored as 'Y-m-d H:i:s' UTC; OJS wants Y-m-d.
        $date_end = $non_expiring ? null : substr( $latest_end, 0, 10 );

        return array(
            'type_id'    => $default_type,
            'date_start' => $earliest_start ? gmdate( 'Y-m-d', strtotime( $earliest_start ) ) : gmdate( 'Y-m-d' ),
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
        global $wpdb;
        $user_ids = array();

        // WCS subscribers — direct query for user IDs only (avoids hydrating
        // full WC_Subscription objects which is extremely slow at scale).
        // Supports both HPOS (wc_orders) and legacy (wp_posts + wp_postmeta).
        $hpos_enabled = 'yes' === get_option( 'woocommerce_custom_orders_table_enabled', 'no' );
        if ( $hpos_enabled ) {
            $wcs_ids = $wpdb->get_col(
                "SELECT DISTINCT customer_id
                FROM {$wpdb->prefix}wc_orders
                WHERE type = 'shop_subscription'
                AND status = 'wc-active'
                AND customer_id > 0"
            );
        } else {
            $wcs_ids = $wpdb->get_col(
                "SELECT DISTINCT pm.meta_value
                FROM {$wpdb->prefix}posts p
                JOIN {$wpdb->prefix}postmeta pm ON p.ID = pm.post_id AND pm.meta_key = '_customer_user'
                WHERE p.post_type = 'shop_subscription'
                AND p.post_status = 'wc-active'
                AND pm.meta_value > 0"
            );
        }
        if ( $wcs_ids ) {
            $user_ids = array_map( 'intval', $wcs_ids );
        }

        // WooCommerce Memberships plan holders. Life and honorary members live
        // here and nowhere else — most of them have no subscription at all, so
        // the query above never returns them.
        $plan_ids = $this->get_member_plans();
        if ( ! empty( $plan_ids ) ) {
            $statuses      = $this->active_membership_post_statuses();
            $status_places = implode( ',', array_fill( 0, count( $statuses ), '%s' ) );
            $plan_places   = implode( ',', array_fill( 0, count( $plan_ids ), '%d' ) );

            $plan_member_ids = $wpdb->get_col(
                $wpdb->prepare(
                    "SELECT DISTINCT um.post_author
                    FROM {$wpdb->posts} um
                    WHERE um.post_type = 'wc_user_membership'
                    AND um.post_author > 0
                    AND um.post_status IN ($status_places)
                    AND um.post_parent IN ($plan_places)",
                    array_merge( $statuses, $plan_ids )
                )
            );
            if ( $plan_member_ids ) {
                $user_ids = array_merge( $user_ids, array_map( 'intval', $plan_member_ids ) );
            }
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

        // Check WooCommerce Memberships plans. No exclusion argument is needed:
        // the membership hooks fire on `transition_post_status`, by which point
        // the new status is already in the posts table.
        if ( $this->resolve_from_memberships( $wp_user_id ) !== null ) {
            return true;
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
        $roles = get_option( 'wpojs_manual_roles', array() );
        return is_array( $roles ) ? array_values( array_filter( $roles ) ) : array();
    }

    /**
     * Get configured WooCommerce Memberships plans that grant OJS access.
     *
     * @return array Array of plan post IDs.
     */
    private function get_member_plans() {
        $plans = get_option( 'wpojs_member_plans', array() );
        if ( ! is_array( $plans ) ) {
            return array();
        }
        return array_values( array_filter( array_map( 'intval', $plans ) ) );
    }

    /**
     * The `wc_user_membership` post statuses that grant access.
     *
     * Mirrors WooCommerce Memberships' own definition —
     * `WC_Memberships_User_Memberships::get_active_access_membership_statuses()`
     * (active, complimentary, free_trial, pending; "pending" is *Pending
     * Cancellation*, which keeps access until the end date). Asked of the
     * plugin when it is loaded so a site filter is honoured, with the literal
     * list from woocommerce-memberships 1.27.5 as the fallback.
     *
     * @return array Array of prefixed post statuses (wcm-active, ...).
     */
    private function active_membership_post_statuses() {
        $statuses = array( 'active', 'complimentary', 'free_trial', 'pending' );

        if ( function_exists( 'wc_memberships' ) ) {
            $memberships = wc_memberships();
            if ( $memberships && method_exists( $memberships, 'get_user_memberships_instance' ) ) {
                $user_memberships = $memberships->get_user_memberships_instance();
                if ( $user_memberships && method_exists( $user_memberships, 'get_active_access_membership_statuses' ) ) {
                    $from_plugin = $user_memberships->get_active_access_membership_statuses();
                    if ( is_array( $from_plugin ) && ! empty( $from_plugin ) ) {
                        $statuses = $from_plugin;
                    }
                }
            }
        }

        $prefixed = array();
        foreach ( $statuses as $status ) {
            // The plugin returns unprefixed slugs; they are stored prefixed.
            $prefixed[] = strpos( $status, 'wcm-' ) === 0 ? $status : 'wcm-' . $status;
        }
        return $prefixed;
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
