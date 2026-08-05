<?php

namespace Tests\Feature;

use App\Models\User;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Support\Facades\Hash;
use Illuminate\Support\Facades\Http;
use Tests\TestCase;

/**
 * Session login for the admin visualiser.
 *
 * The case worth pinning hardest is the first one: `/admin/rcu` declared `auth`
 * middleware with no `login` route to redirect to, and returned 500 in
 * production for that reason alone.
 */
class LoginTest extends TestCase
{
    use RefreshDatabase;

    protected function setUp(): void
    {
        parent::setUp();

        // The visualiser's header calls the service; never reach the network.
        Http::fake(['*/health' => Http::response(['status' => 'ok'])]);
    }

    private function user(string $password = 'correct-horse'): User
    {
        return User::create([
            'name' => 'Operator',
            'email' => 'op@example.com',
            'password' => Hash::make($password),
        ]);
    }

    public function test_a_guest_is_sent_to_the_form_not_a_500(): void
    {
        $this->get('/admin/rcu')
            ->assertRedirect('/login');

        $this->get('/login')->assertOk()->assertSee('Sign in');
    }

    public function test_it_signs_a_user_in(): void
    {
        $this->user();

        $this->post('/login', [
            'email' => 'op@example.com',
            'password' => 'correct-horse',
        ])->assertRedirect(route('admin.rcu.index'));

        $this->assertAuthenticated();
    }

    public function test_it_returns_to_where_the_guest_was_headed(): void
    {
        $this->user();

        // The intended URL is what makes a bookmarked overlay survive a login.
        $this->get('/admin/rcu/catalog')->assertRedirect('/login');

        $this->post('/login', [
            'email' => 'op@example.com',
            'password' => 'correct-horse',
        ])->assertRedirect('/admin/rcu/catalog');
    }

    public function test_a_bad_password_is_rejected(): void
    {
        $this->user();

        $this->post('/login', [
            'email' => 'op@example.com',
            'password' => 'wrong',
        ])->assertSessionHasErrors('email');

        $this->assertGuest();
    }

    public function test_an_unknown_account_says_the_same_thing_as_a_bad_password(): void
    {
        $this->user();

        $unknown = $this->post('/login', [
            'email' => 'nobody@example.com', 'password' => 'wrong',
        ])->assertSessionHasErrors('email');

        $bad = $this->post('/login', [
            'email' => 'op@example.com', 'password' => 'wrong',
        ])->assertSessionHasErrors('email');

        // Distinguishing the two tells an attacker which half they have.
        $this->assertSame(
            session('errors')->first('email'),
            $unknown->getSession()->get('errors')->first('email'),
        );
        $this->assertGuest();
    }

    public function test_the_session_id_changes_on_login(): void
    {
        $this->user();

        $this->get('/login');
        $before = session()->getId();

        $this->post('/login', [
            'email' => 'op@example.com',
            'password' => 'correct-horse',
        ]);

        // Session fixation: the pre-login id is attacker-supplied.
        $this->assertNotSame($before, session()->getId());
    }

    public function test_it_logs_out(): void
    {
        $this->actingAs($this->user());

        $this->post('/logout')->assertRedirect('/');
        $this->assertGuest();
    }

    public function test_an_authenticated_user_reaches_the_visualiser(): void
    {
        $this->actingAs($this->user());

        $this->get('/admin/rcu')->assertOk();
    }

    public function test_make_user_creates_an_account_with_a_generated_password(): void
    {
        $this->artisan('rcu:make-user', ['email' => 'op@example.com'])
            ->assertSuccessful();

        $this->assertDatabaseHas('users', ['email' => 'op@example.com']);
    }

    public function test_make_user_resets_an_existing_password(): void
    {
        $this->user('old-password');

        $this->artisan('rcu:make-user', [
            'email' => 'op@example.com',
            '--password' => 'new-password',
        ])->assertSuccessful();

        $this->assertSame(1, User::where('email', 'op@example.com')->count());

        $this->post('/login', [
            'email' => 'op@example.com',
            'password' => 'new-password',
        ])->assertRedirect(route('admin.rcu.index'));
    }

    public function test_make_user_refuses_a_non_address(): void
    {
        $this->artisan('rcu:make-user', ['email' => 'not-an-email'])
            ->assertFailed();

        $this->assertDatabaseCount('users', 0);
    }
}
