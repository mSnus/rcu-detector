<?php

namespace Tests\Feature;

use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Support\Facades\Artisan;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\File;
use Tests\TestCase;

/**
 * `rcu:legacy-manifest` decides what the catalog build extracts.
 *
 * The extraction container has no database, so without this list it extracts
 * every file in the photo directory -- and the legacy `files/` directory is a
 * third non-remotes. That failure is invisible after the fact: the extra
 * records import as ordinary metadata misses.
 */
class LegacyManifestTest extends TestCase
{
    use RefreshDatabase;

    private string $filesDir;
    private string $out;

    protected function setUp(): void
    {
        parent::setUp();

        $base = storage_path('framework/testing/manifest-' . uniqid());
        $this->filesDir = $base . '/files';
        $this->out = $base . '/primary.txt';
        File::ensureDirectoryExists($this->filesDir);

        config([
            'rcu.catalog.files_dir' => $this->filesDir,
            'database.connections.legacy' => [
                'driver' => 'sqlite', 'database' => ':memory:', 'prefix' => '',
            ],
        ]);

        $legacy = DB::connection('legacy');
        $legacy->getSchemaBuilder()->create('node', function ($t) {
            $t->integer('nid'); $t->string('type'); $t->string('title');
        });
        $legacy->getSchemaBuilder()->create('files', function ($t) {
            $t->integer('fid'); $t->integer('nid');
            $t->string('filename'); $t->string('filepath');
        });
        $legacy->getSchemaBuilder()->create('content_field_image_cache', function ($t) {
            $t->integer('nid'); $t->integer('delta');
            $t->integer('field_image_cache_fid');
        });
    }

    protected function tearDown(): void
    {
        File::deleteDirectory(dirname($this->filesDir));
        parent::tearDown();
    }

    private function product(int $nid, string $title, array $files): void
    {
        DB::connection('legacy')->table('node')
            ->insert(['nid' => $nid, 'type' => 'product', 'title' => $title]);

        foreach ($files as $delta => [$fid, $filename, $filepath]) {
            DB::connection('legacy')->table('files')->insert([
                'fid' => $fid, 'nid' => $nid,
                'filename' => $filename, 'filepath' => $filepath,
            ]);
            DB::connection('legacy')->table('content_field_image_cache')->insert([
                'nid' => $nid, 'delta' => $delta, 'field_image_cache_fid' => $fid,
            ]);
        }
    }

    private function onDisk(string ...$names): void
    {
        foreach ($names as $name) {
            File::put($this->filesDir . '/' . $name, 'x');
        }
    }

    /** @return list<string> */
    private function manifest(): array
    {
        return array_values(array_filter(explode("\n", File::get($this->out))));
    }

    /**
     * The whole point: a promo banner and an instruction sheet sit at delta
     * 1-2 on the same product, in the same directory, and are not remotes.
     */
    public function test_it_lists_only_the_delta_zero_photo(): void
    {
        $this->product(1325, '6710V00125A пульт для телевизоров LG', [
            0 => [21, '6710V00125A.jpg', 'files/6710V00125A.jpg'],
            1 => [22, 'Zamena_TV_4.jpg', 'files/Zamena_TV_4_615.jpg'],
            2 => [23, 'MMD_instr.jpg', 'files/MMD_instr.jpg'],
        ]);
        $this->onDisk('6710V00125A.jpg', 'Zamena_TV_4_615.jpg', 'MMD_instr.jpg');

        $this->artisan("rcu:legacy-manifest --out={$this->out}")
            ->expectsOutputToContain('2 file(s)')
            ->assertSuccessful();

        $this->assertSame(['6710V00125A.jpg'], $this->manifest());
    }

    /**
     * basename(filepath), never `filename` -- the same rule the import keys
     * on. Listing `filename` would hand the build a path that is not there.
     */
    public function test_it_names_the_file_on_disk_not_the_drupal_filename(): void
    {
        $this->product(171, 'IRC-2406D [TELEFUNKEN TV]',
            [0 => [31, 'IRC_new.jpg', 'files/IRC_new_237_51.jpg']]);
        $this->onDisk('IRC_new_237_51.jpg');

        $this->artisan("rcu:legacy-manifest --out={$this->out}")->assertSuccessful();

        $this->assertSame(['IRC_new_237_51.jpg'], $this->manifest());
    }

