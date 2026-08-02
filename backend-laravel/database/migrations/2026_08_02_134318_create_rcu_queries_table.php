<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

/**
 * Recognition requests and their outcomes (plan 2.1, 6.4).
 *
 * Every row where `chosen_record_id` is set is a labelled training pair, and
 * every row where `none_of_these` is set is a harder and more informative one.
 * This is the dataset phase 9's trained detector is built from, so it is wired
 * in from day one rather than added when it is wanted.
 */
return new class extends Migration
{
    public function up(): void
    {
        Schema::create('rcu_queries', function (Blueprint $table) {
            $table->id();

            // The service mints this itself -- 16 hex chars, not a UUID, so
            // the plan's CHAR(36) would be wrong. Stored as returned, and it
            // is what GET /debug/{request_id} is keyed on.
            $table->string('request_id', 64)->unique();

            $table->string('upload_path', 512);

            // Full top-k with per-term score breakdown, exactly as returned.
            $table->json('candidates')->nullable();
            // What the service extracted from the query photo: brand, model
            // code, button count, quality, orientation confidence. Kept
            // because a bad match is usually a bad extraction, and without
            // this the evidence is gone by the time anyone looks.
            $table->json('extracted')->nullable();

            $table->float('top_score')->nullable();
            $table->string('top_record_id', 191)->nullable()->index();
            $table->enum('confidence', ['high', 'medium', 'low', 'none'])
                ->default('none');
            $table->string('hint', 64)->nullable();

            // Ground truth from the picker. A record_id string rather than a
            // model_id, because that is what the user is shown and what the
            // service returns; resolve to a model through rcu_fingerprints.
            $table->string('chosen_record_id', 191)->nullable()->index();
            // "None of these" -- the most informative signal of all, and
            // indistinguishable from "not answered yet" unless recorded.
            $table->boolean('none_of_these')->default(false);
            $table->timestamp('answered_at')->nullable();

            $table->unsignedInteger('latency_ms')->nullable();
            $table->unsignedSmallInteger('bodies_found')->nullable();
            $table->boolean('model_code_fast_path')->default(false);

            $table->timestamps();

            $table->index('created_at');
            $table->index('confidence');
        });
    }

    public function down(): void
    {
        Schema::dropIfExists('rcu_queries');
    }
};
