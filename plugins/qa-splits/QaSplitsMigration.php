<?php

namespace APP\plugins\generic\qaSplits;

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

class QaSplitsMigration extends Migration
{
    public function up(): void
    {
        if (Schema::hasTable('qa_split_reviews')) {
            return;
        }

        Schema::create('qa_split_reviews', function (Blueprint $table) {
            $table->bigIncrements('review_id');
            $table->unsignedBigInteger('submission_id');
            $table->unsignedBigInteger('publication_id');
            $table->unsignedBigInteger('user_id');
            $table->string('username', 255);
            $table->enum('decision', ['approved', 'needs_fix']);
            $table->text('comment')->nullable();
            $table->string('content_hash', 64)->nullable();
            $table->dateTime('created_at');
            $table->index('submission_id', 'qa_sr_submission');
            $table->index('decision', 'qa_sr_decision');
        });
    }

    public function down(): void
    {
        Schema::dropIfExists('qa_split_reviews');
    }
}