    /**
     * A basename naming two products is a metadata ambiguity, reported by the
     * import. It is still one photograph of one real remote, so excluding it
     * from the build would lose the record entirely -- worse than importing
     * it without a title.
     */
    public function test_an_ambiguous_filename_is_still_extracted_once(): void
    {
        $this->product(171, 'IRC-2406D', [0 => [31, 'IRC_new.jpg', 'files/IRC_new.jpg']]);
        $this->product(1508, 'MYSTERY MMD-3601', [0 => [32, 'IRC_new.jpg', 'files/IRC_new.jpg']]);
        $this->onDisk('IRC_new.jpg');

        $this->artisan("rcu:legacy-manifest --out={$this->out}")->assertSuccessful();

        $this->assertSame(['IRC_new.jpg'], $this->manifest());
    }

    /**
     * A catalogue row whose file is gone must be reported, not silently
     * dropped: the summary otherwise counts what succeeded and nothing counts
     * what vanished.
     */
    public function test_it_reports_photographs_missing_from_disk(): void
    {
        $this->product(1508, 'MYSTERY MMD-3601', [0 => [11, 'a.jpg', 'files/a.jpg']]);
        $this->product(1509, 'ROLSEN RSF-3106RT', [0 => [12, 'b.jpg', 'files/b.jpg']]);
        $this->onDisk('a.jpg');

        $this->artisan("rcu:legacy-manifest --out={$this->out}")
            ->expectsOutputToContain('1 listed photograph(s) are not in')
            ->assertSuccessful();

        $this->assertSame(['a.jpg'], $this->manifest());
    }

    /**
     * `--out=-` is how deployment runs this: the Laravel container mounts
     * work/ read-only, so the list leaves over stdout and the operator
     * redirects it.
     *
     * What this pins is that the list itself reaches stdout, unwritten to any
     * file. That the *diagnostics* stay off stdout cannot be asserted here --
     * getErrorStyle() falls back to stdout when there is no real console, and
     * under $this->artisan() there is none. It is checked against the
     * container instead.
     */
    public function test_stdout_mode_emits_the_list_without_writing_a_file(): void
    {
        $this->product(1508, 'MYSTERY MMD-3601', [0 => [11, 'a.jpg', 'files/a.jpg']]);
        $this->product(1509, 'ROLSEN RSF-3106RT', [0 => [12, 'b.jpg', 'files/b.jpg']]);
        $this->onDisk('a.jpg', 'b.jpg', 'Zamena_TV_4.jpg');

        $status = $this->withoutMockingConsoleOutput()
            ->artisan('rcu:legacy-manifest --out=-');

        $this->assertSame(0, $status);
        $this->assertStringContainsString("a.jpg\nb.jpg\n", Artisan::output());
        $this->assertFileDoesNotExist($this->out);
    }

    /**
     * Nodes that are not products have photographs too, and none of them are
     * remotes.
     */
    public function test_it_ignores_nodes_that_are_not_products(): void
    {
        $this->product(1508, 'MYSTERY MMD-3601', [0 => [11, 'a.jpg', 'files/a.jpg']]);

        DB::connection('legacy')->table('node')
            ->insert(['nid' => 9000, 'type' => 'page', 'title' => 'Доставка']);
        DB::connection('legacy')->table('files')->insert([
            'fid' => 90, 'nid' => 9000, 'filename' => 'p.jpg', 'filepath' => 'files/p.jpg',
        ]);
        DB::connection('legacy')->table('content_field_image_cache')->insert([
            'nid' => 9000, 'delta' => 0, 'field_image_cache_fid' => 90,
        ]);

        $this->onDisk('a.jpg', 'p.jpg');

        $this->artisan("rcu:legacy-manifest --out={$this->out}")->assertSuccessful();

        $this->assertSame(['a.jpg'], $this->manifest());
    }
}
