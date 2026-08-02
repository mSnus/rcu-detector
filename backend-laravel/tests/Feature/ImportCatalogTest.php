<?php

namespace Tests\Feature;

use App\Models\RcuFingerprint;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Support\Facades\File;
use Tests\TestCase;

/**
 * Catalog ingestion.
 *
 * The table has to agree with the token index the Python service matches
 * against, because a record_id that resolves to no row makes a correct match
 * look like a database fault.
 */
class ImportCatalogTest extends TestCase
{
    use RefreshDatabase;

    private string $fpDir;
    private string $photoDir;
    private string $normDir;

    protected function setUp(): void
    {
        parent::setUp();

        $base = storage_path('framework/testing/catalog-' . uniqid());
        $this->fpDir = $base . '/fp';
        $this->photoDir = $base . '/photos';
        $this->normDir = $base . '/norm';

        foreach ([$this->fpDir, $this->photoDir, $this->normDir] as $dir) {
            File::ensureDirectoryExists($dir);
        }

        config([
            'rcu.catalog.fp_dir' => $this->fpDir,
            'rcu.catalog.photo_dir' => $this->photoDir,
            'rcu.catalog.norm_dir' => $this->normDir,
        ]);
    }

    protected function tearDown(): void
    {
        File::deleteDirectory(dirname($this->fpDir));

        parent::tearDown();
    }

    private function fingerprint(string $recordId, array $overrides = []): void
    {
        $fp = array_replace_recursive([
            'v' => 2,
            'body' => ['aspect' => 4.2, 'shape' => 'rounded_rect', 'area_frac' => 0.8],
            'buttons' => [['x' => 0.5, 'y' => 0.5, 'w' => 0.1, 'h' => 0.05,
                           'shape' => 'circle', 'color' => 'black']],
            'text_regions' => [],
            'brand' => 'Sony',
            'model_code' => 'RM-PJ20R',
            'stats' => ['n_buttons' => 12, 'orientation_flipped' => false,
                        'orientation_conf' => 1.0],
            'extract_quality' => 0.9,
        ], $overrides);

        File::put($this->fpDir . '/' . $recordId . '.json', json_encode($fp));
    }

    private function photo(string $stem): void
    {
        File::put($this->photoDir . '/' . $stem . '.jpg', 'not-really-a-jpeg');
    }

    public function test_it_imports_fingerprints_into_the_catalog(): void
    {
        $this->fingerprint('Sony_RM-PJ20_big_0');
        $this->photo('Sony_RM-PJ20_big');

        $this->artisan('rcu:import-catalog')->assertSuccessful();

        $record = RcuFingerprint::sole();
        $this->assertSame('Sony_RM-PJ20_big_0', $record->record_id);
        $this->assertSame('Sony_RM-PJ20_big.jpg', $record->source_image);
        $this->assertSame(0, $record->crop_index);
        $this->assertSame(12, $record->button_count);
        $this->assertSame('Sony', $record->brand_text);
        $this->assertSame('RM-PJ20R', $record->model_text);
        $this->assertEqualsWithDelta(0.9, $record->quality_score, 0.001);
        $this->assertIsArray($record->fingerprint);
    }

    public function test_it_splits_the_crop_index_off_the_record_id(): void
    {
        $this->fingerprint('MR-18B_0_1');
        $this->photo('MR-18B_0');

        $this->artisan('rcu:import-catalog')->assertSuccessful();

        $record = RcuFingerprint::sole();
        $this->assertSame('MR-18B_0.jpg', $record->source_image);
        $this->assertSame(1, $record->crop_index);
    }

    /**
     * The trap this catalog actually contains. Both ROLSEN_RSF-3106RT.jpg and
     * ROLSEN_RSF-3106RT_0.jpg exist and are different remotes, so the record
     * ROLSEN_RSF-3106RT_0 belongs to the *first* of them. Resolving a source
     * image by asking whether `<record_id>.jpg` exists gets this backwards,
     * silently pointing two records at one photograph.
     */
    public function test_a_photo_named_like_a_record_id_does_not_capture_it(): void
    {
        $this->fingerprint('ROLSEN_RSF-3106RT_0');
        $this->fingerprint('ROLSEN_RSF-3106RT_0_0');
        $this->photo('ROLSEN_RSF-3106RT');
        $this->photo('ROLSEN_RSF-3106RT_0');

        $this->artisan('rcu:import-catalog')->assertSuccessful();

        $this->assertSame(
            'ROLSEN_RSF-3106RT.jpg',
            RcuFingerprint::where('record_id', 'ROLSEN_RSF-3106RT_0')->sole()->source_image
        );
        $this->assertSame(
            'ROLSEN_RSF-3106RT_0.jpg',
            RcuFingerprint::where('record_id', 'ROLSEN_RSF-3106RT_0_0')->sole()->source_image
        );
    }

