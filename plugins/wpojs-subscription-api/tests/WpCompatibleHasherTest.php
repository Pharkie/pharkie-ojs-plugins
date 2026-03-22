<?php

namespace APP\plugins\generic\wpojsSubscriptionApi\Tests;

use APP\plugins\generic\wpojsSubscriptionApi\WpCompatibleHasher;
use PHPUnit\Framework\TestCase;

class WpCompatibleHasherTest extends TestCase
{
    private WpCompatibleHasher $hasher;

    protected function setUp(): void
    {
        $this->hasher = new WpCompatibleHasher();
    }

    // ---------------------------------------------------------------
    // check() — standard bcrypt
    // ---------------------------------------------------------------

    public function testStandardBcryptVerificationSucceeds(): void
    {
        $password = 'correct-horse-battery-staple';
        $hash = password_hash($password, PASSWORD_BCRYPT);

        $this->assertTrue($this->hasher->check($password, $hash));
    }

    public function testStandardBcryptWrongPasswordFails(): void
    {
        $password = 'correct-horse-battery-staple';
        $hash = password_hash($password, PASSWORD_BCRYPT);

        $this->assertFalse($this->hasher->check('wrong-password', $hash));
    }

    // ---------------------------------------------------------------
    // check() — WP 6.8+ format ($wp$ prefix with SHA-384 prehashed bcrypt)
    // ---------------------------------------------------------------

    public function testWp68FormatVerificationSucceeds(): void
    {
        $password = 'wp-member-password';

        // Replicate WP 6.8+ hashing: SHA-384 HMAC prehash, then bcrypt, then $wp$ prefix.
        $prehash = base64_encode(hash_hmac('sha384', $password, 'wp-sha384', true));
        $innerHash = password_hash($prehash, PASSWORD_BCRYPT);
        $wpHash = '$wp' . $innerHash;

        $this->assertTrue($this->hasher->check($password, $wpHash));
    }

    public function testWp68FormatWrongPasswordFails(): void
    {
        $password = 'wp-member-password';

        $prehash = base64_encode(hash_hmac('sha384', $password, 'wp-sha384', true));
        $innerHash = password_hash($prehash, PASSWORD_BCRYPT);
        $wpHash = '$wp' . $innerHash;

        $this->assertFalse($this->hasher->check('wrong-password', $wpHash));
    }

    // ---------------------------------------------------------------
    // needsRehash()
    // ---------------------------------------------------------------

    public function testNeedsRehashReturnsTrueForWpPrefixedHash(): void
    {
        // Any hash starting with $wp should trigger rehash
        $wpHash = '$wp$2y$10$somefakehashcontenthere1234567890abc';

        $this->assertTrue($this->hasher->needsRehash($wpHash));
    }

    public function testNeedsRehashReturnsFalseForStandardBcrypt(): void
    {
        // Standard bcrypt at the hasher's default cost (12)
        $hash = password_hash('test', PASSWORD_BCRYPT, ['cost' => 12]);

        $this->assertFalse($this->hasher->needsRehash($hash));
    }

    // ---------------------------------------------------------------
    // make()
    // ---------------------------------------------------------------

    public function testMakeProducesVerifiableBcryptHash(): void
    {
        $password = 'make-test-password';
        $hash = $this->hasher->make($password);

        // Must be verifiable with PHP's built-in password_verify
        $this->assertTrue(password_verify($password, $hash));

        // Must be standard bcrypt (no $wp prefix)
        $this->assertStringStartsWith('$2y$', $hash);
    }

    // ---------------------------------------------------------------
    // Edge cases: empty inputs
    // ---------------------------------------------------------------

    public function testEmptyPasswordAlwaysFails(): void
    {
        $hash = password_hash('some-password', PASSWORD_BCRYPT);

        // Empty string should not verify against any real hash
        $this->assertFalse($this->hasher->check('', $hash));
    }

    public function testEmptyHashAlwaysFails(): void
    {
        // Empty hash triggers the early return in check()
        $this->assertFalse($this->hasher->check('any-password', ''));
    }
}
