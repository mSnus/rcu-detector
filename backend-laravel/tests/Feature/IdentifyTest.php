<?php

namespace Tests\Feature;

use App\Models\RcuFingerprint;
use App\Models\RcuQuery;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Http\Client\ConnectionException;
use Illuminate\Http\UploadedFile;
use Illuminate\Support\Facades\Http;
use Illuminate\Support\Facades\Storage;
use Tests\TestCase;

/**
 * The recognition service is faked throughout. These tests are about the
 * Laravel side of the contract -- what gets stored, what the client is told,
 * and what happens when the service is down -- not about recognition quality,
 * which is measured in service-python by scripts/match_eval.py.
 */
class IdentifyTest extends TestCase
{
    use RefreshDatabase;

    /** A realistic /identify body, matching the service's actual response. */
    private function serviceResponse(array $overrides = []): array
    {
        return array_merge([
            'request_id' => '6d253f93cf0f4a0e',
            'confidence' => 'high',
            'latency_ms' => 4728,
            'extracted' => [
                'brand' => 'Sony',
                'model_code' => 'RM-PJ20R',
                'button_count' => 26,
                'quality' => 0.865,
                'orientation_conf' => 0.299,
            ],
            'candidates' => [[
                'record_id' => 'Sony_RM-PJ20_big_0',
                'score' => 1.4,
                'inliers' => 26,
                'brand' => 'Sony',
                'model_code' => 'RM-PJ20R',
                'terms' => [
                    'tier1' => 1.0, 'geometric' => 1.0, 'brand_agreement' => 1.0,
                    'aspect_agreement' => 1.0, 'model_code_bonus' => 0.4,
                ],
                'orientation' => ['candidate_flipped' => false, 'query_flipped' => false],
            ]],
            'hint' => null,
            'bodies_found' => 1,
            'retrieved' => 12,
            'model_code_fast_path' => true,
        ], $overrides);
    }

    private function photo(): UploadedFile
    {
        Storage::fake('rcu');

        return UploadedFile::fake()->image('remote.jpg', 800, 2000);
    }

    public function test_it_identifies_a_remote_and_logs_the_query(): void
    {
        Http::fake(['*/identify*' => Http::response($this->serviceResponse())]);

        $response = $this->postJson('/api/identify', ['photo' => $this->photo()]);

        $response->assertOk()
            ->assertJsonPath('confidence', 'high')
            ->assertJsonPath('candidates.0.record_id', 'Sony_RM-PJ20_big_0')
            ->assertJsonPath('extracted.brand', 'Sony');

        // The service mints the request id; ours is replaced by it, because
        // GET /debug/{request_id} is keyed on the service's.
        $this->assertSame('6d253f93cf0f4a0e', $response->json('request_id'));

        $query = RcuQuery::sole();
        $this->assertSame('6d253f93cf0f4a0e', $query->request_id);
        $this->assertSame('Sony_RM-PJ20_big_0', $query->top_record_id);
        $this->assertSame('high', $query->confidence);
        $this->assertEqualsWithDelta(1.4, $query->top_score, 1e-6);
        $this->assertTrue($query->model_code_fast_path);
        $this->assertSame(26, $query->extracted['button_count']);
    }

    public function test_it_stores_the_upload_on_the_rcu_disk(): void
    {
        Http::fake(['*/identify*' => Http::response($this->serviceResponse())]);

        $this->postJson('/api/identify', ['photo' => $this->photo()])->assertOk();

        Storage::disk('rcu')->assertExists(RcuQuery::sole()->upload_path);
    }

    public function test_it_sends_the_image_as_the_image_field(): void
    {
        Http::fake(['*/identify*' => Http::response($this->serviceResponse())]);

        $this->postJson('/api/identify', ['photo' => $this->photo()])->assertOk();

        // The plan says `photo`; the service reads `image`. Getting this wrong
        // is a 422 from the service on every single request.
        Http::assertSent(function ($request) {
            $names = collect($request->data())->pluck('name');

            return $request->isMultipart() && $names->contains('image');
        });
    }

    public function test_a_confident_answer_of_nothing_is_still_a_success(): void
    {
        Http::fake(['*/identify*' => Http::response($this->serviceResponse([
            'confidence' => 'none',
            'extracted' => null,
            'candidates' => [],
            'hint' => 'reshoot',
        ]))]);

        $this->postJson('/api/identify', ['photo' => $this->photo()])
            ->assertOk()
            ->assertJsonPath('confidence', 'none')
            ->assertJsonPath('hint', 'reshoot')
            ->assertJsonPath('candidates', []);

        $this->assertSame('none', RcuQuery::sole()->confidence);
    }

    public function test_it_returns_503_when_the_service_is_unreachable(): void
    {
        // The failure that matters most. `retry(throw: false)` does not
        // suppress this -- it has no response to return -- so it must be
        // caught explicitly or it surfaces as a 500.
        Http::fake(fn () => throw new ConnectionException('Connection refused'));

        $this->postJson('/api/identify', ['photo' => $this->photo()])
            ->assertStatus(503)
            ->assertJsonPath('error', 'recognition_unavailable');

        // The upload is still logged: a request that broke the service is
        // exactly the one worth keeping the photo for.
        $this->assertDatabaseCount('rcu_queries', 1);
    }

    public function test_it_returns_503_when_the_service_errors(): void
    {
        Http::fake(['*/identify*' => Http::response(['detail' => 'index not loaded'], 503)]);

        $this->postJson('/api/identify', ['photo' => $this->photo()])
            ->assertStatus(503)
            ->assertJsonPath('error', 'recognition_unavailable');
    }

