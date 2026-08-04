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
 *     php artisan rcu:legacy-manifest --out=- > work/primary.txt
 *     docker compose --profile build run --rm extract --manifest /data/work/primary.txt
 *
 * Measured on the 100-product sample: 100 primary photographs against 86
 * further images that are not remotes.
 *
 * Lines are paths relative to the files directory, not bare filenames, because
 * on the live catalogue most originals have been deleted and only Drupal's
 * imagecache derivatives remain -- 3069 originals against 10693 that exist
 * only under imagecache. See `files_search_path` in config/rcu.php. The stem
 * of the basename is unchanged either way, so a record extracted from a
 * derivative still keys onto its catalogue row.
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

        $searchPath = config('rcu.catalog.files_search_path') ?: ['.'];

        $present = [];
        $missing = [];
        $foundIn = [];

        foreach ($names as $name) {
            $path = $this->locate($filesDir, $searchPath, $name);

            if ($path === null) {
                $missing[] = $name;
                continue;
            }

            $present[] = $path;
            $dir = dirname($path);
            $foundIn[$dir] = ($foundIn[$dir] ?? 0) + 1;
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
        $this->report('<info>' . count($present) . ' of ' . count($names)
            . " catalogued photograph(s) written to {$where}</info>");

        // Which directory each one came from. Worth printing every time: a
        // build drawing mostly on imagecache is working from derivatives
        // rather than originals, which is a fact about the whole catalog and
        // is otherwise invisible once extraction has run.
        ksort($foundIn);

        foreach ($foundIn as $dir => $count) {
            $this->report(sprintf('  %6d from %s', $count, $dir === '.' ? $filesDir : $dir));
        }

        if ($missing !== []) {
            $this->report('<comment>' . count($missing)
                . ' photograph(s) are on no search path under '
                . "{$filesDir}:</comment>");

            foreach (array_slice($missing, 0, 10) as $name) {
                $this->report("  {$name}");
            }
        }

        $this->reportExcluded($filesDir, $present);

        return self::SUCCESS;
    }

    /**
     * The first search-path directory holding this photograph, as a path
     * relative to $filesDir -- or null if it is on none of them.
     *
     * @param  list<string>  $searchPath
     */
    private function locate(string $filesDir, array $searchPath, string $name): ?string
    {
        foreach ($searchPath as $dir) {
            $dir = trim($dir, '/');
            $relative = ($dir === '' || $dir === '.') ? $name : $dir . '/' . $name;

            if (is_file($filesDir . '/' . $relative)) {
                return $relative;
            }
        }

        return null;
    }

    /**
     * What is in the top level of the photo directory but not in the manifest.
     *
     * This is the number the command exists for, so it is always reported:
     * silently extracting a third more images than the catalogue can key is
     * exactly the failure this prevents, and it is invisible afterwards --
     * the extra records look like ordinary metadata misses.
     *
     * Only the top level, deliberately. The search path reaches into
     * imagecache, where the live site keeps ~96k derivative files across six
     * presets; walking those to report them as "excluded" would say nothing
     * useful and cost a full tree scan.
     *
     * @param  list<string>  $present  paths relative to $filesDir
     */
    private function reportExcluded(string $filesDir, array $present): void
    {
        $onDisk = glob($filesDir . '/*');

        if ($onDisk === false) {
            return;
        }

        // Keyed on basename: locate() prefers the top level, so anything
        // taken from a search-path directory is not also up here.
        $keep = array_flip(array_map('basename', $present));
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
