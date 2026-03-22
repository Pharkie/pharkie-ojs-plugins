<?php

namespace Illuminate\Contracts\Hashing;

/**
 * Stub of Laravel's Hasher interface for unit testing.
 * Avoids requiring the full Illuminate framework.
 */
interface Hasher
{
    public function make(#[\SensitiveParameter] $value, array $options = []): string;
    public function check(#[\SensitiveParameter] $value, $hashedValue, array $options = []): bool;
    public function needsRehash($hashedValue, array $options = []): bool;
    public function info($hashedValue): array;
}
