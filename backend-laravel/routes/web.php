<?php

use App\Http\Controllers\AdminVisualiserController;
use App\Http\Controllers\TryController;
use Illuminate\Support\Facades\Route;

Route::get('/', function () {
    return view('welcome');
});

/*
| Test page. No auth: it serves catalog crops and match internals to whoever
| can reach it, which is fine on a loopback dev box and nowhere else, so it is
| off unless `rcu.try_page` says otherwise. See config/rcu.php.
|
| The routes are registered either way and the controller refuses when the flag
| is off. Registering them conditionally reads as tighter and is not: routes
| are bound while the application boots, so the behaviour then depends on the
| environment at boot rather than on the config, which cannot be tested without
| rebooting the application mid-test and is a worse thing to depend on.
*/
Route::get('/try', [TryController::class, 'index'])->name('rcu.try');
Route::get('/try/photo/{recordId}', [TryController::class, 'photo'])
    ->where('recordId', '[A-Za-z0-9._-]+')->name('rcu.try.photo');

/*
| Admin visualiser (plan 6.5).
|
| Behind `auth` because it serves user-uploaded photographs and exposes the
| recognition service's internals. There is no public route to any of it.
|
| The auth guard is the framework default; wire it to the existing admin
| authentication when this is mounted into the real application.
*/
Route::middleware(['auth'])->prefix('admin/rcu')->name('admin.rcu.')->group(function () {
    Route::get('/', [AdminVisualiserController::class, 'index'])->name('index');
    Route::post('/inspect', [AdminVisualiserController::class, 'inspect'])->name('inspect');

    /*
    | Catalog. These must stay above the `{requestId}` routes below: that
    | parameter has no pattern constraint, so it matches the literal string
    | "catalog" and would swallow every one of them.
    */
    Route::get('/catalog', [AdminVisualiserController::class, 'catalog'])->name('catalog');
    Route::get('/catalog/{recordId}', [AdminVisualiserController::class, 'record'])
        ->where('recordId', '[A-Za-z0-9._-]+')->name('record');
    Route::post('/catalog/{recordId}/review', [AdminVisualiserController::class, 'review'])
        ->where('recordId', '[A-Za-z0-9._-]+')->name('review');
    Route::get('/catalog/{recordId}/crop', [AdminVisualiserController::class, 'crop'])
        ->where('recordId', '[A-Za-z0-9._-]+')->name('crop');
    Route::get('/catalog/{recordId}/build-overlay', [AdminVisualiserController::class, 'buildOverlay'])
        ->where('recordId', '[A-Za-z0-9._-]+')->name('build-overlay');

    Route::get('/{requestId}', [AdminVisualiserController::class, 'show'])->name('show');
    Route::get('/{requestId}/upload', [AdminVisualiserController::class, 'upload'])->name('upload');
    Route::get('/{requestId}/overlay', [AdminVisualiserController::class, 'overlay'])->name('overlay');
});
