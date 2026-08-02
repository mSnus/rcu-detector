<?php

use App\Http\Controllers\IdentifyController;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Route;

Route::get('/user', function (Request $request) {
    return $request->user();
})->middleware('auth:sanctum');

/*
| Recognition.
|
| Throttled because each call costs the Python service several seconds of OCR
| on a box that measurably runs out of memory under load -- the limit is
| capacity protection, not abuse protection.
*/
Route::post('/identify', [IdentifyController::class, 'store'])
    ->middleware('throttle:20,1');

Route::get('/identify/{requestId}', [IdentifyController::class, 'show'])
    ->middleware('throttle:60,1');

// Feedback. Cheap, and every answer is a labelled training pair (plan 6.4).
Route::post('/identify/{requestId}/choose', [IdentifyController::class, 'choose'])
    ->middleware('throttle:60,1');
