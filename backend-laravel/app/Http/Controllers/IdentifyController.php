<?php

namespace App\Http\Controllers;

use App\Models\RcuFingerprint;
use App\Models\RcuQuery;
use App\Services\RcuService;
use App\Services\RcuServiceException;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Storage;
use Illuminate\Support\Str;

class IdentifyController extends Controller
{
    public function __construct(private readonly RcuService $service)
    {
    }

    /**
     * Accept a photo, identify the remote, log the query.
     *
     * The query row is written before the service is called and updated after,
     * so a request that crashes the service still leaves evidence of what was
     * uploaded. Those are the interesting ones.
     */
    public function store(Request $request): JsonResponse
    {
        $request->validate([
            'photo' => [
                'required', 'file', 'image',
                'mimes:jpeg,jpg,png,webp',
                'max:' . config('rcu.max_upload_kb'),
            ],
        ]);

        $file = $request->file('photo');
        $path = $file->store('uploads', config('rcu.upload_disk'));

        $query = RcuQuery::create([
            // Replaced below by the service's own id, which is what
            // /debug/{request_id} is keyed on. Until then this is only a local
            // correlation id, so the row is never without one.
            'request_id' => (string) Str::uuid(),
            'upload_path' => $path,
        ]);

        try {
            $result = $this->service->identify(
                Storage::disk(config('rcu.upload_disk'))->get($path),
                $file->getClientOriginalName() ?: 'photo.jpg',
            );
        } catch (RcuServiceException $e) {
            report($e);

            // A rejected image is a verdict on the upload and retrying it
            // changes nothing, so it must not be dressed up as an outage.
            if ($e->isRejection()) {
                return response()->json([
                    'error' => 'image_rejected',
                    'message' => 'The recognition service could not read that image.',
                    'request_id' => $query->request_id,
                ], 422);
            }

            return response()->json([
                'error' => 'recognition_unavailable',
                'message' => 'The recognition service is not available. Try again shortly.',
                'request_id' => $query->request_id,
            ], 503);
        }

        if (! empty($result['request_id'])) {
            $query->request_id = $result['request_id'];
        }
        $query->save();
        $query->recordResult($result);

        return response()->json($this->present($query->fresh(), $result));
    }

    /**
     * Record which candidate the user picked.
     *
     * Every tap is a labelled training pair, and "none of these" is the most
     * informative answer of the lot -- it says the catalog is missing the
     * remote, or the extraction was wrong, which no positive pick ever does.
     */
    public function choose(Request $request, string $requestId): JsonResponse
    {
        $validated = $request->validate([
            'record_id' => ['nullable', 'string', 'max:191'],
            'none_of_these' => ['boolean'],
        ]);

        $query = RcuQuery::where('request_id', $requestId)->firstOrFail();

        $none = (bool) ($validated['none_of_these'] ?? false);
        $recordId = $validated['record_id'] ?? null;

        if (! $none && $recordId === null) {
            return response()->json([
                'error' => 'nothing_chosen',
                'message' => 'Provide record_id, or none_of_these=true.',
            ], 422);
        }

        $query->update([
            'chosen_record_id' => $none ? null : $recordId,
            'none_of_these' => $none,
            'answered_at' => now(),
        ]);

        return response()->json(['status' => 'ok']);
    }

    /** A past query and its result. */
    public function show(string $requestId): JsonResponse
    {
        $query = RcuQuery::where('request_id', $requestId)->firstOrFail();

        return response()->json($this->present($query, [
            'candidates' => $query->candidates ?? [],
            'extracted' => $query->extracted,
            'confidence' => $query->confidence,
            'latency_ms' => $query->latency_ms,
            'hint' => $query->hint,
        ]));
    }

    /**
     * Shape the answer for a client.
     *
     * `hint` is passed through deliberately: when confidence is none the
     * service says why ("reshoot"), and that is the only actionable thing a
     * user can be told.
     */
    private function present(RcuQuery $query, array $result): array
    {
        $candidates = $result['candidates'] ?? [];
        $catalog = $this->catalogFor($candidates);

        return [
            'request_id' => $query->request_id,
            'confidence' => $result['confidence'] ?? 'none',
            'hint' => $result['hint'] ?? null,
            'latency_ms' => $result['latency_ms'] ?? null,
            'extracted' => $result['extracted'] ?? null,
            'candidates' => collect($candidates)
                ->map(fn (array $c) => [
                    'record_id' => $c['record_id'] ?? null,
                    'score' => $c['score'] ?? null,
                    'inliers' => $c['inliers'] ?? null,
                    'brand' => $c['brand'] ?? null,
                    'model_code' => $c['model_code'] ?? null,
                    'terms' => $c['terms'] ?? null,
                    // Both orientations are reported because a match against a
                    // record indexed upside down is still a match, and a
                    // client showing the crop needs to know which way up.
                    'orientation' => $c['orientation'] ?? null,
                    'catalog' => $catalog->get($c['record_id'] ?? ''),
                ])->all(),
        ];
    }

    /**
     * Resolve candidate record_ids to catalog rows, in one query.
     *
     * A record_id is the fingerprint stem and means nothing to a client. The
     * row carries what a person needs to see -- which photograph it came from,
     * how good the extraction was, whether anyone has checked it.
     *
     * A missing row is returned as null rather than skipped or faked. It means
     * the service's index and this table were built from different extraction
     * runs, and that is worth surfacing rather than hiding behind a blank
     * name: the match itself is still valid, it just refers to a record the
     * catalog no longer lists.
     *
     * @param  array<int, array>  $candidates
     * @return \Illuminate\Support\Collection<string, array>
     */
    private function catalogFor(array $candidates): \Illuminate\Support\Collection
    {
        $ids = collect($candidates)->pluck('record_id')->filter()->unique()->all();

        if ($ids === []) {
            return collect();
        }

        return RcuFingerprint::whereIn('record_id', $ids)
            ->get(['record_id', 'model_id', 'source_image', 'button_count',
                   'quality_score', 'reviewed', 'brand_text', 'model_text'])
            ->keyBy('record_id')
            ->map(fn (RcuFingerprint $f) => [
                'model_id' => $f->model_id,
                'source_image' => $f->source_image,
                'button_count' => $f->button_count,
                'quality_score' => round($f->quality_score, 3),
                'reviewed' => $f->reviewed,
                'brand' => $f->brand_text,
                'model_code' => $f->model_text,
            ]);
    }
}
