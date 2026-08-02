<?php

namespace App\Http\Controllers;

use App\Models\RcuFingerprint;
use App\Models\RcuQuery;
use App\Services\RcuService;
use App\Services\RcuServiceException;
use Illuminate\Http\Request;
use Illuminate\Http\Response;
use Illuminate\Support\Facades\Storage;
use Illuminate\View\View;

/**
 * The admin visualiser (plan 6.5).
 *
 * When a match is wrong this tells you in seconds whether the cause was
 * detection, colour, OCR or scoring. Essentially every bug found in this
 * project so far was found by looking at an overlay rather than by reasoning
 * about the code, which is why this is a first-class page and not a debug
 * afterthought.
 */
class AdminVisualiserController extends Controller
{
    public function __construct(private readonly RcuService $service)
    {
    }

    /** Recent queries, worst first -- misses are what repay looking at. */
    public function index(Request $request): View
    {
        $filter = $request->query('filter', 'recent');

        $queries = RcuQuery::query()
            ->when($filter === 'misses', fn ($q) => $q->misses())
            ->when($filter === 'low', fn ($q) => $q->whereIn('confidence', ['low', 'none']))
            ->latest('id')
            ->limit(50)
            ->get();

        return view('admin.rcu.index', [
            'queries' => $queries,
            'filter' => $filter,
            'health' => $this->service->health(),
        ]);
    }

    /** One query: the photo, what was extracted, and every candidate's terms. */
    public function show(string $requestId): View
    {
        $query = RcuQuery::where('request_id', $requestId)->firstOrFail();

        return view('admin.rcu.show', [
            'query' => $query,
            'candidates' => $query->candidates ?? [],
            // Asked for lazily: the service keeps overlays in a small bounded
            // ring, so this is null for anything but the last few requests.
            'hasOverlay' => $this->service->debugOverlay($requestId) !== null,
        ]);
    }

    /**
     * Proxy the uploaded photo.
     *
     * Served through the app rather than from a public disk: these are user
     * photographs, and they stay unreachable without an authenticated route.
     */
    public function upload(string $requestId): Response
    {
        $query = RcuQuery::where('request_id', $requestId)->firstOrFail();
        $disk = Storage::disk(config('rcu.upload_disk'));

        abort_unless($disk->exists($query->upload_path), 404);

        return response($disk->get($query->upload_path), 200, [
            'Content-Type' => $disk->mimeType($query->upload_path) ?: 'image/jpeg',
            'Cache-Control' => 'private, max-age=300',
        ]);
    }

    /** Proxy the service's debug overlay for a request. */
    public function overlay(string $requestId): Response
    {
        $jpeg = $this->service->debugOverlay($requestId);

        abort_if($jpeg === null, 404, 'No overlay retained for that request.');

        return response($jpeg, 200, [
            'Content-Type' => 'image/jpeg',
            'Cache-Control' => 'private, max-age=300',
        ]);
    }

    /**
     * The catalog, worst extraction first.
     *
     * Ordering by quality ascending is not a cosmetic choice: the bottom of
     * this list is the review queue (plan 3.10), and every extraction bug
     * found so far was found by opening the worst-scoring overlay.
     */
    public function catalog(Request $request): View
    {
        $filter = $request->query('filter', 'all');
        $search = trim((string) $request->query('q', ''));

        $records = RcuFingerprint::query()
            ->when($filter === 'review',
                fn ($q) => $q->needsReview(config('rcu.catalog.review_below')))
            ->when($filter === 'brandless', fn ($q) => $q->whereNull('brand_text'))
            ->when($filter === 'ambiguous',
                // Orientation is the failure that corrupts a record silently,
                // so it gets its own filter rather than hiding in the tail.
                fn ($q) => $q->where('orientation_conf', '<', 0.5))
            ->when($search !== '', fn ($q) => $q->where(function ($w) use ($search) {
                $w->where('record_id', 'like', "%{$search}%")
                    ->orWhere('brand_text', 'like', "%{$search}%")
                    ->orWhere('model_text', 'like', "%{$search}%")
                    // The catalogue title is the name an operator actually
                    // knows the record by; OCR often reads no brand at all.
                    ->orWhere('title', 'like', "%{$search}%");
            }))
            ->orderBy('quality_score')
            ->limit(200)
            ->get();

        return view('admin.rcu.catalog', [
            'records' => $records,
            'filter' => $filter,
            'search' => $search,
            'health' => $this->service->health(),
            'total' => RcuFingerprint::count(),
            'reviewBelow' => config('rcu.catalog.review_below'),
        ]);
    }