    public function test_re_importing_updates_rather_than_duplicates(): void
    {
        $this->fingerprint('Sony_RM-PJ20_big_0');
        $this->photo('Sony_RM-PJ20_big');
        $this->artisan('rcu:import-catalog')->assertSuccessful();

        $this->fingerprint('Sony_RM-PJ20_big_0', [
            'stats' => ['n_buttons' => 26], 'extract_quality' => 0.95,
        ]);
        $this->artisan('rcu:import-catalog')->assertSuccessful();

        $this->assertSame(1, RcuFingerprint::count());
        $this->assertSame(26, RcuFingerprint::sole()->button_count);
        // The update path writes the model's raw attributes back through the
        // array cast, which double-encodes the fingerprint if done carelessly.
        $this->assertIsArray(RcuFingerprint::sole()->fingerprint);
    }

    /**
     * `reviewed` and `model_id` are set by a person, not by the extractor. A
     * rebuild that clears them empties the review queue and unlinks the
     * catalog joins, losing work that cannot be recomputed.
     */
    public function test_a_rebuild_preserves_human_owned_columns(): void
    {
        $this->fingerprint('Sony_RM-PJ20_big_0');
        $this->photo('Sony_RM-PJ20_big');
        $this->artisan('rcu:import-catalog')->assertSuccessful();

        RcuFingerprint::sole()->update(['reviewed' => true, 'model_id' => 4242]);

        $this->fingerprint('Sony_RM-PJ20_big_0', ['extract_quality' => 0.4]);
        $this->artisan('rcu:import-catalog')->assertSuccessful();

        $record = RcuFingerprint::sole();
        $this->assertTrue($record->reviewed, 'review state was lost on rebuild');
        $this->assertSame(4242, (int) $record->model_id);
        $this->assertEqualsWithDelta(0.4, $record->quality_score, 0.001);
    }

    public function test_prune_removes_records_whose_fingerprint_is_gone(): void
    {
        $this->fingerprint('Sony_RM-PJ20_big_0');
        $this->fingerprint('DVD_80_0');
        $this->artisan('rcu:import-catalog')->assertSuccessful();
        $this->assertSame(2, RcuFingerprint::count());

        File::delete($this->fpDir . '/DVD_80_0.json');

        $this->artisan('rcu:import-catalog --prune')->assertSuccessful();

        $this->assertSame(1, RcuFingerprint::count());
        $this->assertSame('Sony_RM-PJ20_big_0', RcuFingerprint::sole()->record_id);
    }

    public function test_without_prune_a_stale_record_survives(): void
    {
        $this->fingerprint('Sony_RM-PJ20_big_0');
        $this->fingerprint('DVD_80_0');
        $this->artisan('rcu:import-catalog')->assertSuccessful();

        File::delete($this->fpDir . '/DVD_80_0.json');
        $this->artisan('rcu:import-catalog')->assertSuccessful();

        $this->assertSame(2, RcuFingerprint::count());
    }

    /**
     * An empty directory is refused (see above), so pruning can never be the
     * thing that empties the catalog. Deleting every fingerprint reads as "the
     * extraction run has not happened yet", which is far more often true than
     * "the catalog is genuinely empty now".
     */
    public function test_an_emptied_directory_cannot_prune_the_catalog_away(): void
    {
        $this->fingerprint('DVD_80_0');
        $this->artisan('rcu:import-catalog')->assertSuccessful();

        File::delete($this->fpDir . '/DVD_80_0.json');

        $this->artisan('rcu:import-catalog --prune')->assertFailed();
        $this->assertSame(1, RcuFingerprint::count());
    }

    public function test_a_dry_run_writes_nothing(): void
    {
        $this->fingerprint('Sony_RM-PJ20_big_0');

        $this->artisan('rcu:import-catalog --dry-run')->assertSuccessful();

        $this->assertSame(0, RcuFingerprint::count());
    }

    public function test_a_malformed_document_is_skipped_not_fatal(): void
    {
        $this->fingerprint('Sony_RM-PJ20_big_0');
        File::put($this->fpDir . '/broken_0.json', '{"nope": true}');

        $this->artisan('rcu:import-catalog')->assertSuccessful();

        $this->assertSame(1, RcuFingerprint::count());
        $this->assertSame('Sony_RM-PJ20_big_0', RcuFingerprint::sole()->record_id);
    }

    public function test_an_empty_fingerprint_directory_fails_loudly(): void
    {
        $this->artisan('rcu:import-catalog')->assertFailed();
    }

    /** A record whose photo is missing still imports: the fingerprint stands alone. */
    public function test_a_missing_source_photo_is_a_warning_not_a_failure(): void
    {
        $this->fingerprint('Sony_RM-PJ20_big_0');

        $this->artisan('rcu:import-catalog')->assertSuccessful();

        $this->assertSame(1, RcuFingerprint::count());
    }
}
