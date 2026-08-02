<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

/**
 * The catalogue's own name for the record.
 *
 * Stored verbatim, exactly as the source database has it -- freeform Russian
 * like "#003 пульт для телевизора Рубин 55SM10-4 и других". Deliberately not
 * parsed into brand and model: the titles follow no single grammar, and a
 * heuristic split would manufacture a brand the catalogue never claimed.
 *
 * `brand_text` and `model_text` are left alone, holding what the extractor
 * READ OFF THE IMAGE. Keeping the two apart is the point -- it is what lets
 * OCR be measured against the catalogue rather than quietly replaced by it.
 *
 * The link back to the item page is not stored. `model_id` holds the source
 * node id and the URL is a config template (`rcu.catalog.item_url`), so a
 * domain change is a config edit rather than a rewrite of every row.
 */
return new class extends Migration
{
    public function up(): void
    {
        Schema::table('rcu_fingerprints', function (Blueprint $table) {
            $table->string('title', 255)->nullable()->after('model_text');
            // Operators look a record up by the name they know it under.
            $table->index('title');
        });
    }

    public function down(): void
    {
        Schema::table('rcu_fingerprints', function (Blueprint $table) {
            $table->dropIndex(['title']);
            $table->dropColumn('title');
        });
    }
};
