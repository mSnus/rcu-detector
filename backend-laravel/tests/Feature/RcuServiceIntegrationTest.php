<?php

namespace Tests\Feature;

use App\Models\RcuQuery;
use App\Services\RcuService;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Http\UploadedFile;
use Illuminate\Support\Facades\Storage;
use Tests\TestCase;

/**
 * End-to-end against the real Python service. Skipped when it is not running,
 * so the suite stays green on a machine without it.
 *
 * These exist because the rest of the suite fakes HTTP, and a faked contract
 * cannot catch a contract mismatch. The three things most likely to be wrong
 * are exactly the three the plan gets wrong -- the field name, who mints the
 * request id, and where top_k goes -- and only a real call proves them.
 *
 * Run with: php artisan test --filter=RcuServiceIntegration
 */
class RcuServiceIntegrationTest extends TestCase
{
    use RefreshDatabase;

    private function skipUnlessServiceIsUp(): void
    {
        $health = app(RcuService::class)->health();

        if (($health['status'] ?? null) !== 'ok') {
            $this->markTestSkipped(
                'recognition service not reachable at ' . config('rcu.service_url')
            );
        }

        if (($health['index_records'] ?? 0) < 1) {
            $this->markTestSkipped('service is up but holds an empty index');
        }
    }

    /** A catalog photo, so there is a known right answer to check against. */
    private function catalogPhoto(string $stem): string
    {
        $path = base_path('../photos/' . $stem . '.jpg');

        if (! is_file($path)) {
            $this->markTestSkipped("sample photo missing: {$path}");
        }

        return $path;
    }

    public function test_the_service_answers_health(): void
    {
        $this->skipUnlessServiceIsUp();

        $health = app(RcuService::class)->health();

        $this->assertSame('ok', $health['status']);
        $this->assertGreaterThan(0, $health['index_records']);
        $this->assertNotEmpty($health['ocr_engines']);
    }

    public function test_it_identifies_a_catalog_photo_end_to_end(): void
    {
        $this->skipUnlessServiceIsUp();
        Storage::fake('rcu');

        $photo = $this->catalogPhoto('ROLSEN_RSF-3106RT');

        $response = $this->postJson('/api/identify', [
            'photo' => new UploadedFile($photo, 'ROLSEN_RSF-3106RT.jpg',
                'image/jpeg', null, true),
        ]);

        $response->assertOk();

        // The service mints its own 16-hex request id; ours is replaced by it.
        $this->assertMatchesRegularExpression('/^[0-9a-f]{16}$/', $response->json('request_id'));
        $this->assertNotEmpty($response->json('candidates'));

        // This photo is in the catalog, so it must find itself.
        $this->assertStringStartsWith(
            'ROLSEN_RSF-3106RT', $response->json('candidates.0.record_id')
        );

        $query = RcuQuery::sole();
        $this->assertSame($response->json('request_id'), $query->request_id);
        $this->assertNotNull($query->top_score);
        $this->assertContains($query->confidence, ['high', 'medium', 'low', 'none']);
    }

    /**
     * A truncated JPEG must still be identifiable.
     *
     * `RM-PJ20_big_light.jpg` is missing its final EOI marker. `cv2.imread`
     * tolerates that, so the catalog build extracted and indexed it happily,
     * but the service decoded uploads with `cv2.imdecode`, which does not --
     * so the one thing that could never be matched was a remote already in
     * the catalog. Truncated files are ordinary in a scraped catalog.
     *
     * Only a real call proves this: a faked HTTP response cannot catch a
     * decoder disagreeing with itself.
     */
    public function test_a_truncated_jpeg_is_still_identifiable(): void
    {
        $this->skipUnlessServiceIsUp();
        Storage::fake('rcu');

        $photo = $this->catalogPhoto('RM-PJ20_big_light');

        if (str_ends_with(file_get_contents($photo), "\xFF\xD9")) {
            $this->markTestSkipped('sample photo is no longer truncated');
        }

        $this->postJson('/api/identify', [
            'photo' => new UploadedFile($photo, 'RM-PJ20_big_light.jpg',
                'image/jpeg', null, true),
        ])
            ->assertOk()
            ->assertJsonPath('error', null);

        $this->assertNotNull(RcuQuery::sole()->extracted,
            'the truncated photo produced no extraction');
    }

    public function test_fingerprint_returns_the_catalog_shape(): void
    {
        $this->skipUnlessServiceIsUp();

        $result = app(RcuService::class)->fingerprint(
            file_get_contents($this->catalogPhoto('ROLSEN_RSF-3106RT'))
        );

        $this->assertGreaterThanOrEqual(1, $result['bodies_found']);

        $fp = $result['fingerprints'][0];
        // The shape the admin visualiser and RcuFingerprint both read.
        $this->assertArrayHasKey('body', $fp);
        $this->assertArrayHasKey('buttons', $fp);
        $this->assertArrayHasKey('stats', $fp);
        $this->assertArrayHasKey('extract_quality', $fp);
        $this->assertArrayHasKey('n_buttons', $fp['stats']);
        $this->assertArrayHasKey('orientation_flipped', $fp['stats']);
    }

    public function test_a_fingerprint_maps_onto_the_model(): void
    {
        $this->skipUnlessServiceIsUp();

        $result = app(RcuService::class)->fingerprint(
            file_get_contents($this->catalogPhoto('ROLSEN_RSF-3106RT'))
        );

        $row = \App\Models\RcuFingerprint::fromServiceFingerprint(
            'ROLSEN_RSF-3106RT_0', $result['fingerprints'][0],
            'photos/ROLSEN_RSF-3106RT.jpg', 'work/norm/ROLSEN_RSF-3106RT_0.jpg'
        );
        $row->save();

        $this->assertGreaterThan(0, $row->button_count);
        $this->assertGreaterThan(0, $row->aspect_ratio);
        $this->assertDatabaseHas('rcu_fingerprints', [
            'record_id' => 'ROLSEN_RSF-3106RT_0',
        ]);
    }
}
