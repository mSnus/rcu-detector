<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

/**
 * Physical-mould clusters (plan 2.1, 3.11).
 *
 * Several brands routinely sell the identical remote under different names,
 * and the fingerprint cannot tell them apart because there is nothing to tell
 * apart -- same mould, same buttons, same geometry. Grouping them means a
 * match can answer "this mould, sold as any of these" instead of picking one
 * arbitrarily and being wrong most of the time.
 */
return new class extends Migration
{
    public function up(): void
    {
        Schema::create('rcu_clusters', function (Blueprint $table) {
            $table->id();
            // The member whose extraction is best; the one to show a user.
            $table->foreignId('canonical_fp_id')
                ->constrained('rcu_fingerprints')
                ->cascadeOnDelete();
            $table->unsignedSmallInteger('member_count')->default(0);
            $table->timestamps();
        });

        Schema::create('rcu_cluster_members', function (Blueprint $table) {
            $table->foreignId('cluster_id')
                ->constrained('rcu_clusters')
                ->cascadeOnDelete();
            $table->foreignId('fingerprint_id')
                ->constrained('rcu_fingerprints')
                ->cascadeOnDelete();
            $table->primary(['cluster_id', 'fingerprint_id']);
            // A fingerprint belongs to at most one cluster, so this is the
            // lookup that actually runs at query time.
            $table->index('fingerprint_id');
        });
    }

    public function down(): void
    {
        Schema::dropIfExists('rcu_cluster_members');
        Schema::dropIfExists('rcu_clusters');
    }
};
