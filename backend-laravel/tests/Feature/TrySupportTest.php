<?php

namespace Tests\Feature;

use App\Mail\SupportRequestMail;
use App\Models\RcuQuery;
use App\Models\RcuSupportRequest;
use App\Support\SupportImage;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Support\Facades\Mail;
use Illuminate\Support\Facades\Storage;
use Tests\TestCase;

/**
 * The support form on /try: the way to reach a person.
 *
 * The row is the record of intent and must survive everything else failing.
 * These tests are mostly about that: an unset e-mail address, an upload that
 * is not there, an upstream API that is not configured.
 */
class TrySupportTest extends TestCase
{
    use RefreshDatabase;

    protected function setUp(): void
    {
        parent::setUp();
        config(['rcu.try_page' => true, 'rcu.support.enabled' => true]);
        Storage::fake('rcu');
        Storage::fake('rcu_support');
        config(['rcu.upload_disk' => 'rcu', 'rcu.support.disk' => 'rcu_support']);
    }

    private function query(string $id = 'req-1'): RcuQuery
    {
        Storage::disk('rcu')->put('uploads/x.jpg', $this->jpeg(300, 200));

        return RcuQuery::create([
            'request_id' => $id,
            'upload_path' => 'uploads/x.jpg',
            'confidence' => 'high',
            'top_record_id' => 'ABC_0',
        ]);
    }

    private function jpeg(int $w, int $h): string
    {
        $im = imagecreatetruecolor($w, $h);
        ob_start();
        imagejpeg($im);

        return (string) ob_get_clean();
    }

    public function test_it_stores_the_request_and_mails_support(): void
    {
        Mail::fake();
        config(['rcu.support.email' => 'support@example.com',
                'mail.default' => 'smtp']);
        $this->query();

        $this->postJson('/try/support', [
            'request_id' => 'req-1', 'name' => 'Иван', 'phone' => '+7 900 000',
        ])->assertOk()->assertJson(['ok' => true]);

        $req = RcuSupportRequest::firstOrFail();
        $this->assertSame('Иван', $req->name);
        $this->assertSame('+7 900 000', $req->phone);
        // What the customer was shown, denormalised: the catalogue moves.
        $this->assertSame('high', $req->confidence);
        $this->assertSame('ABC_0', $req->top_record_id);
        $this->assertNotNull($req->emailed_at);
        Mail::assertSent(SupportRequestMail::class);
    }

    /** No address configured is a supported state, not a lost request. */
    public function test_the_request_survives_an_unconfigured_mailbox(): void
    {
        config(['rcu.support.email' => null]);
        $this->query();

        $this->postJson('/try/support', [
            'request_id' => 'req-1', 'name' => 'A', 'phone' => 'B',
        ])->assertOk();

        $req = RcuSupportRequest::firstOrFail();
        $this->assertNull($req->emailed_at);
        $this->assertStringContainsString('no-address-configured',
            (string) $req->delivery_error);
    }

    /** The photo is taken from the query, never re-uploaded by the phone. */
    public function test_it_copies_the_photo_into_the_support_folder(): void
    {
        Mail::fake();
        $this->query();

        $this->postJson('/try/support',
            ['request_id' => 'req-1', 'name' => 'A', 'phone' => 'B'])->assertOk();

        $path = RcuSupportRequest::firstOrFail()->image_path;
        $this->assertNotNull($path);
        Storage::disk('rcu_support')->assertExists($path);
        // Never the caller's filename, and never the upload's path.
        $this->assertStringNotContainsString('uploads', $path);
    }

    public function test_a_missing_upload_does_not_fail_the_request(): void
    {
        Mail::fake();
        RcuQuery::create(['request_id' => 'gone', 'upload_path' => 'uploads/nope.jpg']);

        $this->postJson('/try/support',
            ['request_id' => 'gone', 'name' => 'A', 'phone' => 'B'])->assertOk();

        $this->assertNull(RcuSupportRequest::firstOrFail()->image_path);
    }

    public function test_name_and_phone_are_required(): void
    {
        $this->postJson('/try/support', ['name' => '', 'phone' => ''])
            ->assertStatus(422);
        $this->assertSame(0, RcuSupportRequest::count());
    }

    /** A box without the test page has no public write endpoint either. */
    public function test_it_is_404_when_the_try_page_is_off(): void
    {
        config(['rcu.try_page' => false]);
        $this->postJson('/try/support', ['name' => 'A', 'phone' => 'B'])
            ->assertStatus(404);
    }

    public function test_downscale_bounds_the_long_side(): void
    {
        $out = SupportImage::downscale($this->jpeg(4000, 3000), 2000);
        $this->assertNotNull($out);
        [$w, $h] = getimagesizefromstring($out);
        $this->assertSame(2000, $w);
        $this->assertSame(1500, $h);
    }

    /** Already small: re-encoding would cost a JPEG generation for nothing. */
    public function test_downscale_leaves_a_small_image_alone(): void
    {
        $this->assertNull(SupportImage::downscale($this->jpeg(800, 600), 2000));
    }

    /**
     * The trap this guards: `Mail::send()` succeeds on the `log` mailer, so a
     * naive implementation stamps emailed_at for a message nobody received.
     * rcud runs on `log` today -- MAIL_MAILER is unset and the container has
     * no sendmail -- so this is the live configuration, not a hypothetical.
     */
    public function test_a_non_delivering_mailer_is_not_recorded_as_delivered(): void
    {
        Mail::fake();
        config(['rcu.support.email' => 'support@example.com',
                'mail.default' => 'log']);
        $this->query();

        $this->postJson('/try/support',
            ['request_id' => 'req-1', 'name' => 'A', 'phone' => 'B'])->assertOk();

        $req = RcuSupportRequest::firstOrFail();
        $this->assertNull($req->emailed_at);
        $this->assertStringContainsString('not-delivered-transport-is-log',
            (string) $req->delivery_error);
        // Still handed to the mailer: on `log` that is how you read what
        // would have been sent.
        Mail::assertSent(SupportRequestMail::class);
    }

    /**
     * No API URL is a decision not yet taken, not a failure. It must leave
     * nothing on delivery_error, or the column stops being useful for
     * spotting the requests that genuinely did go wrong.
     */
    public function test_an_unconfigured_api_is_skipped_silently(): void
    {
        Mail::fake();
        config(['rcu.support.email' => 'support@example.com',
                'rcu.support.api_url' => null, 'mail.default' => 'smtp']);
        $this->query();

        $this->postJson('/try/support',
            ['request_id' => 'req-1', 'name' => 'A', 'phone' => 'B'])->assertOk();

        $req = RcuSupportRequest::firstOrFail();
        $this->assertNull($req->forwarded_at);
        $this->assertNull($req->delivery_error);
        $this->assertNotNull($req->emailed_at);
    }
}
