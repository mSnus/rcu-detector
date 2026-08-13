<?php

namespace App\Http\Controllers;

use App\Mail\SupportRequestMail;
use App\Models\RcuFingerprint;
use App\Models\RcuQuery;
use App\Models\RcuSupportRequest;
use App\Support\SupportGateway;
use App\Support\SupportImage;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Illuminate\Http\Response;
use Illuminate\Support\Facades\Mail;
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
            'simple' => (bool) config('rcu.try_simple'),
            'supportForm' => (bool) config('rcu.support.enabled'),
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

    /**
     * "Have a person look at this."
     *
     * Offered whatever the matcher said. `high` is 100% precise when the
     * remote is in the catalogue, but when it is absent the matcher returns
     * the nearest sibling at high confidence about 45% of the time, and the
     * customer has no way to tell those two apart. So the way to reach a human
     * is not gated on the band.
     *
     * The photograph is taken from the query that was already uploaded, never
     * re-uploaded: it is on the server already, and asking a phone to send a
     * ten-megabyte photograph twice on a mobile connection is the kind of
     * thing that loses the request.
     *
     * Written first, delivered second, and the outcome of delivery recorded on
     * the row. Neither the e-mail nor the upstream API may fail the request:
     * the customer has given a name and a telephone number, and that is the
     * part that must survive.
     */
    public function support(Request $request): JsonResponse
    {
        $this->guard();
        abort_unless((bool) config('rcu.support.enabled'), 404);

        $data = $request->validate([
            'request_id' => ['nullable', 'string', 'max:64'],
            'name' => ['required', 'string', 'max:120'],
            'phone' => ['required', 'string', 'max:64'],
        ]);

        $query = ! empty($data['request_id'])
            ? RcuQuery::where('request_id', $data['request_id'])->first()
            : null;

        $top = $query?->top_record_id;

        $req = RcuSupportRequest::create([
            'request_id' => $data['request_id'] ?? null,
            'name' => trim($data['name']),
            'phone' => trim($data['phone']),
            'image_path' => SupportImage::store($query?->upload_path),
            'confidence' => $query?->confidence,
            'top_record_id' => $top,
            'top_title' => $top
                ? RcuFingerprint::where('record_id', $top)->value('title')
                : null,
        ]);

        $to = config('rcu.support.email');

        // `log`, `array` and `null` are not delivery. Mail::send() returns
        // perfectly happily on all three, so stamping emailed_at from a
        // successful call would record a message that nobody received -- the
        // exact shape of silent failure this project keeps finding. The
        // message is still handed to the mailer, because on `log` that is
        // how you read what would have been sent; only the claim to have
        // delivered it is withheld.
        $transport = (string) config('mail.default');
        $delivers = ! in_array($transport, ['log', 'array', 'null'], true);

        if (! $to) {
            $req->delivery_error = trim(($req->delivery_error ?? '')
                . ' mail:no-address-configured');
        } else {
            try {
                Mail::to($to)->send(new SupportRequestMail($req));
                if ($delivers) {
                    $req->emailed_at = now();
                } else {
                    $req->delivery_error = trim(($req->delivery_error ?? '')
                        . " mail:not-delivered-transport-is-{$transport}");
                }
            } catch (\Throwable $e) {
                report($e);
                $req->delivery_error = trim(($req->delivery_error ?? '')
                    . ' mail:' . substr($e->getMessage(), 0, 120));
            }
        }

        if (SupportGateway::forward($req)) {
            $req->forwarded_at = now();
        }

        $req->save();

        return response()->json(['ok' => true, 'id' => $req->id]);
    }
}
