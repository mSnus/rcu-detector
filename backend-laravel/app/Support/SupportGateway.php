<?php

namespace App\Support;

use App\Models\RcuSupportRequest;
use Illuminate\Support\Facades\Http;
use Illuminate\Support\Facades\Log;
use Illuminate\Support\Facades\Storage;

/**
 * Hand the request to the upstream ticket system.
 *
 * Unconfigured by default and that is a supported state, not a broken one:
 * the row is already written and the customer has already been told their
 * request was received. What is missing is only the forwarding, so it is
 * logged and left visible on the row, never allowed to fail the request.
 */
class SupportGateway
{
    /** True when the request was accepted upstream. */
    public static function forward(RcuSupportRequest $req): bool
    {
        $url = config('rcu.support.api_url');
        if (! $url) {
            return false;
        }

        try {
            $http = Http::timeout((int) config('rcu.support.api_timeout', 10));

            if ($token = config('rcu.support.api_token')) {
                $http = $http->withToken($token);
            }

            $fields = [
                'name' => $req->name,
                'phone' => $req->phone,
                'source' => 'rcu-try',
                'reference' => (string) $req->id,
            ];

            $disk = Storage::disk(config('rcu.support.disk'));
            if ($req->image_path && $disk->exists($req->image_path)) {
                $http = $http->attach('image', $disk->get($req->image_path),
                    'remote-' . $req->id . '.jpg');
            }

            $res = $http->post($url, $fields);

            if ($res->successful()) {
                return true;
            }

            $req->delivery_error = trim(($req->delivery_error ?? '')
                . " api:{$res->status()}");
        } catch (\Throwable $e) {
            report($e);
            $req->delivery_error = trim(($req->delivery_error ?? '')
                . ' api:' . substr($e->getMessage(), 0, 120));
        }

        Log::warning('support request not forwarded', ['id' => $req->id]);

        return false;
    }
}