    /** One catalog record: its crop, its build overlay, its fingerprint. */
    public function record(string $recordId): View
    {
        $record = RcuFingerprint::where('record_id', $recordId)->firstOrFail();

        return view('admin.rcu.record', [
            'record' => $record,
            'health' => $this->service->health(),
            'hasCrop' => $this->catalogFile('norm_dir', $record->record_id) !== null,
            'hasOverlay' => $this->catalogFile('debug_dir', $record->record_id) !== null,
        ]);
    }

    /**
     * Mark an extraction as checked by a person.
     *
     * `reviewed` is one of only two columns a human owns rather than the
     * extractor, and `rcu:import-catalog` preserves it across rebuilds for
     * exactly that reason.
     */
    public function review(Request $request, string $recordId)
    {
        $record = RcuFingerprint::where('record_id', $recordId)->firstOrFail();

        $record->update(['reviewed' => ! $request->boolean('unreview')]);

        return back()->with('status', $record->reviewed
            ? "{$recordId} marked reviewed"
            : "{$recordId} returned to the queue");
    }

    /** The rectified crop the fingerprint was actually built from. */
    public function crop(string $recordId): Response
    {
        return $this->serveCatalogImage('norm_dir', $recordId);
    }

    /**
     * The build-time overlay for a catalog record.
     *
     * Distinct from `overlay()`, which proxies the *query* overlay out of the
     * service's in-memory ring. This one is the file the extraction run wrote
     * and it does not expire, which is why it is worth serving separately.
     */
    public function buildOverlay(string $recordId): Response
    {
        return $this->serveCatalogImage('debug_dir', $recordId);
    }

    /**
     * Resolve a catalog artefact path, or null.
     *
     * The record id is confirmed against the database before it reaches the
     * filesystem, and `basename` is applied regardless. A route parameter must
     * never be concatenated into a path on trust.
     */
    private function catalogFile(string $configKey, string $recordId): ?string
    {
        if (! RcuFingerprint::where('record_id', $recordId)->exists()) {
            return null;
        }

        $path = rtrim(config("rcu.catalog.{$configKey}"), '/')
            . '/' . basename($recordId) . '.jpg';

        return is_file($path) ? $path : null;
    }

    private function serveCatalogImage(string $configKey, string $recordId): Response
    {
        $path = $this->catalogFile($configKey, $recordId);

        abort_if($path === null, 404, 'No such catalog image.');

        return response((string) file_get_contents($path), 200, [
            'Content-Type' => 'image/jpeg',
            'Cache-Control' => 'private, max-age=300',
        ]);
    }

    /**
     * Ad-hoc extraction: upload a photo, see what the catalog build would
     * store for it. This is the comparison to reach for when a query and its
     * catalog record disagree, since the two take different paths through the
     * same extraction code and a bug can live on one side only.
     */
    public function inspect(Request $request): View
    {
        $request->validate([
            'photo' => ['required', 'file', 'image', 'mimes:jpeg,jpg,png,webp',
                        'max:' . config('rcu.max_upload_kb')],
        ]);

        $file = $request->file('photo');

        try {
            $result = $this->service->fingerprint(
                $file->get(), $file->getClientOriginalName() ?: 'photo.jpg'
            );
            $error = null;
        } catch (RcuServiceException $e) {
            $result = null;
            $error = $e->getMessage();
        }

        return view('admin.rcu.inspect', ['result' => $result, 'error' => $error]);
    }
}
