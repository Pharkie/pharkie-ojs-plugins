<?php

namespace APP\plugins\generic\similarArticles;

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

class SimilarArticlesMigration extends Migration
{
    public function up(): void
    {
        if (Schema::hasTable('similar_articles')) {
            return;
        }

        Schema::create('similar_articles', function (Blueprint $table) {
            $table->unsignedBigInteger('submission_id');
            $table->unsignedBigInteger('similar_id');
            $table->unsignedTinyInteger('rank');
            $table->decimal('score', 5, 4);
            $table->timestamp('computed_at')->useCurrent();
            $table->primary(['submission_id', 'rank']);
            $table->index('similar_id', 'sa_similar');
        });
    }

    public function down(): void
    {
        Schema::dropIfExists('similar_articles');
    }
}
