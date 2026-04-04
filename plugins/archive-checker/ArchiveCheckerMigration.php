<?php

namespace APP\plugins\generic\archiveChecker;

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

class ArchiveCheckerMigration extends Migration
{
    public function up(): void
    {
        if (Schema::hasTable('archive_checker_reviews')) {
            return;
        }

        Schema::create('archive_checker_reviews', function (Blueprint $table) {
            $table->bigIncrements('review_id');
            $table->unsignedBigInteger('submission_id');
            // publication_id is audit-only — goes stale after reimport.
            // NEVER join on this for lookups; use submissions.current_publication_id instead.
            $table->unsignedBigInteger('publication_id');
            $table->unsignedBigInteger('user_id');
            $table->string('username', 255);
            $table->enum('decision', ['approved', 'needs_fix', 'recheck', 'deferred']);
            $table->text('comment')->nullable();
            $table->string('content_hash', 64)->nullable();
            $table->dateTime('created_at');
            $table->index('submission_id', 'ac_submission');
            $table->index('decision', 'ac_decision');
        });
    }

    public function down(): void
    {
        Schema::dropIfExists('archive_checker_reviews');
    }
}
