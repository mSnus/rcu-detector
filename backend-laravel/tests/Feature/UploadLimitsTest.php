<?php

namespace Tests\Feature;

use Tests\TestCase;

/**
 * PHP's upload ceiling must stay above the application's.
 *
 * When it is below, the application's limit never applies and its error message
 * never runs: PHP discards the file before Laravel sees the request, validation
 * reports "The photo failed to upload", and /try tells the user to take another
 * photo -- which fails identically. That shipped, on a page whose only input is
 * a phone photograph, with the image default of 2M against a 10 MB app limit.
 *
 * This reads the ini the image ships rather than the running php.ini, because
 * the suite runs on the host and the value that matters is the container's.
 */
class UploadLimitsTest extends TestCase
{
    private function shippedIni(): array
    {
        $path = base_path('docker/uploads.ini');
        $this->assertFileExists($path, 'the image ships no upload ini');

        return parse_ini_file($path) ?: [];
    }

    private static function toKb(string $value): int
    {
        $unit = strtoupper(substr(trim($value), -1));
        $n = (int) $value;

        return match ($unit) {
            'G' => $n * 1024 * 1024,
            'M' => $n * 1024,
            'K' => $n,
            default => (int) ($n / 1024),
        };
    }

    public function test_php_accepts_a_file_at_the_application_limit(): void
    {
        $ini = $this->shippedIni();
        $appKb = (int) config('rcu.max_upload_kb');

        $this->assertGreaterThan(
            $appKb,
            self::toKb($ini['upload_max_filesize']),
            'upload_max_filesize is below RCU_MAX_UPLOAD_KB, so the app limit never applies'
        );
    }

    public function test_the_whole_multipart_body_fits(): void
    {
        // post_max_size covers the file plus boundaries and other fields, so
        // equal to upload_max_filesize means a file at the limit is rejected by
        // the outer check, with the less useful message.
        $ini = $this->shippedIni();

        $this->assertGreaterThan(
            self::toKb($ini['upload_max_filesize']),
            self::toKb($ini['post_max_size']),
            'post_max_size must exceed upload_max_filesize'
        );
    }

    public function test_nginx_accepts_at_least_as_much_as_php(): void
    {
        // The outermost limit. If nginx is the tightest, the request is refused
        // with an HTML 413 that no application code can turn into a message.
        $conf = file_get_contents(base_path('../docker/nginx.conf'));
        $this->assertSame(1, preg_match('/client_max_body_size\s+(\d+)m/i', $conf, $m),
            'nginx.conf declares no client_max_body_size in megabytes');

        $ini = $this->shippedIni();
        $this->assertGreaterThanOrEqual(
            self::toKb($ini['post_max_size']),
            ((int) $m[1]) * 1024,
            'client_max_body_size is below post_max_size'
        );
    }
}
