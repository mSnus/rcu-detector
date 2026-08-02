<?php

namespace Tests\Feature;

use App\Models\RcuFingerprint;
use App\Models\User;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Support\Facades\Http;
use Tests\TestCase;

/**
 * The catalog side of the admin visualiser.
 *
 * The review queue is the point of this page: the worst extraction in the
 * catalog is where the next real bug is, and it has to be reachable in one
 * click from a list that is ordered to put it there.
 */
class AdminCatalogTest extends TestCase
{
    use RefreshDatabase;

    protected function setUp(): void
    {
        parent::setUp();

        // The health call in the header must not reach the network in tests.
        Http::fake([
            '*/health' => Http::response(['status' => 'ok', 'index_records' => 2,
                                          'index_docs' => 2]),
        ]);
    }

    private function record(string $recordId, array $overrides = []): RcuFingerprint
    {
        return RcuFingerprint::create(array_merge([
            'record_id' => $recordId,
            'source_image' => 'photo.jpg',
            'crop_index' => 0,
            'norm_path' => $recordId . '.jpg',
            'aspect_ratio' => 4.2,
            'button_count' => 12,
            'fingerprint' => ['v' => 2, 'buttons' => []],
            'quality_score' => 0.9,
            'orientation_conf' => 1.0,
        ], $overrides));
    }

    private function admin(): User
    {
        return User::factory()->create();
    }

    /**
     * These pages serve user photographs and the service's internals, so a
     * guest must never reach one.
     *
     * Asserted as JSON because this install has no web auth scaffolding:
     * `routes/web.php` leaves the guard as the framework default, to be
     * pointed at the real admin authentication when this is mounted. On a
     * browser request the guard fires and then fails to build its redirect to
     * a `login` route that does not exist here, which would assert the absent
     * scaffolding rather than the guard. A JSON request gets a straight 401.
     */
    public function test_the_catalog_requires_authentication(): void
    {
        $this->getJson('/admin/rcu/catalog')->assertUnauthorized();
        $this->getJson('/admin/rcu/catalog/anything_0')->assertUnauthorized();
        $this->getJson('/admin/rcu/catalog/anything_0/crop')->assertUnauthorized();
        $this->postJson('/admin/rcu/catalog/anything_0/review')->assertUnauthorized();
    }

    public function test_it_lists_the_catalog_worst_first(): void
    {
        $this->record('good_0', ['quality_score' => 0.94]);
        $this->record('bad_0', ['quality_score' => 0.30]);

        $response = $this->actingAs($this->admin())->get('/admin/rcu/catalog');

        $response->assertOk()->assertSeeInOrder(['bad_0', 'good_0']);
    }

    /**
     * `catalog` is a literal segment sharing a prefix with the unconstrained
     * `{requestId}` route. Registered in the wrong order it resolves as a
     * request id and 404s on a query that does not exist.
     */
    public function test_the_catalog_route_is_not_swallowed_by_the_request_id_route(): void
    {
        $this->actingAs($this->admin())->get('/admin/rcu/catalog')
            ->assertOk()
            ->assertSee('Catalog');
    }

    public function test_it_filters_to_the_review_queue(): void
    {
        $this->record('needs_0', ['quality_score' => 0.30]);
        $this->record('fine_0', ['quality_score' => 0.94]);
        $this->record('checked_0', ['quality_score' => 0.31, 'reviewed' => true]);

        $response = $this->actingAs($this->admin())
            ->get('/admin/rcu/catalog?filter=review');

        $response->assertOk()->assertSee('needs_0')
            ->assertDontSee('fine_0')
            // Already reviewed: a person has looked, so it leaves the queue.
            ->assertDontSee('checked_0');
    }

    public function test_it_filters_to_unresolved_orientation(): void
    {
        $this->record('ambiguous_0', ['orientation_conf' => 0.0]);
        $this->record('certain_0', ['orientation_conf' => 1.0]);

        $this->actingAs($this->admin())->get('/admin/rcu/catalog?filter=ambiguous')
            ->assertOk()->assertSee('ambiguous_0')->assertDontSee('certain_0');
    }

