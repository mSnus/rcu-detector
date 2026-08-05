<?php

namespace Tests\Feature;

use App\Models\RcuFingerprint;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Tests\TestCase;

/**
 * The /try test page.
 *
 * It has no authentication, so the thing most worth pinning is that it does
 * not exist unless a box asks for it. Everything else on this page is a thin
 * wrapper over /api/identify, which IdentifyTest already covers.
 */
class TryPageTest extends TestCase
{
    use RefreshDatabase;

    protected function setUp(): void
    {
        parent::setUp();

        config(['rcu.try_page' => true]);
    }

    public function test_the_page_is_absent_unless_the_flag_is_set(): void
    {
        // 404 rather than 403: a box that has not enabled the page should not
        // advertise that it exists.
        config(['rcu.try_page' => false]);

        $this->get('/try')->assertNotFound();
        $this->get('/try/photo/anything')->assertNotFound();
    }

    public function test_it_renders_the_capture_control(): void
    {
        $this->get('/try')
            ->assertOk()
            // capture="environment" is what opens the camera rather than a
            // file browser, which is the entire point of the page on a phone.
            ->assertSee('capture="environment"', false)
            ->assertSee('/api/identify', false);
    }

    public function test_it_offers_an_upload_as_well_as_the_camera(): void
    {
        // Two controls, and the second must NOT carry `capture`: that attribute
        // is the only difference between them, and a copy-paste that keeps it
        // gives a phone two camera buttons and no way to pick an existing file.
        $html = $this->get('/try')->assertOk()->getContent();

        $this->assertSame(2, substr_count($html, 'type="file"'));
        $this->assertSame(1, substr_count($html, 'capture='));
        $this->assertStringContainsString('id="upload"', $html);
    }

    public function test_it_states_the_size_limit_before_the_choice(): void
    {
        // The limit has to come from config, or the page and the server drift
        // and the page becomes confidently wrong about the reason for a refusal.
        config(['rcu.max_upload_kb' => 20480]);

        $this->get('/try')->assertOk()->assertSee('up to 20 MB', false);
    }

    public function test_candidate_images_use_a_path_not_an_absolute_url(): void
    {
        // Behind a TLS-terminating proxy an absolute URL comes out as http://
        // unless TrustProxies is configured for that proxy, and every crop is
        // then blocked as mixed content on the https page. A path cannot.
        $this->get('/try')
            ->assertSee('const PHOTO_URL = "\/try\/photo\/__ID__"', false);
    }

    public function test_it_says_how_small_the_catalog_is(): void
    {
        // A sample catalog answers `none` to almost every real remote. Saying
        // so on the page is the difference between "the recogniser is broken"
        // and "there are 3 records in it".
        $this->record('AAA_0');

        $this->get('/try')->assertSee('1 record in the catalog');
    }

    public function test_a_candidate_photo_needs_a_catalog_row(): void
    {
        $this->get('/try/photo/no-such-record')->assertNotFound();
    }

    public function test_it_serves_the_crop_for_a_real_record(): void
    {
        $dir = sys_get_temp_dir() . '/rcu-try-' . uniqid();
        mkdir($dir);
        file_put_contents($dir . '/AAA_0.jpg', 'not-really-a-jpeg');
        config(['rcu.catalog.norm_dir' => $dir]);

        $this->record('AAA_0');

        $this->get('/try/photo/AAA_0')
            ->assertOk()
            ->assertHeader('Content-Type', 'image/jpeg');

        unlink($dir . '/AAA_0.jpg');
        rmdir($dir);
    }

    public function test_a_record_id_cannot_escape_the_crop_directory(): void
    {
        // The id is confirmed against the database and basename'd regardless.
        // A route parameter must never be concatenated into a path on trust.
        $this->get('/try/photo/..%2F..%2Fetc%2Fpasswd')->assertNotFound();
    }

    private function record(string $recordId): RcuFingerprint
    {
        return RcuFingerprint::create([
            'record_id' => $recordId,
            'source_image' => 'photo.jpg',
            'crop_index' => 0,
            'norm_path' => $recordId . '.jpg',
            'aspect_ratio' => 4.2,
            'button_count' => 12,
            'fingerprint' => ['v' => 2, 'buttons' => []],
            'quality_score' => 0.9,
            'orientation_conf' => 1.0,
        ]);
    }
}
