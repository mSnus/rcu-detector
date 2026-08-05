<?php

namespace App\Http\Controllers;

use Illuminate\Http\RedirectResponse;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Auth;
use Illuminate\Validation\ValidationException;
use Illuminate\View\View;

/**
 * Session login for the admin visualiser.
 *
 * Hand-rolled rather than Breeze: the application needs one form and a session,
 * and the `users` table and model already exist from the framework skeleton.
 * Everything else Breeze installs -- registration, password reset, email
 * verification, a build toolchain -- is surface area on a box that serves user
 * photographs, for a page only the operator uses.
 *
 * Why this exists at all: `/admin/rcu` has declared `auth` middleware since
 * session 5, and there was no `login` route for it to redirect to, so every
 * request to the review queue, the catalog browser and the overlays returned
 * a 500 in production rather than a login form.
 */
class LoginController extends Controller
{
    public function show(): View|RedirectResponse
    {
        if (Auth::check()) {
            return redirect()->intended(route('admin.rcu.index'));
        }

        return view('auth.login');
    }

    public function store(Request $request): RedirectResponse
    {
        $credentials = $request->validate([
            'email' => ['required', 'string', 'email'],
            'password' => ['required', 'string'],
        ]);

        if (! Auth::attempt($credentials, $request->boolean('remember'))) {
            // One message for both cases. Distinguishing "no such account" from
            // "wrong password" tells an attacker which half they have.
            throw ValidationException::withMessages([
                'email' => 'Those credentials do not match our records.',
            ]);
        }

        // The old session id is an attacker-supplied value until this point;
        // fixing it to an authenticated session is session fixation.
        $request->session()->regenerate();

        return redirect()->intended(route('admin.rcu.index'));
    }

    public function destroy(Request $request): RedirectResponse
    {
        Auth::logout();

        $request->session()->invalidate();
        $request->session()->regenerateToken();

        return redirect('/');
    }
}
