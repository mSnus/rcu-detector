<?php

return [

    /*
    |---------------------------------------------------------------------------
    | Python recognition service
    |---------------------------------------------------------------------------
    |
    | All computer vision lives in service-python and is reached over loopback
    | HTTP. Never reimplement any of it in PHP -- a catalog built by one
    | implementation and queried by a slightly different one degrades silently
    | and looks like a matching problem.
    |
    */

    'service_url' => env('RCU_SERVICE_URL', 'http://127.0.0.1:8600'),

    // Sent as X-Internal-Token. The service skips the check when its own
    // RCU_INTERNAL_TOKEN is unset, so a dev box works without one.
    'service_token' => env('RCU_SERVICE_TOKEN'),

    /*
    | The service's own budget is ~1s but measured queries run 3-7s with OCR
    | on, and a cold first request pays for model loading on top. 30s is
    | generous on purpose: a slow answer beats a failed upload.
    */
    'timeout' => (int) env('RCU_SERVICE_TIMEOUT', 30),

    // Retries cover a service restart, not a slow query. Identify is not
    // idempotent in the logging sense (each call mints a new request_id
    // server-side), so keep this low.
    'retries' => (int) env('RCU_SERVICE_RETRIES', 2),
    'retry_delay_ms' => (int) env('RCU_SERVICE_RETRY_DELAY_MS', 250),

    /*
    |---------------------------------------------------------------------------
    | Uploads
    |---------------------------------------------------------------------------
    */

    'upload_disk' => env('RCU_UPLOAD_DISK', 'rcu'),

    // The service refuses anything over its own max_upload_bytes; keep this at
    // or below that so an oversized file is rejected here, cheaply, with a
    // validation error rather than a 413 from loopback.
    'max_upload_kb' => (int) env('RCU_MAX_UPLOAD_KB', 10240),

    // How many candidates to ask for. The picker shows these to the user and
    // every tap is a labelled training pair (plan 6.4).
    'top_k' => (int) env('RCU_TOP_K', 5),

    /*
    |---------------------------------------------------------------------------
    | Admin visualiser
    |---------------------------------------------------------------------------
    |
    | The overlay is the single highest-value debugging artefact in this
    | project -- essentially every bug so far was found by looking at one.
    | Asking for it costs the service a JPEG encode, so it is opt-in per
    | request rather than always on.
    |
    */

    'debug_overlays' => (bool) env('RCU_DEBUG_OVERLAYS', true),

    /*
    |---------------------------------------------------------------------------
    | Catalog build artefacts
    |---------------------------------------------------------------------------
    |
    | Where the offline extraction run left its output. `rcu:import-catalog`
    | reads these; the Python service reads the same fingerprint directory plus
    | the token index built from it.
    |
    | The catalog table and the service's index must come from the *same* run.
    | If they drift, matching returns record_ids that resolve to no row, which
    | looks like a database fault and is not.
    |
    */

    'catalog' => [
        'fp_dir' => env('RCU_FP_DIR', base_path('../work/fp')),
        'norm_dir' => env('RCU_NORM_DIR', base_path('../work/norm')),
        'photo_dir' => env('RCU_PHOTO_DIR', base_path('../photos')),
        'debug_dir' => env('RCU_DEBUG_DIR', base_path('../work/debug')),

        // Extractions below this score go into the review queue (plan 3.10).
        'review_below' => (float) env('RCU_REVIEW_BELOW', 0.75),

        /*
        | The legacy catalogue (Drupal 6), read over the `legacy` connection.
        |
        | Product photos are files on disk; the database holds their paths.
        | Two rules, both learned the hard way on this data:
        |
        |  - the file on disk is `basename(files.filepath)`, NEVER
        |    `files.filename`. Drupal appends _NN on collision, so the two
        |    differ on 53 of 186 rows and `filename` is not even unique --
        |    two different remotes are both called IRC_new.jpg.
        |  - the product's own photo is the `delta = 0` row of
        |    content_field_image_cache. Higher deltas are replacement-model
        |    promos (Zamena_*) and instruction sheets, which are not remotes
        |    and would poison the index if extracted as if they were.
        */
        'files_dir' => env('RCU_LEGACY_FILES', base_path('../files')),

        // Link back to the source item page. {id} is the source node id,
        // stored as model_id. Templated so a domain change is a config edit.
        'item_url' => env('RCU_ITEM_URL', 'https://pultov.net/item/{id}'),
    ],

];
