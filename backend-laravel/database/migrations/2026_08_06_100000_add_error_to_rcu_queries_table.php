<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

/**
 * Distinguish "we looked and found nothing" from "we never got to look".
 *
 * The query row is written before the service is called, so that an upload is
 * recorded even if everything after it fails. When the call then failed, the
 * row was simply left as created: `confidence` at its default of `none`, and
 * top_score, latency_ms and bodies_found all NULL. In the admin list and in
 * any metric over this table, that is indistinguishable from a genuine
 * no-match.
 *
 * It is not a hypothetical. Query 602dac4b arrived seven seconds after the
 * service restarted, while it was still loading 12754 fingerprints, and was
 * filed as a remote nothing matched.
 *
 * This matters more than one row: plan 10.1 makes acceptance rate over
 * rcu_queries the single best health metric, and an outage counted as a failed
 * match moves that metric in the same direction as a real regression. The two
 * have opposite fixes.
 */
return new class extends Migration
{
    public function up(): void
    {
        Schema::table('rcu_queries', function (Blueprint $table) {
            // The error code the API returned -- `recognition_unavailable`,
            // `image_rejected`. NULL means the service answered.
            $table->string('error', 64)->nullable()->after('confidence');
            $table->index('error');
        });
    }

    public function down(): void
    {
        Schema::table('rcu_queries', function (Blueprint $table) {
            $table->dropIndex(['error']);
            $table->dropColumn('error');
        });
    }
};
