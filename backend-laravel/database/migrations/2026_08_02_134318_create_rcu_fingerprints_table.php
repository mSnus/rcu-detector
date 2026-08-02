<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

/**
 * One row per physical remote crop. A catalog image may yield several: two
 * remotes photographed side by side, or a remote plus the same remote inside
 * its blister pack.
 *
 * Plan 2.1, with one addition the plan does not have. The Python service
 * identifies catalog records by `record_id` -- the fingerprint stem, e.g.
 * "Sony_RM-PJ20_big_0" -- and that string is what comes back in every
 * candidate. Without it stored here there is no way to resolve a match to a
 * row, so it is unique and indexed rather than derived.
 */
return new class extends Migration
{
    public function up(): void
    {
        Schema::create('rcu_fingerprints', function (Blueprint $table) {
            $table->id();

            // The service's identifier for this record. Matches the .json stem
            // under work/fp and the `record_id` in every /identify candidate.
            $table->string('record_id', 191)->unique();

            // FK to the existing catalog models table, which this project does
            // not own and must not alter. Left unconstrained on purpose: the
            // table may not exist yet, and a hard FK would couple the catalog
            // rebuild to it.
            $table->unsignedBigInteger('model_id')->nullable()->index();

            $table->string('source_image', 512);
            $table->unsignedTinyInteger('crop_index')->default(0);
            $table->string('norm_path', 512);

            $table->float('aspect_ratio');
            $table->unsignedSmallInteger('button_count');
            $table->json('fingerprint');

            $table->string('brand_text', 128)->nullable();
            $table->string('model_text', 128)->nullable();

            $table->float('quality_score')->default(0);
            $table->boolean('reviewed')->default(false);

            // Orientation is stored because getting it wrong silently corrupts
            // the fingerprint. A record flagged ambiguous here is one whose
            // matches should be read with suspicion.
            $table->boolean('orientation_flipped')->default(false);
            $table->float('orientation_conf')->default(0);

            $table->timestamp('built_at')->useCurrent();
            $table->timestamps();

            $table->index('quality_score');
            $table->index('model_text');
            $table->index('brand_text');
            // The review queue reads "worst first, unreviewed only".
            $table->index(['reviewed', 'quality_score']);
        });
    }

    public function down(): void
    {
        Schema::dropIfExists('rcu_fingerprints');
    }
};
