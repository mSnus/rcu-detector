<?php

namespace App\Support;

use Illuminate\Support\Facades\Storage;
use Illuminate\Support\Str;

/**
 * Copy a query's upload into the support folder, downscaled.
 *
 * Not a thumbnail and not a re-encode for its own sake. Three things are
 * wanted, and only the first is about size:
 *
 *   - a support agent should not be sent a 40 MP phone photograph;
 *   - the stored name must be ours. The upload arrived from an unauthenticated
 *     page and its filename is caller-supplied text;
 *   - the copy is independent of the query log, which is prunable. A support
 *     request outlives the query that raised it.
 *
 * GD rather than the Python service: this is image *I/O*, not computer
 * vision, and routing it through the recogniser would make a customer's
 * support request depend on the recogniser being up. The pipeline's rule is
 * that recognition never happens in PHP -- resizing a JPEG is not that.
 *
 * Never fatal. A request whose image could not be copied is still a request,
 * and a person on the phone is a better outcome than a 500.
 */
class SupportImage
{
    public static function store(?string $uploadPath): ?string
    {
        if (! $uploadPath) {
            return null;
        }

        $from = Storage::disk(config('rcu.upload_disk'));
        if (! $from->exists($uploadPath)) {
            return null;
        }

        $max = (int) config('rcu.support.max_side', 2000);
        $name = date('Y/m/') . Str::uuid()->toString() . '.jpg';
        $disk = Storage::disk(config('rcu.support.disk'));

        try {
            $raw = $from->get($uploadPath);
            $out = self::downscale($raw, $max);
        } catch (\Throwable $e) {
            report($e);
            $out = null;
        }

        // Falling back to the original bytes rather than to nothing: an
        // oversized photograph reaching support beats no photograph at all,
        // which is the whole point of the form.
        $disk->put($name, $out ?? ($raw ?? ''));

        return $name;
    }

    /**
     * Longest side to at most $max, aspect preserved. Null when GD cannot
     * read it, which the caller treats as "send the original".
     */
    public static function downscale(string $raw, int $max): ?string
    {
        if (! function_exists('imagecreatefromstring')) {
            return null;
        }

        $img = @imagecreatefromstring($raw);
        if ($img === false) {
            return null;
        }

        $w = imagesx($img);
        $h = imagesy($img);
        if ($w <= 0 || $h <= 0) {
            imagedestroy($img);

            return null;
        }

        // Already small enough: re-encoding would cost a generation of JPEG
        // quality for nothing.
        if (max($w, $h) <= $max) {
            imagedestroy($img);

            return null;
        }

        $scale = $max / max($w, $h);
        $nw = max(1, (int) round($w * $scale));
        $nh = max(1, (int) round($h * $scale));

        $dst = imagecreatetruecolor($nw, $nh);
        // Photographs from a phone are opaque; flattening onto white keeps a
        // transparent PNG from arriving as a black rectangle.
        imagefilledrectangle($dst, 0, 0, $nw, $nh, imagecolorallocate($dst, 255, 255, 255));
        imagecopyresampled($dst, $img, 0, 0, 0, 0, $nw, $nh, $w, $h);

        ob_start();
        imagejpeg($dst, null, (int) config('rcu.support.jpeg_quality', 85));
        $out = (string) ob_get_clean();

        imagedestroy($img);
        imagedestroy($dst);

        return $out !== '' ? $out : null;
    }
}
