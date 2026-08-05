<?php

namespace App\Console\Commands;

use App\Models\User;
use Illuminate\Console\Command;
use Illuminate\Support\Facades\Hash;
use Illuminate\Support\Str;

/**
 * Create or re-password the operator account.
 *
 * There is no registration route and there should not be: this application has
 * exactly one class of human user, the operator, and an open sign-up form on a
 * box serving user photographs is a liability rather than a feature.
 */
class MakeUserCommand extends Command
{
    protected $signature = 'rcu:make-user
                            {email : the account to create or update}
                            {--name= : display name, defaults to the address}
                            {--password= : plain password; generated when omitted}';

    protected $description = 'Create the admin user, or reset its password';

    public function handle(): int
    {
        $email = (string) $this->argument('email');

        if (! filter_var($email, FILTER_VALIDATE_EMAIL)) {
            $this->error("Not an email address: {$email}");

            return self::FAILURE;
        }

        // Generated rather than defaulted: a command that quietly creates an
        // account with a known password on a public host is worse than one
        // that refuses to run.
        $plain = (string) ($this->option('password') ?: Str::password(20));
        $existing = User::where('email', $email)->first();

        $user = User::updateOrCreate(
            ['email' => $email],
            [
                'name' => $this->option('name') ?: $email,
                'password' => Hash::make($plain),
            ],
        );

        $this->info(($existing ? 'Updated ' : 'Created ') . $user->email);

        if (! $this->option('password')) {
            $this->line('');
            $this->line("  password: {$plain}");
            $this->line('');
            $this->comment('Shown once. It is stored only as a hash.');
        }

        return self::SUCCESS;
    }
}
