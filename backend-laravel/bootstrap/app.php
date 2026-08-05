<?php

use Illuminate\Foundation\Application;
use Illuminate\Foundation\Configuration\Exceptions;
use Illuminate\Foundation\Configuration\Middleware;

return Application::configure(basePath: dirname(__DIR__))
    ->withRouting(
        web: __DIR__.'/../routes/web.php',
        api: __DIR__.'/../routes/api.php',
        commands: __DIR__.'/../routes/console.php',
        health: '/up',
    )
    ->withMiddleware(function (Middleware $middleware): void {
        /*
         * TLS is terminated by a reverse proxy, never by this stack: the
         * container nginx listens on plain HTTP and is published on loopback
         * only (HTTP_BIND), so every request that reaches it has already come
         * through the host's proxy.
         *
         * Without this Laravel builds absolute URLs with the scheme it thinks
         * it is serving, which is http. Live proof before this was set: a
         * guest hitting https://rcud.pultovnet.ru/admin/rcu was redirected to
         * http://rcud.pultovnet.ru/login -- a login form, downgraded, with a
         * session cookie that then had no reason to be marked secure.
         *
         * Trusting every proxy is right here precisely because the app is not
         * reachable except through one. If it is ever published directly, this
         * must become a list of addresses: a trusted X-Forwarded-For from an
         * arbitrary client is a spoofed client address.
         */
        $middleware->trustProxies(at: '*');
    })
    ->withExceptions(function (Exceptions $exceptions): void {
        //
    })->create();
