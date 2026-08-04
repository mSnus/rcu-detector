<?php

namespace App\Http\Controllers;

use App\Models\RcuFingerprint;
use Illuminate\Http\Response;
use Illuminate\View\View;

/**
 * The test page: photograph a remote, see what comes back.
 *
 * Deliberately thin. All the work is already done by `/api/identify` and
 * `/api/identify/{id}/choose`, and this page calls them exactly as any other
 * client would -- if it took a shortcut through the service directly it would
 * stop being a test of the thing that ships.
 *
 * Unauthenticated, and therefore gated on `rcu.try_page`. See config/rcu.php.
 */
class TryController extends Controller
{
    /**
     * 404, not 403: a box that has not enabled this page should not advertise
     * that it exists.
     */
    private function guard(): void
    {
        abort_unless((bool) config('rcu.try_page'), 404);
    }

    public function index(): View
    {
        $this->guard();

        return view('rcu.try', [
            'maxUploadKb' => (int) config('rcu.max_upload_kb'),
            'catalogSize' => RcuFingerprint::count(),
        ]);
    }

    /**
     * The rectified crop for a candidate.
     *
     * The page has to show the user what it thinks their remote is, and the
     * crop is the closest thing the catalog holds to a picture of one record.
     * Same guards as the admin route it mirrors: the id is confirmed against
     * the database before it reaches the filesystem, and `basename` is applied
     * regardless, because a route parameter must never be concatenated into a
     * path on trust.
     */
    public function photo(string $recordId): Response
    {
        $this->guard();

        if (! RcuFingerprint::where('record_id', $recordId)->exists()) {
            abort(404, 'No such catalog record.');
        }

        $path = rtrim((string) config('rcu.catalog.norm_dir'), '/')
            . '/' . basename($recordId) . '.jpg';

        abort_unless(is_file($path), 404, 'No crop for that record.');

        return response((string) file_get_contents($path), 200, [
            'Content-Type' => 'image/jpeg',
            'Cache-Control' => 'private, max-age=300',
        ]);
    }
}
