<?php

/**
 * Minimal bootstrap for PHPUnit tests.
 *
 * The WpCompatibleHasher only depends on PHP built-in functions
 * (password_hash, password_verify, hash_hmac), so no OJS framework needed.
 *
 * We just need the Hasher interface stub and the class under test.
 */

// Stub the Laravel Hasher interface so WpCompatibleHasher can implement it
// without requiring the full Illuminate framework.
if (!interface_exists('Illuminate\Contracts\Hashing\Hasher')) {
    require_once __DIR__ . '/stubs/HasherInterface.php';
}

// Load the class under test
require_once dirname(__DIR__) . '/WpCompatibleHasher.php';
