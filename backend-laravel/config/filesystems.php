<?php

return [

    /*
    |--------------------------------------------------------------------------
    | Default Filesystem Disk
    |--------------------------------------------------------------------------
    |
    | Here you may specify the default filesystem disk that should be used
    | by the framework. The "local" disk, as well as a variety of cloud
    | based disks are available to your application for file storage.
    |
    */

    'default' => env('FILESYSTEM_DISK', 'local'),

    /*
    |--------------------------------------------------------------------------
    | Filesystem Disks
    |--------------------------------------------------------------------------
    |
    | Below you may configure as many filesystem disks as necessary, and you
    | may even configure multiple disks for the same driver. Examples for
    | most supported storage drivers are configured here for reference.
    |
    | Supported drivers: "local", "ftp", "sftp", "s3"
    |
    */

    'disks' => [

        'local' => [
            'driver' => 'local',
            'root' => storage_path('app/private'),
            'serve' => true,
            'throw' => false,
            'report' => false,
        ],

        /*
        | Query uploads. Private on purpose: these are photographs taken by
        | users, they are retained as training data, and nothing about them
        | should be reachable by guessing a URL.
        |
        | `throw` is on because a silently missing upload would be sent to the
        | recognition service as an empty body and come back as a confident
        | "no remote found", which is the wrong diagnosis entirely.
        */
        'rcu' => [
            'driver' => 'local',
            'root' => env('RCU_STORAGE_ROOT', storage_path('app/rcu')),
            'serve' => false,
            'throw' => true,
            'report' => false,
        ],

        /*
         * Support requests raised from /try. A separate disk from `rcu`
         * because the two have different lifetimes: query uploads are a
         * prunable log, and a customer asking to be called back is not. A
         * prune of one must not be able to reach the other.
         *
         * `throw` is false here and true above, deliberately. A storage
         * failure while writing a query upload should fail the request; a
         * storage failure while writing the support copy should not lose the
         * name and telephone number, which are the part that matters.
         */
        'rcu_support' => [
            'driver' => 'local',
            'root' => env('RCU_SUPPORT_ROOT',
                storage_path('app/rcu/support_requests')),
            'serve' => false,
            'throw' => false,
            'report' => true,
        ],

        'public' => [
            'driver' => 'local',
            'root' => storage_path('app/public'),
            'url' => rtrim(env('APP_URL', 'http://localhost'), '/').'/storage',
            'visibility' => 'public',
            'throw' => false,
            'report' => false,
        ],

        's3' => [
            'driver' => 's3',
            'key' => env('AWS_ACCESS_KEY_ID'),
            'secret' => env('AWS_SECRET_ACCESS_KEY'),
            'region' => env('AWS_DEFAULT_REGION'),
            'bucket' => env('AWS_BUCKET'),
            'url' => env('AWS_URL'),
            'endpoint' => env('AWS_ENDPOINT'),
            'use_path_style_endpoint' => env('AWS_USE_PATH_STYLE_ENDPOINT', false),
            'throw' => false,
            'report' => false,
        ],

    ],

    /*
    |--------------------------------------------------------------------------
    | Symbolic Links
    |--------------------------------------------------------------------------
    |
    | Here you may configure the symbolic links that will be created when the
    | `storage:link` Artisan command is executed. The array keys should be
    | the locations of the links and the values should be their targets.
    |
    */

    'links' => [
        public_path('storage') => storage_path('app/public'),
    ],

];