    /**
     * A 4xx from the service is a verdict on the image, not an outage.
     *
     * Reported as 503 it tells the user to retry something that cannot
     * succeed, and it hides a service-side defect behind what looks like
     * downtime -- which is how the truncated-JPEG decode bug presented.
     */
    public function test_a_rejected_image_is_422_not_503(): void
    {
        Http::fake(['*/identify*' => Http::response(['detail' => 'undecodable image'], 400)]);

        $this->postJson('/api/identify', ['photo' => $this->photo()])
            ->assertStatus(422)
            ->assertJsonPath('error', 'image_rejected');

        // Still logged: an upload the service could not read is worth keeping.
        $this->assertDatabaseCount('rcu_queries', 1);
    }

    /**
     * The service's reason reaches the caller.
     *
     * "could not read that image" is wrong for an image the service read
     * perfectly well and refused on its size, and the difference is the only
     * thing that tells the user what to change about the photograph.
     */
    public function test_a_rejection_carries_the_services_own_reason(): void
    {
        Http::fake(['*/identify*' => Http::response(
            ['detail' => 'image too small: 200x300, long side must be at least 600px'], 400
        )]);

        $this->postJson('/api/identify', ['photo' => $this->photo()])
            ->assertStatus(422)
            ->assertJsonPath('message',
                'image too small: 200x300, long side must be at least 600px');
    }

    public function test_a_rejection_without_a_reason_still_says_something(): void
    {
        Http::fake(['*/identify*' => Http::response('not json at all', 400)]);

        $this->postJson('/api/identify', ['photo' => $this->photo()])
            ->assertStatus(422)
            ->assertJsonPath('error', 'image_rejected')
            ->assertJsonPath('message',
                'The recognition service could not read that image.');
    }

    public function test_an_oversized_image_reported_by_the_service_is_also_422(): void
    {
        Http::fake(['*/identify*' => Http::response(['detail' => 'image too large'], 413)]);

        $this->postJson('/api/identify', ['photo' => $this->photo()])
            ->assertStatus(422)
            ->assertJsonPath('error', 'image_rejected');
    }

    public function test_it_rejects_a_non_image(): void
    {
        Storage::fake('rcu');
        Http::fake();

        $this->postJson('/api/identify', [
            'photo' => UploadedFile::fake()->create('notes.pdf', 12, 'application/pdf'),
        ])->assertStatus(422)->assertJsonValidationErrors('photo');

        Http::assertNothingSent();
        $this->assertDatabaseCount('rcu_queries', 0);
    }

    public function test_it_rejects_an_oversized_image(): void
    {
        Storage::fake('rcu');
        Http::fake();

        $tooBig = config('rcu.max_upload_kb') + 1024;

        $this->postJson('/api/identify', [
            'photo' => UploadedFile::fake()->create('huge.jpg', $tooBig, 'image/jpeg'),
        ])->assertStatus(422)->assertJsonValidationErrors('photo');

        Http::assertNothingSent();
    }

    /**
     * A record_id is a fingerprint stem and means nothing to a client, so each
     * candidate carries its catalog row.
     */
    public function test_it_resolves_candidates_against_the_catalog(): void
    {
        RcuFingerprint::create([
            'record_id' => 'Sony_RM-PJ20_big_0',
            'model_id' => 77,
            'source_image' => 'Sony_RM-PJ20_big.jpg',
            'crop_index' => 0,
            'norm_path' => 'Sony_RM-PJ20_big_0.jpg',
            'aspect_ratio' => 4.821,
            'button_count' => 26,
            'fingerprint' => ['v' => 2],
            'brand_text' => 'Sony',
            'model_text' => 'RM-PJ20R',
            'title' => 'Sony RM-PJ20R projector remote',
            'quality_score' => 0.935,
            'reviewed' => true,
        ]);

        Http::fake(['*/identify*' => Http::response($this->serviceResponse())]);

        $this->postJson('/api/identify', ['photo' => $this->photo()])
            ->assertOk()
            // The catalogue's own name for the product. Without it a client has
            // only the record_id, a filename stem naming nothing a person
            // recognises -- brand_text/model_text are what the extractor read
            // off the photograph and are null on most records.
            ->assertJsonPath('candidates.0.catalog.title', 'Sony RM-PJ20R projector remote')
            ->assertJsonPath('candidates.0.catalog.model_id', 77)
            ->assertJsonPath('candidates.0.catalog.source_image', 'Sony_RM-PJ20_big.jpg')
            ->assertJsonPath('candidates.0.catalog.button_count', 26)
            ->assertJsonPath('candidates.0.catalog.reviewed', true)
            // Orientation is reported: a match against a record indexed upside
            // down is still a match, and the client has to know which way up.
            ->assertJsonPath('candidates.0.orientation.candidate_flipped', false);
    }

    /**
     * An unresolvable record_id means the index and the catalog table were
     * built from different runs. The match is still valid, so it is reported
     * with a null catalog rather than dropped or faked.
     */
    public function test_a_candidate_missing_from_the_catalog_is_reported_as_null(): void
    {
        Http::fake(['*/identify*' => Http::response($this->serviceResponse())]);

        $this->postJson('/api/identify', ['photo' => $this->photo()])
            ->assertOk()
            ->assertJsonPath('candidates.0.record_id', 'Sony_RM-PJ20_big_0')
            ->assertJsonPath('candidates.0.catalog', null);
    }
}
