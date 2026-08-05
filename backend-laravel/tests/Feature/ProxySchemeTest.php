<?php

namespace Tests\Feature;

use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Support\Facades\Http;
use Tests\TestCase;

/**
 * TLS is terminated by a reverse proxy, so the scheme arrives in a header.
 *
 * Untrusted, Laravel builds absolute URLs as http. That sent a guest hitting
 * https://rcud.pultovnet.ru/admin/rcu to an http login form on the live host.
 */
class ProxySchemeTest extends TestCase
{
    use RefreshDatabase;

    protected function setUp(): void
    {
        parent::setUp();

        Http::fake(['*/health' => Http::response(['status' => 'ok'])]);
    }

    public function test_a_forwarded_https_request_redirects_to_https(): void
    {
        $response = $this->get('/admin/rcu', [
            'X-Forwarded-Proto' => 'https',
            'X-Forwarded-For' => '203.0.113.10',
        ]);

        $response->assertRedirect();
        $this->assertStringStartsWith(
            'https://', (string) $response->headers->get('Location'),
            'the login redirect must keep the scheme the user arrived on',
        );
    }

    public function test_plain_http_is_left_alone(): void
    {
        // No forwarded header: nothing to honour, and nothing to invent.
        $this->get('/admin/rcu')
            ->assertRedirect();

        $this->assertStringStartsWith(
            'http://', (string) $this->get('/admin/rcu')->headers->get('Location'),
        );
    }
}
