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

    /**
     * Collapse photographs whose file contents are byte-identical.
     *
     * One physical remote is routinely listed as many products, one per TV
     * brand whose code set it carries, and every listing points at the same
     * photograph. Measured over the live catalogue: 13763 photographs are
     * 13174 distinct images, and the redundancy is concentrated --
     *
     *     120  one IRC universal, listed under 120 IRC model numbers
     *      23  AN1603, for Novex / Asano / Centek / Hartens / Accesstyle ...
     *      20  a second IRC group
     *      19  Sber SBDV-00001, for Sber / Olto / Prestigio / SUNWIND ...
     *
     * That is correct catalogue data, not a defect, and it must not be
     * "cleaned up" in the database. But extracting all of them puts 120
     * identical fingerprints in the index, and identical inputs extract
     * identically -- so a query against that remote is an exact 120-way tie
     * that no amount of detector work can break, and 119 correct answers are
     * scored as errors by any measurement keyed on the model.
     *
     * The canonical member is the alphabetically first path, and alphabetical
     * is the right rule *because* the members are byte-identical: there is no
     * better-quality member to prefer, so the only thing that matters is that
     * the choice is stable between builds. It has to be -- the record keys to
     * its photograph's stem, so a canonical that moved would silently
     * re-point the record at a different product.
     *
     * Costs one md5 per photograph, ~820 MB of reads on the live catalogue.
     * Paid once per build, which is the only time the manifest is produced.
     *
     * @param  list<string>  $relPaths  paths relative to $filesDir
     * @return array{keep: list<string>, duplicates: array<string, string>}
     *         `duplicates` maps each dropped path to the one kept in its place
     */
    public static function canonicalByContent(array $relPaths, string $filesDir): array
    {
        $filesDir = rtrim($filesDir, '/');
        sort($relPaths);

        $canonical = [];
        $keep = [];
        $duplicates = [];

        foreach ($relPaths as $rel) {
            $hash = @md5_file($filesDir . '/' . $rel);

            if ($hash === false) {
                // Unreadable here is not a duplicate. Keep it and let the
                // extractor report it as unreadable, which it counts; deciding
                // that here would hide the failure behind the wrong reason.
                $keep[] = $rel;
                continue;
            }

            if (isset($canonical[$hash])) {
                $duplicates[$rel] = $canonical[$hash];
                continue;
            }

            $canonical[$hash] = $rel;
            $keep[] = $rel;
        }

        return ['keep' => $keep, 'duplicates' => $duplicates];
    }
}
