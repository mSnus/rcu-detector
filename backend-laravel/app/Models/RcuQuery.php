<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;

/**
 * One recognition request and what came back.
 *
 * Rows with `chosen_record_id` or `none_of_these` set are labelled training
 * pairs (plan 6.4). Collecting them costs nothing and they are the only
 * source of real-world ground truth this project will ever have.
 */
class RcuQuery extends Model
{
    use HasFactory;

    protected $table = 'rcu_queries';

    protected $fillable = [
        'request_id', 'upload_path', 'candidates', 'extracted', 'top_score',
        'top_record_id', 'confidence', 'error', 'hint', 'chosen_record_id',
        'none_of_these', 'answered_at', 'latency_ms', 'bodies_found',
        'model_code_fast_path',
    ];

    protected function casts(): array
    {
        return [
            'candidates' => 'array',
            'extracted' => 'array',
            'top_score' => 'float',
            'none_of_these' => 'boolean',
            'model_code_fast_path' => 'boolean',
            'answered_at' => 'datetime',
        ];
    }

    public function getRouteKeyName(): string
    {
        return 'request_id';
    }

    public function chosenFingerprint(): BelongsTo
    {
        return $this->belongsTo(RcuFingerprint::class, 'chosen_record_id', 'record_id');
    }

    public function topFingerprint(): BelongsTo
    {
        return $this->belongsTo(RcuFingerprint::class, 'top_record_id', 'record_id');
    }

    /**
     * Record the service's answer.
     *
     * Everything here comes straight off the /identify response. `extracted`
     * is null when no remote was found at all, which is a distinct outcome
     * from a low-scoring match and is stored as such.
     */
    public function recordResult(array $result): self
    {
        $candidates = $result['candidates'] ?? [];
        $top = $candidates[0] ?? null;

        $this->update([
            'candidates' => $candidates,
            'extracted' => $result['extracted'] ?? null,
            'top_score' => $top['score'] ?? null,
            'top_record_id' => $top['record_id'] ?? null,
            'confidence' => $result['confidence'] ?? 'none',
            'hint' => $result['hint'] ?? null,
            'latency_ms' => $result['latency_ms'] ?? null,
            'bodies_found' => $result['bodies_found'] ?? null,
            'model_code_fast_path' => (bool) ($result['model_code_fast_path'] ?? false),
        ]);

        return $this;
    }

    /** True once the user has told us something, either way. */
    public function isAnswered(): bool
    {
        return $this->answered_at !== null;
    }

    /**
     * Answered queries where the user's pick was not our top candidate.
     * This is the set worth looking at in the admin visualiser.
     */
    public function scopeMisses($query)
    {
        return $query->whereNotNull('answered_at')
            ->where(function ($q) {
                $q->where('none_of_these', true)
                    ->orWhereColumn('chosen_record_id', '!=', 'top_record_id');
            });
    }
}
