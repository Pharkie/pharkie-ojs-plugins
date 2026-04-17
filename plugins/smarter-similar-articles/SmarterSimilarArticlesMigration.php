<?php

namespace APP\plugins\generic\smarterSimilarArticles;

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

class SmarterSimilarArticlesMigration extends Migration
{
    public function up(): void
    {
        if (Schema::hasTable('smarter_similar_articles')) {
            return;
        }

        Schema::create('smarter_similar_articles', function (Blueprint $table) {
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
        Schema::dropIfExists('smarter_similar_articles');
    }
}
