<?php

namespace APP\plugins\generic\wpojsSubscriptionApi\Tests;

use PHPUnit\Framework\TestCase;

/**
 * Tests for the CIDR IP matching algorithm used in WpojsApiController::checkIp().
 *
 * Since checkIp() is a private method that depends on the Request object and OJS
 * framework (Config class), we replicate the matching algorithm here as a static
 * helper and test it in isolation. The logic is copied verbatim from checkIp().
 */
class CidrMatchTest extends TestCase
{
    /**
     * Replicate the IP matching logic from WpojsApiController::checkIp().
     *
     * @param string $clientIp The IP address to check
     * @param string $allowedIps Comma-separated list of IPs/CIDRs
     * @return bool Whether the client IP matches any entry in the allowlist
     */
    private static function ipMatches(string $clientIp, string $allowedIps): bool
    {
        if (empty($allowedIps)) {
            return false;
        }

        $allowed = array_map('trim', explode(',', $allowedIps));
        $clientLong = ip2long($clientIp);

        $matched = false;
        foreach ($allowed as $entry) {
            if (str_contains($entry, '/')) {
                // CIDR notation — IPv4 only
                if ($clientLong === false) {
                    continue;
                }
                [$subnet, $bits] = explode('/', $entry, 2);
                $bits = (int) $bits;
                if ($bits < 0 || $bits > 32) {
                    continue; // invalid CIDR prefix length
                }
                $subnetLong = ip2long($subnet);
                $mask = -1 << (32 - $bits);
                if (
                    $subnetLong !== false
                    && ($clientLong & $mask) === ($subnetLong & $mask)
                ) {
                    $matched = true;
                    break;
                }
            } elseif ($entry === $clientIp) {
                // Exact string match — works for both IPv4 and IPv6
                $matched = true;
                break;
            }
        }

        return $matched;
    }

    // ---------------------------------------------------------------
    // Exact IPv4 matching
    // ---------------------------------------------------------------

    public function testExactIpv4Match(): void
    {
        $this->assertTrue(self::ipMatches('192.168.1.1', '192.168.1.1'));
    }

    public function testExactIpv4NoMatch(): void
    {
        $this->assertFalse(self::ipMatches('192.168.1.1', '192.168.1.2'));
    }

    // ---------------------------------------------------------------
    // CIDR matching
    // ---------------------------------------------------------------

    public function testCidr24Match(): void
    {
        $this->assertTrue(self::ipMatches('192.168.1.100', '192.168.1.0/24'));
    }

    public function testCidr24NoMatch(): void
    {
        $this->assertFalse(self::ipMatches('192.168.2.1', '192.168.1.0/24'));
    }

    public function testCidr8Match(): void
    {
        $this->assertTrue(self::ipMatches('172.18.5.3', '172.0.0.0/8'));
    }

    public function testCidr32ExactMatch(): void
    {
        $this->assertTrue(self::ipMatches('10.0.0.1', '10.0.0.1/32'));
    }

    // ---------------------------------------------------------------
    // IPv6 matching (exact only — CIDR not supported)
    // ---------------------------------------------------------------

    public function testIpv6ExactMatch(): void
    {
        $this->assertTrue(self::ipMatches('::1', '::1'));
    }

    public function testIpv6NoMatch(): void
    {
        $this->assertFalse(self::ipMatches('::1', '::2'));
    }

    // ---------------------------------------------------------------
    // Edge cases
    // ---------------------------------------------------------------

    public function testEmptyAllowlistDeniesAll(): void
    {
        $this->assertFalse(self::ipMatches('192.168.1.1', ''));
    }

    public function testMultipleEntriesMatchesAny(): void
    {
        $this->assertTrue(
            self::ipMatches('10.0.0.5', '192.168.1.0/24, 10.0.0.0/8, 172.16.0.1')
        );
    }

    public function testMultipleEntriesNoMatch(): void
    {
        $this->assertFalse(
            self::ipMatches('8.8.8.8', '192.168.1.0/24, 10.0.0.0/8, 172.16.0.1')
        );
    }

    public function testInvalidCidrPrefixSkipped(): void
    {
        // /33 is invalid for IPv4 — should be silently skipped, not crash
        $this->assertFalse(self::ipMatches('192.168.1.1', '192.168.1.0/33'));
    }

    public function testNegativeCidrPrefixSkipped(): void
    {
        $this->assertFalse(self::ipMatches('192.168.1.1', '192.168.1.0/-1'));
    }
}
