<?php

namespace App\Console\Commands;

use Illuminate\Console\Command;
use Illuminate\Support\Facades\DB;
use Symfony\Component\Console\Output\OutputInterface;

/**
 * Write `record_id <TAB> title` for every catalog record that has a title.
 *
 * The catalogue knows each remote's model code -- it is printed at the head of
 * the title, before the Russian description -- and the extractor has to read
 * it off a photograph that is often a 289x1057 imagecache derivative. On the
 * one measured record the catalogue says `BN59-01315B` and OCR of its own
 * photograph says `BN59-013158`, which is the difference between the exact
 * model-code bonus and the fuzzy one.
 *
 * So the codes come from here. This command deliberately exports *titles* and
 * not codes: the grammar of a model code is `MODEL_RE` in the Python tree, and
 * the extractor and this path must never disagree about what one looks like.
 * A second regex in PHP would be a second definition, and the two would drift.
 * `scripts/apply_catalog_codes.py` does the parsing.
 *
 *     php artisan rcu:export-titles --out=- > work/titles.tsv
 *     docker compose --profile build run --rm --entrypoint python extract \
 *         scripts/apply_catalog_codes.py --fp /data/work/fp --titles /data/work/titles.tsv
 *
 * stdout, because the Laravel container mounts work/ read-only. Diagnostics go
 * to stderr so the two can be separated by redirection -- and note that
 * getErrorStyle() silently falls back to stdout when there is no real console,
 * so that split cannot be asserted with $this->artisan().
 */
class ExportTitlesCommand extends Command
{
    protected $signature = 'rcu:export-titles
        {--out= : Where to write, or "-" for stdout (default <fp_dir>/../titles.tsv)}';

    protected $description = 'Export catalog titles so the build can take model codes from them';

    public function handle(): int
    {
        $out = $this->option('out')
            ?: dirname(rtrim(config('rcu.catalog.fp_dir'), '/')) . '/titles.tsv';

        $rows = DB::table('rcu_fingerprints')
            ->whereNotNull('title')
            ->where('title', '!=', '')
            ->orderBy('record_id')
            ->get(['record_id', 'title']);

        $lines = [];
        foreach ($rows as $r) {
            // A tab or newline inside a title would split the record into two
            // fields or two rows further down the pipe. Neither has been seen,
            // which is exactly why it is worth collapsing rather than trusting.
            $title = preg_replace('/\s+/u', ' ', trim($r->title));
            $lines[] = $r->record_id . "\t" . $title;
        }

        $body = implode("\n", $lines) . (count($lines) ? "\n" : '');

        if ($out === '-') {
            $this->output->write($body, false, OutputInterface::OUTPUT_RAW);
            $this->getErrorStyle()->writeln(count($lines) . ' title(s)');

            return self::SUCCESS;
        }

        file_put_contents($out, $body);
        $this->info(count($lines) . " title(s) -> {$out}");

        return self::SUCCESS;
    }
}
