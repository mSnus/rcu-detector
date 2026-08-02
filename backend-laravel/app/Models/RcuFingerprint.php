<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsToMany;

/**
 * One catalog record: a single remote crop and the fingerprint extracted
 * from it.
 *
 * `record_id` is the join key to everything the Python service says. The
 * service never returns a database id, only the fingerprint stem.
 */
class RcuFingerprint extends Model
{
    use HasFactory;

    protected $table = 'rcu_fingerprints';

    protected $fillable = [
        'record_id', 'model_id', 'source_image', 'crop_index', 'norm_path',
        'aspect_ratio', 'button_count', 'fingerprint', 'brand_text',
        'model_text', 'title', 'quality_score', 'reviewed',
        'orientation_flipped', 'orientation_conf', 'built_at',
    ];

    protected function casts(): array
    {
        return [
            'fingerprint' => 'array',
            'aspect_ratio' => 'float',
            'quality_score' => 'float',
            'orientation_conf' => 'float',
            'reviewed' => 'boolean',
            'orientation_flipped' => 'boolean',
            'built_at' => 'datetime',
        ];
    }

    public function clusters(): BelongsToMany
    {
        return $this->belongsToMany(
            RcuCluster::class, 'rcu_cluster_members', 'fingerprint_id', 'cluster_id'
        );
    }

    /**
     * Build a row from the service's fingerprint JSON.
     *
     * The shape is service-python's, not the plan's -- `stats` and
     * `extract_quality` are real, `corner_r` is not -- so read it from the
     * fingerprint rather than trusting plan 2.2.
     */
    public static function fromServiceFingerprint(
        string $recordId,
        array $fp,
        string $sourceImage,
        string $normPath,
        int $cropIndex = 0
    ): self {
        $stats = $fp['stats'] ?? [];

        return new self([
            'record_id' => $recordId,
            'source_image' => $sourceImage,
            'crop_index' => $cropIndex,
            'norm_path' => $normPath,
            'aspect_ratio' => $fp['body']['aspect'] ?? 0.0,
            'button_count' => $stats['n_buttons'] ?? count($fp['buttons'] ?? []),
            'fingerprint' => $fp,
            'brand_text' => $fp['brand'] ?? null,
            'model_text' => $fp['model_code'] ?? null,
            'quality_score' => $fp['extract_quality'] ?? 0.0,
            'orientation_flipped' => (bool) ($stats['orientation_flipped'] ?? false),
            'orientation_conf' => (float) ($stats['orientation_conf'] ?? 0.0),
        ]);
    }

    /**
     * Link back to this record on the source catalogue, or null.
     *
     * Built from `model_id` (the source node id) and a config template rather
     * than stored per row, so moving domain is a config edit. Null when the
     * record was not imported from the catalogue -- the original sample
     * photos have no item page.
     */
    public function itemUrl(): ?string
    {
        $template = config('rcu.catalog.item_url');

        return ($template && $this->model_id)
            ? str_replace('{id}', (string) $this->model_id, $template)
            : null;
    }

    /** Worst extractions first -- the review queue (plan 3.10). */
    public function scopeNeedsReview($query, float $below = 0.75)
    {
        return $query->where('reviewed', false)
            ->where('quality_score', '<', $below)
            ->orderBy('quality_score');
    }
}
