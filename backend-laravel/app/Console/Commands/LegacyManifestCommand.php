<?php

namespace App\Console\Commands;

use App\Support\LegacyCatalog;
use Illuminate\Console\Command;
use Symfony\Component\Console\Output\OutputInterface;

/**
 * Write the list of photographs the catalog build should extract.
 *
 * The extraction container has no database: it is handed a directory and
 * fingerprints everything in it. But the legacy `files/` directory is not a
 * directory of remotes -- roughly a third of it is replacement-model promos
 * (Zamena_*) and instruction sheets hung off the same products at delta >= 1.
 * Extracting those indexes them as if they were remotes, so an instruction
 * sheet can be returned as a match, and every one of them imports as a record
 * with no title and no model_id because nothing in the catalogue keys it.
 *
 * So the database decides what to extract, here, and the build reads the
 * answer from a file:
 *
 *     php artisan rcu:legacy-manifest --out=/data/work/primary.txt
 *     docker compose --profile build run --rm extract --manifest /data/work/primary.txt
 *
 * Measured on the 100-product sample: 100 primary photographs against 86
 * further images that are not remotes.
 */
class LegacyManifestCommand extends Command
{
    protected $signature = 'rcu:legacy-manifest
        {--out= : Where to write the list, or "-" for stdout (default <fp_dir>/../primary.txt)}
        {--files= : Directory the photographs live in (default config rcu.catalog.files_dir)}';

    protected $description = 'List the legacy catalogue photographs worth extracting';

    public function handle(): int
    {
        $filesDir = rtrim($this->option('files') ?: config('rcu.catalog.files_dir'), '/');
        $out = $this->option('out')
            ?: dirname(rtrim(config('rcu.catalog.fp_dir'), '/')) . '/primary.txt';

        $rows = LegacyCatalog::primaryPhotos();

        // A basename naming two products is still one photograph to extract.
        // The ambiguity is a metadata problem, and rcu:import-catalog reports
        // it; dropping the image here would lose a real remote as well.
        $names = $rows->pluck('basename')->unique()->sort()->values();

        $present = [];
        $missing = [];

        foreach ($names as $name) {
            if (is_file($filesDir . '/' . $name)) {
                $present[] = $name;
            } else {
                $missing[] = $name;
            }
        }

        $list = implode("\n", $present) . "\n";

        if ($out === '-') {
            // The Laravel container mounts work/ read-only on purpose -- it
            // reads build artefacts, it does not produce them. So the list
            // leaves over stdout and the operator redirects it, and every
            // diagnostic below goes to stderr to stay out of the file.
            $this->getOutput()->write($list, false, OutputInterface::OUTPUT_RAW);
        } elseif (@file_put_contents($out, $list) === false) {
            $this->error("cannot write {$out}"
                . ' -- pass --out=- and redirect if the directory is read-only');

            return self::FAILURE;
        }

        $where = $out === '-' ? 'stdout' : $out;
        $this->report('<info>' . count($present) . " photograph(s) written to {$where}</info>");

        if ($missing !== []) {
            $this->report('<comment>' . count($missing)
                . " listed photograph(s) are not in {$filesDir}:</comment>");

            foreach (array_slice($missing, 0, 10) as $name) {
                $this->report("  {$name}");
            }
        }

        $this->reportExcluded($filesDir, $present);

        return self::SUCCESS;
    }

    /**
     * What is on disk but not in the manifest.
     *
     * This is the number the command exists for, so it is always reported:
     * silently extracting a third more images than the catalogue can key is
     * exactly the failure this prevents, and it is invisible afterwards --
     * the extra records look like ordinary metadata misses.
     *
     * @param  list<string>  $present
     */
    private function reportExcluded(string $filesDir, array $present): void
    {
        $onDisk = glob($filesDir . '/*');

        if ($onDisk === false) {
            return;
        }

        $keep = array_flip($present);
        $excluded = [];

        foreach ($onDisk as $path) {
            if (is_file($path) && ! isset($keep[basename($path)])) {
                $excluded[] = basename($path);
            }
        }

        if ($excluded === []) {
            $this->report('nothing in ' . $filesDir . ' is excluded');

            return;
        }

        sort($excluded);

        $this->report('<comment>' . count($excluded) . ' file(s) in ' . $filesDir
            . ' are NOT product photographs and will not be extracted:</comment>');

        foreach (array_slice($excluded, 0, 10) as $name) {
            $this->report("  {$name}");
        }
    }

    /**
     * Diagnostics go to stderr, always -- so `--out=-` can be redirected into
     * a manifest without the commentary landing in it, and so the two modes
     * report identically.
     *
     * getErrorStyle() falls back to stdout when the output is not a real
     * console, which is what happens under `$this->artisan()`. So the
     * separation cannot be asserted in-process; it is verified by running the
     * command in the container, which DEPLOY records.
     */
    private function report(string $line): void
    {
        $this->getOutput()->getErrorStyle()->writeln($line);
    }
}
