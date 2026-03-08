<?php

namespace APP\plugins\generic\wpojsSubscriptionApi;

use Illuminate\Contracts\Hashing\Hasher;

/**
 * Custom hasher that understands WordPress password hashes.
 *
 * Stock WP 6.8+ uses `$wp$2y$10$...` (SHA-384 pre-hashed bcrypt).
 * Bedrock/roots uses `$2y$10$...` (standard bcrypt, no prehash).
 * OJS uses `$2y$12$...` via `password_hash()`.
 *
 * This hasher verifies both formats at login time. When a WP hash is
 * verified successfully, `needsRehash()` returns true so Laravel
 * automatically rehashes to native bcrypt on the next login.
 *
 * No plaintext passwords are stored or transmitted — only hashes.
 */
class WpCompatibleHasher implements Hasher
{
    private const WP_PREFIX = '$wp$';
    private const BCRYPT_COST = 12;

    /**
     * Hash a value (new password or rehash).
     * Always produces standard bcrypt — never WP format.
     */
    public function make(#[\SensitiveParameter] $value, array $options = []): string
    {
        $cost = $options['rounds'] ?? self::BCRYPT_COST;
        $hash = password_hash($value, PASSWORD_BCRYPT, ['cost' => $cost]);

        if ($hash === false) {
            throw new \RuntimeException('Bcrypt hashing failed.');
        }

        return $hash;
    }

    /**
     * Check a plaintext value against a hash.
     *
     * For WP hashes ($wp$2y$...): strip prefix, SHA-384 the plaintext,
     * then password_verify() against the inner bcrypt hash.
     *
     * For standard bcrypt ($2y$...): direct password_verify().
     */
    public function check(#[\SensitiveParameter] $value, $hashedValue, array $options = []): bool
    {
        if (empty($hashedValue)) {
            return false;
        }

        if (str_starts_with($hashedValue, self::WP_PREFIX)) {
            // WP 6.8+ format: $wp$2y$10$...
            // Strip the $wp prefix to get the inner bcrypt hash.
            $innerHash = substr($hashedValue, strlen(self::WP_PREFIX));

            // WP prehashes: SHA-384 of plaintext, base64-encoded.
            $prehash = base64_encode(hash('sha384', $value, true));

            return password_verify($prehash, $innerHash);
        }

        // Standard bcrypt or any other format PHP understands.
        return password_verify($value, $hashedValue);
    }

    /**
     * Check if a hash needs rehashing.
     *
     * All WP hashes need rehashing to native bcrypt.
     * Standard bcrypt hashes are checked against current cost.
     */
    public function needsRehash($hashedValue, array $options = []): bool
    {
        if (str_starts_with($hashedValue, self::WP_PREFIX)) {
            return true;
        }

        $cost = $options['rounds'] ?? self::BCRYPT_COST;
        return password_needs_rehash($hashedValue, PASSWORD_BCRYPT, ['cost' => $cost]);
    }

    public function info($hashedValue): array
    {
        if (str_starts_with($hashedValue, self::WP_PREFIX)) {
            $innerHash = substr($hashedValue, strlen(self::WP_PREFIX));
            $info = password_get_info($innerHash);
            $info['algoName'] = 'wp-bcrypt';
            return $info;
        }

        return password_get_info($hashedValue);
    }
}
