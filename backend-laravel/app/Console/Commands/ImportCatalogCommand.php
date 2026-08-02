<?php

namespace App\Console\Commands;

use App\Models\RcuFingerprint;
use App\Services\RcuService;
use App\Services\RcuServiceException;
use Illuminate\Console\Command;
use Illuminate\Support\Facades\DB;

/**
 * Load the fingerprints produced by the offline extraction run into MySQL.
 *
 * The Python service is the only thing that reads `work/index/tokens.npz` and
 * matches against it; this table exists so a `record_id` coming back from
 * /identify can be resolved to something a human or an API client can read.
 * The two must be built from the same run -- an index newer than this table
 * returns record_ids that resolve to nothing, and an older one silently
 * matches against remotes the catalog no longer lists.
 *
 * Usage:
 *
 *     php artisan rcu:import-catalog
 *     php artisan rcu:import-catalog --prune --reindex
 *     php artisan rcu:import-catalog --dry-run
 */
class ImportCatalogCommand extends Command
{
    protected $signature = 'rcu:import-catalog
        {--fp= : Directory of fingerprint JSON (default config rcu.catalog.fp_dir)}
        {--photos= : Directory of source photographs}
        {--norm= : Directory of rectified crops}
        {--legacy : Take metadata from the legacy catalogue DB, keyed on nid}
        {--prune : Delete rows whose fingerprint file is gone}
        {--reindex : Ask the service to reload its token index afterwards}
        {--dry-run : Report what would change and write nothing}';

    protected $description = 'Import extracted fingerprints into the catalog table';

    public function handle(RcuService $service): int
    {
        $fpDir = $this->dir('fp', 'rcu.catalog.fp_dir');
        $photoDir = $this->dir('photos', 'rcu.catalog.photo_dir');
        $normDir = $this->dir('norm', 'rcu.catalog.norm_dir');

        if (! is_dir($fpDir)) {
            $this->error("fingerprint directory not found: {$fpDir}");

            return self::FAILURE;
        }

        $files = glob(rtrim($fpDir, '/') . '/*.json') ?: [];

        if ($files === []) {
            $this->error("no fingerprints in {$fpDir} -- run scripts/extract_one.py first");

            return self::FAILURE;
        }

        $dry = (bool) $this->option('dry-run');
        $legacy = $this->option('legacy') ? $this->legacyMetadata() : [];

        if ($this->option('legacy')) {
            $this->info(count($legacy) . ' product(s) with a primary photo in the legacy catalogue');
        }

        $created = $updated = $failed = 0;
        $seen = [];
        $missingPhotos = [];
        $unmatched = [];

        foreach ($files as $file) {
            $recordId = pathinfo($file, PATHINFO_FILENAME);
            $seen[] = $recordId;

            $fp = json_decode((string) file_get_contents($file), true);

            if (! is_array($fp) || ! isset($fp['body'])) {
                $this->warn("  skipped {$recordId}: not a fingerprint document");
                $failed++;
                continue;
            }

            [$stem, $cropIndex] = $this->splitRecordId($recordId);

            $meta = null;

            if ($this->option('legacy')) {
                // The stem is the node id, so no filename guessing.
                $meta = $legacy[$stem] ?? null;
                if ($meta === null) {
                    $unmatched[] = $recordId;
                }
                $sourceImage = $meta['filepath'] ?? ($stem . '.jpg');
            } else {
                $sourceImage = $stem . '.jpg';

                if (! is_file(rtrim($photoDir, '/') . '/' . $sourceImage)) {
                    $missingPhotos[] = $recordId;
                }
            }

            $existing = RcuFingerprint::where('record_id', $recordId)->first();

            $model = RcuFingerprint::fromServiceFingerprint(
                $recordId, $fp, $sourceImage, $recordId . '.jpg', $cropIndex
            );

            // attributesToArray, not getAttributes: raw values re-encode
            // through the array cast and double-encode `fingerprint`.
            $attributes = $model->attributesToArray();
            $attributes['built_at'] = date('Y-m-d H:i:s', filemtime($file) ?: time());

            if ($meta !== null) {
                $attributes['model_id'] = $meta['nid'];
                $attributes['title'] = $meta['title'];
            }

            if ($existing) {
                /*
                 * `reviewed` and `model_id` are the only two columns a person
                 * sets rather than the extractor. A rebuild must not throw
                 * that away, or every catalog rebuild silently empties the
                 * review queue and unlinks the catalog joins.
                 */
                unset($attributes['reviewed']);

                // In legacy mode the catalogue owns model_id, not the operator.
                if ($meta === null) {
                    unset($attributes['model_id']);
                }

                if (! $dry) {
                    $existing->update($attributes);
                }
                $updated++;
            } else {
                if (! $dry) {
                    // Set extras individually; fromServiceFingerprint carries
                    // neither built_at nor the legacy metadata.
                    $model->built_at = $attributes['built_at'];
                    if ($meta !== null) {
                        $model->model_id = $meta['nid'];
                        $model->title = $meta['title'];
                    }
                    $model->save();
                }
                $created++;
            }
        }

        $pruned = 0;

        if ($this->option('prune')) {
            $stale = RcuFingerprint::whereNotIn('record_id', $seen)->pluck('record_id');
            $pruned = $stale->count();

            if ($pruned > 0 && ! $dry) {
                // Chunked: whereNotIn with a catalog-sized list is the kind of
                // query that works on 21 records and dies on 50k.
                RcuFingerprint::whereIn('record_id', $stale)->delete();
            }

            foreach ($stale as $recordId) {
                $this->line("  pruned {$recordId}");
            }
        }

        if ($unmatched !== []) {
            $this->warn(count($unmatched) . ' fingerprint(s) have no product row in the legacy DB:');
            foreach (array_slice($unmatched, 0, 10) as $recordId) {
                $this->line("  {$recordId}");
            }
        }

        $this->reportPhotoGaps($missingPhotos, $photoDir);
        $this->reportNormGaps($seen, $normDir);

        $verb = $dry ? 'would be' : '';
        $this->info(trim("{$created} created, {$updated} updated, {$pruned} pruned, "
            . "{$failed} failed {$verb}"));

        if ($dry) {
            $this->comment('dry run -- nothing written');

            return self::SUCCESS;
        }

        $this->warnOnIndexSkew($service, count($seen));

        if ($this->option('reindex')) {
            try {
                $result = $service->reindex();
                $this->info('service reindexed: ' . json_encode($result));
            } catch (RcuServiceException $e) {
                $this->error('reindex failed: ' . $e->getMessage());

                return self::FAILURE;
            }
        }

        return self::SUCCESS;
    }

