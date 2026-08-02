<?php

namespace Tests\Feature;

use App\Models\RcuFingerprint;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\File;
use Illuminate\Support\Facades\Schema;
use Tests\TestCase;

/**
 * Importing metadata from the legacy Drupal catalogue.
 *
 * The legacy connection is faked with SQLite carrying the same table shapes,
 * so these run without the real database. Both rules asserted here were found
 * by measuring the real data, and both fail *silently* when broken -- which is
 * the whole reason they are pinned.
 */
class ImportLegacyCatalogTest extends TestCase
{
    use RefreshDatabase;

    private string $fpDir;
    private string $filesDir;

    protected function setUp(): void
    {
        parent::setUp();

        $base = storage_path('framework/testing/legacy-' . uniqid());
        $this->fpDir = $base . '/fp';
        $this->filesDir = $base . '/files';
        File::ensureDirectoryExists($this->fpDir);
        File::ensureDirectoryExists($this->filesDir);

        config([
            'rcu.catalog.fp_dir' => $this->fpDir,
            'rcu.catalog.norm_dir' => $base . '/norm',
            'rcu.catalog.photo_dir' => $this->filesDir,
            'rcu.catalog.item_url' => 'https://pultov.net/item/{id}',
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
        File::deleteDirectory(dirname($this->fpDir));
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

    private function fingerprint(string $recordId): void
    {
        File::put($this->fpDir . '/' . $recordId . '.json', json_encode([
            'v' => 2,
            'body' => ['aspect' => 4.2, 'shape' => 'rounded_rect'],
            'buttons' => [], 'text_regions' => [],
            'brand' => 'Sony', 'model_code' => 'RM-1',
            'stats' => ['n_buttons' => 9, 'orientation_flipped' => false,
                        'orientation_conf' => 1.0],
            'extract_quality' => 0.9,
        ]));
    }

    public function test_it_takes_title_and_nid_from_the_catalogue(): void
    {
        $this->product(1508, 'MYSTERY MMD-3601 пульт для телевизора',
            [0 => [11, 'MMD-3601_all.jpg', 'files/MMD-3601_all.jpg']]);
        $this->fingerprint('1508_0');

        $this->artisan('rcu:import-catalog --legacy')->assertSuccessful();

        $r = RcuFingerprint::sole();
        $this->assertSame('1508_0', $r->record_id);
        $this->assertSame(1508, (int) $r->model_id);
        // Stored verbatim -- never parsed into brand/model.
        $this->assertSame('MYSTERY MMD-3601 пульт для телевизора', $r->title);
        // ...while brand/model stay as the extractor read them off the image.
        $this->assertSame('Sony', $r->brand_text);
        $this->assertSame('https://pultov.net/item/1508', $r->itemUrl());
    }

    /**
     * delta 0 is the product's own photo. Higher deltas are replacement-model
     * promos and instruction sheets -- on the real data, deltas 1-2 hold all
     * 36 Zamena_* banners and 12 of 13 manuals, and delta 0 holds none of
     * either. Importing on files.nid alone extracts those as if they were
     * remotes and poisons the index.
     */
    public function test_it_uses_the_delta_zero_photo_not_just_any_file(): void
    {
        $this->product(1325, '6710V00125A пульт для телевизоров LG', [
            0 => [21, '6710V00125A.jpg', 'files/6710V00125A.jpg'],
            1 => [22, 'Zamena_TV_4.jpg', 'files/Zamena_TV_4_615.jpg'],
            2 => [23, 'MMD_instr.jpg', 'files/MMD_instr.jpg'],
        ]);
        $this->fingerprint('1325_0');

        $this->artisan('rcu:import-catalog --legacy')->assertSuccessful();

        $this->assertSame('6710V00125A.jpg', RcuFingerprint::sole()->source_image);
    }

    /**
     * The file on disk is basename(filepath), never `filename`. Drupal appends
     * _NN on collision, so the two disagree on 53 of 186 real rows -- and
     * `filename` is not unique: two different remotes are both "IRC_new.jpg".
     * Keying on it merges two records into one.
     */
    public function test_the_source_image_comes_from_filepath_not_filename(): void
    {
        $this->product(171, 'IRC-2406D [TELEFUNKEN TV]',
            [0 => [31, 'IRC_new.jpg', 'files/IRC_new_237_51.jpg']]);
        $this->product(1508, 'MYSTERY MMD-3601',
            [0 => [32, 'IRC_new.jpg', 'files/IRC_new_20.jpg']]);
        $this->fingerprint('171_0');
        $this->fingerprint('1508_0');

        $this->artisan('rcu:import-catalog --legacy')->assertSuccessful();

        $this->assertSame(2, RcuFingerprint::count(), 'the two records merged');
        $this->assertSame('IRC_new_237_51.jpg',
            RcuFingerprint::where('record_id', '171_0')->sole()->source_image);
        $this->assertSame('IRC_new_20.jpg',
            RcuFingerprint::where('record_id', '1508_0')->sole()->source_image);
    }

    /** A fingerprint with no product row means fp/ and the DB disagree. */
    public function test_an_unmatched_fingerprint_still_imports_and_is_reported(): void
    {
        $this->fingerprint('99999_0');

        $this->artisan('rcu:import-catalog --legacy')
            ->expectsOutputToContain('no product row in the legacy DB')
            ->assertSuccessful();

        $this->assertSame(1, RcuFingerprint::count());
        $this->assertNull(RcuFingerprint::sole()->title);
    }

    /** Review state survives a rebuild; the catalogue link is refreshed. */
    public function test_a_rebuild_keeps_review_state_and_refreshes_the_link(): void
    {
        $this->product(1508, 'first title',
            [0 => [11, 'a.jpg', 'files/a.jpg']]);
        $this->fingerprint('1508_0');
        $this->artisan('rcu:import-catalog --legacy')->assertSuccessful();

        RcuFingerprint::sole()->update(['reviewed' => true]);
        DB::connection('legacy')->table('node')->where('nid', 1508)
            ->update(['title' => 'renamed in the catalogue']);

        $this->artisan('rcu:import-catalog --legacy')->assertSuccessful();

        $r = RcuFingerprint::sole();
        $this->assertTrue($r->reviewed, 'review state was lost');
        $this->assertSame('renamed in the catalogue', $r->title);
        $this->assertSame(1508, (int) $r->model_id);
    }
}
