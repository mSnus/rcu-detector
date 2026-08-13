<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

/**
 * A person asking a human to identify their remote.
 *
 * Raised from `/try` when the matcher is unsure, and also when it is sure --
 * `high` is 100% precise when the remote is in the catalogue, but when it is
 * absent the matcher returns the nearest sibling at high confidence about 45%
 * of the time (session 9). Neither band is a reason to withhold a way of
 * reaching a person.
 *
 * The row is the record of intent and is written first. Delivery -- e-mail to
 * support, and the upstream ticket API -- is recorded on it rather than
 * assumed: a request that failed to send must be visible as a request that
 * failed to send, not missing.
 */
return new class extends Migration
{
    public function up(): void
    {
        Schema::create('rcu_support_requests', function (Blueprint $table) {
            $table->id();

            // The query this was raised from, by the id the service issued.
            // Nullable and not a foreign key on purpose: the query row is a
            // log, and losing it must not cost a customer's request.
            $table->string('request_id', 64)->nullable()->index();

            $table->string('name', 120);
            $table->string('phone', 64);

            // Relative to the support disk. The original upload stays where it
            // was; this is a downscaled copy under a name of our choosing, so
            // that what support receives cannot be a 40 MP file and cannot
            // carry a caller-supplied filename.
            $table->string('image_path', 255)->nullable();

            // What the matcher said at the moment they asked, denormalised on
            // purpose: the catalogue changes, and a support agent needs to see
            // what the customer saw, not what the answer would be today.
            $table->string('confidence', 16)->nullable();
            $table->string('top_record_id', 191)->nullable();
            $table->string('top_title', 255)->nullable();

            $table->timestamp('emailed_at')->nullable();
            $table->timestamp('forwarded_at')->nullable();
            $table->text('delivery_error')->nullable();

            $table->timestamps();
        });
    }

    public function down(): void
    {
        Schema::dropIfExists('rcu_support_requests');
    }
};