    /**
     * Primary product photos from the legacy catalogue, keyed by node id.
     *
     * delta=0 is the product's own photo; higher deltas are replacement-model
     * promos and instruction sheets. The file on disk is basename(filepath) --
     * `filename` is not unique (two remotes share "IRC_new.jpg").
     *
     * @return array<string, array{nid: int, title: string, filepath: string}>
     */
    private function legacyMetadata(): array
    {
        $rows = DB::connection('legacy')
            ->table('node as n')
            ->join('content_field_image_cache as c', function ($j) {
                $j->on('c.nid', '=', 'n.nid')->where('c.delta', '=', 0);
            })
            ->join('files as f', 'f.fid', '=', 'c.field_image_cache_fid')
            ->where('n.type', 'product')
            ->select('n.nid', 'n.title', 'f.filepath')
            ->get();

        $out = [];
        foreach ($rows as $r) {
            $out[(string) $r->nid] = [
                'nid' => (int) $r->nid,
                'title' => $r->title,
                'filepath' => basename($r->filepath),
            ];
        }

        return $out;
    }

    /**
     * Split "Sony_RM-PJ20_big_0" into ["Sony_RM-PJ20_big", 0].
     *
     * extract_one.py names every output `<photo stem>_<crop index>`, always,
     * even when the photo yielded exactly one crop. So the trailing group is
     * removed unconditionally -- and in particular this must never be
     * short-circuited by testing whether `<record_id>.jpg` exists in the photo
     * directory. It sometimes does and means something else: this catalog
     * holds both `ROLSEN_RSF-3106RT.jpg` and `ROLSEN_RSF-3106RT_0.jpg`, two
     * different remotes, whose records are `ROLSEN_RSF-3106RT_0` and
     * `ROLSEN_RSF-3106RT_0_0`. The existence test resolves the first of those
     * to the second's photograph.
     *
     * @return array{0: string, 1: int}
     */
    private function splitRecordId(string $recordId): array
    {
        if (preg_match('/^(.*)_(\d+)$/', $recordId, $m) === 1) {
            return [$m[1], (int) $m[2]];
        }

        // No trailing index at all: not something extract_one.py produces, so
        // keep the whole string rather than inventing a stem for it.
        return [$recordId, 0];
    }

    private function dir(string $option, string $configKey): string
    {
        return rtrim($this->option($option) ?: config($configKey), '/');
    }

    /**
     * A record whose source photograph is missing still matches perfectly --
     * the fingerprint is self-contained. Only the admin visualiser suffers,
     * so this is a warning and never a failure.
     */
    private function reportPhotoGaps(array $missing, string $photoDir): void
    {
        if ($missing === []) {
            return;
        }

        $this->warn(count($missing) . " record(s) have no source photo in {$photoDir}:");

        foreach (array_slice($missing, 0, 10) as $recordId) {
            $this->line("  {$recordId}");
        }
    }

    private function reportNormGaps(array $recordIds, string $normDir): void
    {
        $missing = array_values(array_filter(
            $recordIds,
            fn (string $id) => ! is_file($normDir . '/' . $id . '.jpg')
        ));

        if ($missing === []) {
            return;
        }

        $this->warn(count($missing) . " record(s) have no rectified crop in {$normDir}");
    }

    /**
     * The table and the service's index are built from the same extraction run
     * and must agree. When they do not, matching still "works" and returns
     * record_ids that resolve to nothing, which reads as a database bug.
     */
    private function warnOnIndexSkew(RcuService $service, int $imported): void
    {
        $health = $service->health();

        if (($health['status'] ?? null) !== 'ok') {
            $this->comment('service not reachable -- index skew not checked');

            return;
        }

        $inIndex = (int) ($health['index_records'] ?? 0);

        if ($inIndex !== $imported) {
            $this->warn(
                "index holds {$inIndex} record(s), catalog now holds {$imported}. "
                . 'Rebuild the index (scripts/build_index.py) and pass --reindex.'
            );
        }
    }
}
