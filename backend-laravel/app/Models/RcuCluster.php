<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;
use Illuminate\Database\Eloquent\Relations\BelongsToMany;

/**
 * A physical-mould cluster: remotes that are the same object sold under
 * different brands (plan 3.11).
 *
 * The fingerprint cannot separate cluster members, because there is nothing
 * to separate -- same mould, same buttons, same geometry. A match should
 * answer with the mould and let the user pick the branding.
 */
class RcuCluster extends Model
{
    use HasFactory;

    protected $table = 'rcu_clusters';

    protected $fillable = ['canonical_fp_id', 'member_count'];

    public function canonical(): BelongsTo
    {
        return $this->belongsTo(RcuFingerprint::class, 'canonical_fp_id');
    }

    public function members(): BelongsToMany
    {
        return $this->belongsToMany(
            RcuFingerprint::class, 'rcu_cluster_members', 'cluster_id', 'fingerprint_id'
        );
    }
}
