<?php

namespace APP\plugins\generic\wpojsSubscriptionApi;

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

class WpojsApiLogMigration extends Migration
{
    public function up(): void
    {
        if (Schema::hasTable('wpojs_api_log')) {
            // Add duration_ms column if missing (existing installs).
            if (!Schema::hasColumn('wpojs_api_log', 'duration_ms')) {
                Schema::table('wpojs_api_log', function (Blueprint $table) {
                    $table->unsignedInteger('duration_ms')->nullable()->after('http_status');
                });
            }
            // Add error_detail and request_id columns if missing (existing installs).
            if (!Schema::hasColumn('wpojs_api_log', 'error_detail')) {
                Schema::table('wpojs_api_log', function (Blueprint $table) {
                    $table->string('error_detail', 500)->nullable()->after('duration_ms');
                });
            }
            if (!Schema::hasColumn('wpojs_api_log', 'request_id')) {
                Schema::table('wpojs_api_log', function (Blueprint $table) {
                    $table->string('request_id', 36)->nullable()->after('error_detail');
                });
            }
            return;
        }

        Schema::create('wpojs_api_log', function (Blueprint $table) {
            $table->bigIncrements('log_id');
            $table->string('endpoint', 255);
            $table->string('method', 10);
            $table->string('source_ip', 45);
            $table->smallInteger('http_status');
            $table->unsignedInteger('duration_ms')->nullable();
            $table->string('error_detail', 500)->nullable();
            $table->string('request_id', 36)->nullable();
            $table->dateTime('created_at');
            $table->index('created_at', 'wpojs_api_log_created_at');
            $table->index(['created_at', 'duration_ms'], 'wpojs_api_log_load');
        });
    }

    public function down(): void
    {
        Schema::dropIfExists('wpojs_api_log');
    }
}
