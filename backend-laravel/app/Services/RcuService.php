<?php

namespace App\Services;

use Illuminate\Http\Client\ConnectionException;
use Illuminate\Http\Client\PendingRequest;
use Illuminate\Http\Client\RequestException;
use Illuminate\Support\Facades\Http;
use Illuminate\Support\Facades\Log;

/**
 * The only thing in this application that talks to the recognition service.
 *
 * All computer vision lives in service-python, behind loopback HTTP on
 * 127.0.0.1:8600. None of it may be reimplemented here: the catalog and the
 * query path must run identical extraction code, or matching degrades in ways
 * that look like a scoring bug and are not.
 *
 * The wire contract is the service's, not plan 6.3's, which is out of date on
 * three points that would each break at runtime:
 *
 *   - the file field is `image`, not `photo`
 *   - the service mints its own `request_id` and ignores any we send, so ours
 *     is a local correlation id until the response tells us the real one
 *   - `top_k` and `debug` are query-string parameters, not body fields
 */
class RcuService
{
    /**
     * Identify a remote from image bytes.
     *
     * Returns the decoded /identify response. A successful call may still
     * report confidence "none" -- that is an answer, not a failure.
     *
     * @throws RcuServiceException when the service cannot be reached or errors
     */
    public function identify(string $contents, string $filename = 'photo.jpg',
                             ?int $topK = null, ?bool $debug = null): array
    {
        $query = [
            'top_k' => $topK ?? config('rcu.top_k'),
            'debug' => ($debug ?? config('rcu.debug_overlays')) ? 'true' : 'false',
        ];

        return $this->send('identify', fn () => $this->request()
            ->attach('image', $contents, $filename)
            ->post($this->url('/identify') . '?' . http_build_query($query)));
    }

    /**
     * Raw extraction with no matching, for the admin visualiser.
     *
     * Defaults to the offline build's settings, so what comes back is what the
     * catalog would have stored for this image -- which is exactly the
     * comparison you want when a query and its catalog record disagree.
     */
    public function fingerprint(string $contents, string $filename = 'photo.jpg',
                                bool $ensemble = true, bool $ocr = true): array
    {
        $query = [
            'ensemble' => $ensemble ? 'true' : 'false',
            'ocr' => $ocr ? 'true' : 'false',
        ];

        return $this->send('fingerprint', fn () => $this->request()
            ->attach('image', $contents, $filename)
            ->post($this->url('/fingerprint') . '?' . http_build_query($query)));
    }

    /** Reload the token index after a catalog rebuild. */
    public function reindex(): array
    {
        return $this->send('reindex', fn () => $this->request()->post($this->url('/reindex')));
    }

    /**
     * Service health. Never throws -- a dashboard asking "is it up?" must not
     * itself fail when the answer is no.
     */
    public function health(): array
    {
        try {
            $response = Http::timeout(5)->get($this->url('/health'));

            return $response->successful()
                ? $response->json()
                : ['status' => 'unavailable', 'error' => "HTTP {$response->status()}"];
        } catch (ConnectionException $e) {
            return ['status' => 'unavailable', 'error' => $e->getMessage()];
        }
    }

    /**
     * The debug overlay for a past request, as JPEG bytes, or null.
     *
     * The service keeps these in a bounded in-memory ring, so this returns
     * null for anything older than the last few requests. That is expected;
     * callers must not treat it as an error.
     */
    public function debugOverlay(string $requestId): ?string
    {
        try {
            $response = Http::timeout(10)->get($this->url('/debug/' . urlencode($requestId)));

            return $response->successful() ? $response->body() : null;
        } catch (ConnectionException) {
            return null;
        }
    }

    /**
     * Run a request and normalise every failure mode to RcuServiceException.
     *
     * `retry(throw: false)` suppresses throwing on an HTTP error *response*,
     * but a transport failure has no response: `retry()` re-throws the
     * ConnectionException once attempts are exhausted, whatever that flag
     * says. Without this the one failure that matters most -- the service
     * being down -- escapes as the wrong exception type and the controller's
     * 503 handling never runs.
     *
     * @throws RcuServiceException
     */
    private function send(string $what, callable $call): array
    {
        try {
            return $this->decode($call(), $what);
        } catch (ConnectionException $e) {
            Log::warning("rcu {$what} unreachable", ['error' => $e->getMessage()]);

            throw new RcuServiceException(
                "recognition service unreachable: {$e->getMessage()}", null, $e
            );
        } catch (RequestException $e) {
            throw new RcuServiceException(
                "recognition service {$what} failed: {$e->getMessage()}",
                $e->response?->status(),
                $e
            );
        }
    }

    private function request(): PendingRequest
    {
        $request = Http::timeout(config('rcu.timeout'))
            ->connectTimeout(5)
            ->retry(
                config('rcu.retries'),
                config('rcu.retry_delay_ms'),
                // Retry transport failures and 5xx, never 4xx: a rejected
                // upload is rejected the same way every time.
                fn ($e) => $e instanceof ConnectionException
                    || ($e instanceof RequestException && $e->response->serverError()),
                throw: false
            );

        $token = config('rcu.service_token');

        return $token ? $request->withHeaders(['X-Internal-Token' => $token]) : $request;
    }

    private function url(string $path): string
    {
        return rtrim(config('rcu.service_url'), '/') . $path;
    }

    /**
     * @throws RcuServiceException
     */
    private function decode($response, string $what): array
    {
        if ($response->failed()) {
            // The service reports its own reason in `detail`; keep it, because
            // "index not loaded" and "image too large" need different fixes.
            $detail = $response->json('detail') ?? $response->body();
            Log::warning("rcu {$what} failed", [
                'status' => $response->status(),
                'detail' => $detail,
            ]);

            throw new RcuServiceException(
                "recognition service {$what} failed: {$detail}",
                $response->status()
            );
        }

        $decoded = $response->json();

        if (! is_array($decoded)) {
            throw new RcuServiceException("recognition service {$what} returned no JSON");
        }

        return $decoded;
    }
}
