<?php

namespace App\Support;

use Illuminate\Support\Collection;
use Illuminate\Support\Facades\DB;

/**
 * The one definition of "a product's own photograph" in the legacy catalogue.
 *
 * Two consumers need it and must never disagree: `rcu:legacy-manifest`, which
 * decides what gets extracted, and `rcu:import-catalog --legacy`, which
 * decides what metadata a fingerprint gets. A build that extracts a wider set
 * than the import can key produces records with no title and no model_id, and
 * they are indistinguishable from a genuine metadata miss.
 *
 * Two rules, both measured on the real data:
 *
 *  - the product's own photo is the `delta = 0` row of
 *    content_field_image_cache. Higher deltas are replacement-model promos
 *    (Zamena_*) and instruction sheets -- not remotes, and poison the index
 *    if extracted as if they were. On the 100-product sample that is 86
 *    further images against 100 real ones.
 *  - the file on disk is `basename(files.filepath)`, NEVER `files.filename`.
 *    Drupal appends _NN on collision, so the two differ on 53 of 186 rows and
 *    `filename` is not even unique -- two different remotes are both called
 *    IRC_new.jpg.
 */
class LegacyCatalog
{
    /**
     * Every product's delta=0 photograph, as {nid, title, basename}.
     *
     * Rows are returned as they come: a nid appearing twice at delta 0 is a
     * duplicate row rather than an error, and a basename naming two products
     * is a real ambiguity that only the caller can decide what to do about.
     *
     * @return Collection<int, object{nid: int, title: string, basename: string}>
     */
    public static function primaryPhotos(): Collection
    {
        return DB::connection('legacy')
            ->table('node as n')
            ->join('content_field_image_cache as c', function ($j) {
                $j->on('c.nid', '=', 'n.nid')->where('c.delta', '=', 0);
            })
            ->join('files as f', 'f.fid', '=', 'c.field_image_cache_fid')
            ->where('n.type', 'product')
            ->select('n.nid', 'n.title', 'f.filepath')
            ->get()
            ->map(fn ($r) => (object) [
                'nid' => (int) $r->nid,
                'title' => (string) $r->title,
                'basename' => basename((string) $r->filepath),
            ]);
    }
}