    public function test_it_searches_by_brand_and_model_code(): void
    {
        $this->record('sony_0', ['brand_text' => 'Sony', 'model_text' => 'RM-PJ20R']);
        $this->record('other_0', ['brand_text' => 'Aiwa']);

        $this->actingAs($this->admin())->get('/admin/rcu/catalog?q=RM-PJ20')
            ->assertOk()->assertSee('sony_0')->assertDontSee('other_0');
    }

    public function test_it_shows_one_record(): void
    {
        $this->record('Sony_RM-PJ20_big_0', ['brand_text' => 'Sony']);

        $this->actingAs($this->admin())->get('/admin/rcu/catalog/Sony_RM-PJ20_big_0')
            ->assertOk()
            ->assertSee('Sony_RM-PJ20_big_0')
            ->assertSee('Sony');
    }

    public function test_an_unknown_record_404s(): void
    {
        $this->actingAs($this->admin())->get('/admin/rcu/catalog/nope_0')->assertNotFound();
    }

    public function test_it_marks_a_record_reviewed_and_back_again(): void
    {
        $this->record('Sony_RM-PJ20_big_0');

        $this->actingAs($this->admin())
            ->post('/admin/rcu/catalog/Sony_RM-PJ20_big_0/review')
            ->assertRedirect();

        $this->assertTrue(RcuFingerprint::sole()->reviewed);

        $this->actingAs($this->admin())
            ->post('/admin/rcu/catalog/Sony_RM-PJ20_big_0/review', ['unreview' => 1])
            ->assertRedirect();

        $this->assertFalse(RcuFingerprint::sole()->fresh()->reviewed);
    }

    public function test_a_missing_crop_404s_rather_than_erroring(): void
    {
        $this->record('Sony_RM-PJ20_big_0');

        config(['rcu.catalog.norm_dir' => storage_path('framework/testing/nothing-here')]);

        $this->actingAs($this->admin())
            ->get('/admin/rcu/catalog/Sony_RM-PJ20_big_0/crop')
            ->assertNotFound();
    }

    /**
     * The record id reaches the filesystem, so it must not be able to leave
     * the configured directory. The route pattern rejects the separators and
     * the controller checks the database besides.
     */
    public function test_a_traversing_record_id_cannot_read_arbitrary_files(): void
    {
        $this->actingAs($this->admin())
            ->get('/admin/rcu/catalog/' . urlencode('../../../../etc/passwd') . '/crop')
            ->assertNotFound();
    }

    /**
     * Serve a real crop and a real overlay off disk.
     *
     * The fixtures above prove the 404 path; this proves the path that
     * matters. The overlay is the single highest-value artefact in this
     * project, and a page that renders a broken image instead of it is worse
     * than useless -- it looks like the extraction produced nothing.
     */
    public function test_it_serves_real_catalog_images_from_the_build_output(): void
    {
        $normDir = config('rcu.catalog.norm_dir');
        $debugDir = config('rcu.catalog.debug_dir');
        $recordId = 'MR-18B_0_1';

        if (! is_file($normDir . '/' . $recordId . '.jpg')) {
            $this->markTestSkipped("no extraction output at {$normDir}");
        }

        $this->record($recordId);
        $admin = $this->admin();

        $crop = $this->actingAs($admin)->get("/admin/rcu/catalog/{$recordId}/crop");
        $crop->assertOk()->assertHeader('Content-Type', 'image/jpeg');
        $this->assertStringStartsWith("\xFF\xD8\xFF", $crop->getContent(),
            'the crop route did not return JPEG bytes');

        if (! is_file($debugDir . '/' . $recordId . '.jpg')) {
            return;
        }

        $overlay = $this->actingAs($admin)->get("/admin/rcu/catalog/{$recordId}/build-overlay");
        $overlay->assertOk()->assertHeader('Content-Type', 'image/jpeg');
        $this->assertStringStartsWith("\xFF\xD8\xFF", $overlay->getContent());
    }
}
