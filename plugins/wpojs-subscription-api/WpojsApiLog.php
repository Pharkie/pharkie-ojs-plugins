<?php

namespace APP\plugins\generic\wpojsSubscriptionApi;

use Illuminate\Support\Facades\DB;
use PKP\core\Core;

class WpojsApiLog
{
    /**
     * Log an API request.
     */
    public static function log(string $endpoint, string $method, string $sourceIp, int $httpStatus, ?int $durationMs = null): void
    {
        try {
            DB::table('wpojs_api_log')->insert([
                'endpoint'    => substr($endpoint, 0, 255),
                'method'      => substr($method, 0, 10),
                'source_ip'   => substr($sourceIp, 0, 45),
                'http_status' => $httpStatus,
                'duration_ms' => $durationMs,
                'created_at'  => Core::getCurrentDate(),
            ]);
        } catch (\Exception $e) {
            // Logging should never break the API, but record the failure.
            error_log('[wpojs-api-log] Failed to log request: ' . $e->getMessage());
        }
    }

    /**
     * Get average response time of recent successful requests.
     *
     * @param int $sampleSize Max number of recent requests to average.
     * @param int $windowSecs Only consider requests within this many seconds.
     * @return array ['avg_ms' => float|null, 'sample_count' => int]
     */
    public static function getAverageResponseTime(int $sampleSize = 20, int $windowSecs = 60): array
    {
        try {
            $cutoff = date('Y-m-d H:i:s', time() - $windowSecs);

            $rows = DB::table('wpojs_api_log')
                ->where('created_at', '>=', $cutoff)
                ->whereNotNull('duration_ms')
                ->where('http_status', '<', 400)
                ->orderBy('created_at', 'desc')
                ->limit($sampleSize)
                ->pluck('duration_ms');

            $count = $rows->count();
            if ($count === 0) {
                return ['avg_ms' => null, 'sample_count' => 0];
            }

            return [
                'avg_ms' => round($rows->avg(), 1),
                'sample_count' => $count,
            ];
        } catch (\Exception $e) {
            return ['avg_ms' => null, 'sample_count' => 0];
        }
    }

    /**
     * Get recent log entries.
     *
     * @param int $limit
     * @return array
     */
    public static function getRecent(int $limit = 50): array
    {
        try {
            return DB::table('wpojs_api_log')
                ->orderBy('created_at', 'desc')
                ->limit($limit)
                ->get()
                ->toArray();
        } catch (\Exception $e) {
            return [];
        }
    }

    /**
     * Delete entries older than N days.
     *
     * @param int $days
     * @return int Number of rows deleted.
     */
    public static function cleanup(int $days = 30): int
    {
        try {
            $cutoff = date('Y-m-d H:i:s', strtotime("-{$days} days"));
            return DB::table('wpojs_api_log')
                ->where('created_at', '<', $cutoff)
                ->delete();
        } catch (\Exception $e) {
            error_log('[wpojs-api-log] Cleanup failed: ' . $e->getMessage());
            return 0;
        }
    }
}
