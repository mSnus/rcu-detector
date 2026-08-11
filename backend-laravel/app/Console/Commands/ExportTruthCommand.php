<?php

namespace App\Console\Commands;

use Illuminate\Console\Command;
use Illuminate\Support\Facades\DB;
use Symfony\Component\Console\Output\OutputInterface;

/**
 * Write `record_id <TAB> model_id` for every catalog record that has one.
 *
 * This exists because the offline evaluators had no way to know what a
 * *correct* answer is, and quietly used the filename instead.
 *
 * `query_drift.py` and `calibrate_bands.py` key truth on the photo stem: a
 * query made from `X.jpg` is correct only if it returns a record extracted
 * from `X.jpg`. That is true but far from complete, and it under-reports:
 *
 *   - the same remote is often catalogued twice under two filenames, so
 *     `RS41C0` answering `RS41C0_1_0` scores as a miss. Ten of the thirteen
 *     "wrong" medium answers in the session-7 calibration were this, which is
 *     why medium read 78% when it was nearer 95%;
 *   - one physical remote is listed once per TV brand whose codes it carries,
 *     so a whole group of model_ids share one appearance and any of them is
 *     an equally correct answer.
 *
 * A model_id is the catalogue's own statement that two records are the same
 * product, so it is the honest key. Not the only one -- records sharing a
 * source image are also all correct, and `rcu:legacy-manifest` now collapses
 * those before they reach the catalogue, which is what makes model_id
 * sufficient here rather than merely better.
 *
 *     php artisan rcu:export-truth --out=- > work/truth.tsv
 *     python scripts/calibrate_bands.py --truth ../work/truth.tsv ...
 */
class ExportTruthCommand extends Command
{
    protected $signature = 'rcu:export-truth
        {--out= : Where to write, or "-" for stdout (default <fp_dir>/../truth.tsv)}';

    protected $description = 'Export record_id -> model_id, so evaluators can tell a correct answer from a coincidence';

    public function handle(): int
    {
        $out = $this->option('out')
            ?: dirname(rtrim(config('rcu.catalog.fp_dir'), '/')) . '/truth.tsv';

        $rows = DB::table('rcu_fingerprints')
            ->whereNotNull('model_id')
            ->orderBy('record_id')
            ->get(['record_id', 'model_id']);

        $lines = [];
        foreach ($rows as $r) {
            $lines[] = $r->record_id . "\t" . $r->model_id;
        }

        $body = implode("\n", $lines) . (count($lines) ? "\n" : '');
        $groups = count(array_unique(array_map(
            fn ($r) => $r->model_id, $rows->all())));

        if ($out === '-') {
            // Body to stdout, commentary to stderr, so `--out=- > file` gets a
            // clean file. See ExportTitlesCommand for what happens otherwise.
            $this->getOutput()->write($body, false, OutputInterface::OUTPUT_RAW);
            $this->getOutput()->getErrorStyle()->writeln(
                count($lines) . " record(s) in {$groups} product group(s)");

            return self::SUCCESS;
        }

        file_put_contents($out, $body);
        $this->info(count($lines) . " record(s) in {$groups} product group(s) -> {$out}");

        return self::SUCCESS;
    }
}
