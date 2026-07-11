<?php

namespace APP\plugins\generic\wpojsSubscriptionApi;

use Illuminate\Contracts\Hashing\Hasher;

/**
 * Custom hasher that understands WordPress password hashes.
 *
 * Stock WP 6.8+ uses `$wp$2y$10$...` (SHA-384 pre-hashed bcrypt).
 * Pre-6.8 WP uses `$P$B...` (portable phpass, MD5-based) — these
 * survive in wp_users until the member next logs into WP, so the
 * sync copies them into OJS verbatim.
 * Bedrock/roots uses `$2y$10$...` (standard bcrypt, no prehash).
 * OJS uses `$2y$12$...` via `password_hash()`.
 *
 * This hasher verifies all three formats at login time. When a WP
 * hash is verified successfully, `needsRehash()` returns true so
 * Laravel automatically rehashes to native bcrypt on the next login.
 *
 * No plaintext passwords are stored or transmitted — only hashes.
 */
class WpCompatibleHasher implements Hasher
{
    private const WP_PREFIX = '$wp';
    private const PHPASS_PREFIXES = ['$P$', '$H$'];
    private const PHPASS_ITOA64 = './0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz';
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
     * For legacy WP phpass hashes ($P$/$H$): portable phpass check.
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

            // WP prehashes: HMAC-SHA384 with 'wp-sha384' key, base64-encoded.
            // See wp-includes/pluggable.php wp_hash_password().
            $prehash = base64_encode(hash_hmac('sha384', $value, 'wp-sha384', true));

            return password_verify($prehash, $innerHash);
        }

        if ($this->isPhpassHash($hashedValue)) {
            // Pre-6.8 WP format: $P$B<salt><hash> (portable phpass).
            return $this->phpassVerify($value, $hashedValue);
        }

        // Standard bcrypt or any other format PHP understands.
        return password_verify($value, $hashedValue);
    }

    private function isPhpassHash(string $hashedValue): bool
    {
        foreach (self::PHPASS_PREFIXES as $prefix) {
            if (str_starts_with($hashedValue, $prefix)) {
                return true;
            }
        }
        return false;
    }

    /**
     * Verify a password against a portable phpass hash ($P$ or $H$).
     *
     * Port of phpass 0.3 crypt_private() as bundled with WP <6.8
     * (wp-includes/class-phpass.php): iterated raw MD5 with a custom
     * base-64 alphabet. Format: 3-char prefix, 1 char cost (log2 of
     * iteration count), 8-char salt, 22-char encoded digest = 34 chars.
     */
    private function phpassVerify(#[\SensitiveParameter] string $value, string $hashedValue): bool
    {
        if (strlen($hashedValue) !== 34) {
            return false;
        }

        $countLog2 = strpos(self::PHPASS_ITOA64, $hashedValue[3]);
        if ($countLog2 === false || $countLog2 < 7 || $countLog2 > 30) {
            return false;
        }
        $count = 1 << $countLog2;

        $salt = substr($hashedValue, 4, 8);
        $digest = md5($salt . $value, true);
        do {
            $digest = md5($digest . $value, true);
        } while (--$count);

        $computed = substr($hashedValue, 0, 12) . $this->phpassEncode64($digest, 16);

        return hash_equals($hashedValue, $computed);
    }

    /**
     * phpass base-64 encoding (not RFC 4648 — custom alphabet, little-endian).
     */
    private function phpassEncode64(string $input, int $count): string
    {
        $output = '';
        $i = 0;
        do {
            $value = ord($input[$i++]);
            $output .= self::PHPASS_ITOA64[$value & 0x3f];
            if ($i < $count) {
                $value |= ord($input[$i]) << 8;
            }
            $output .= self::PHPASS_ITOA64[($value >> 6) & 0x3f];
            if ($i++ >= $count) {
                break;
            }
            if ($i < $count) {
                $value |= ord($input[$i]) << 16;
            }
            $output .= self::PHPASS_ITOA64[($value >> 12) & 0x3f];
            if ($i++ >= $count) {
                break;
            }
            $output .= self::PHPASS_ITOA64[($value >> 18) & 0x3f];
        } while ($i < $count);

        return $output;
    }

    /**
     * Check if a hash needs rehashing.
     *
     * All WP hashes (current and legacy phpass) need rehashing to
     * native bcrypt. Standard bcrypt hashes are checked against
     * current cost.
     */
    public function needsRehash($hashedValue, array $options = []): bool
    {
        if (str_starts_with($hashedValue, self::WP_PREFIX) || $this->isPhpassHash($hashedValue)) {
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

        if ($this->isPhpassHash($hashedValue)) {
            $info = password_get_info('');
            $info['algoName'] = 'wp-phpass';
            return $info;
        }

        return password_get_info($hashedValue);
    }
}
