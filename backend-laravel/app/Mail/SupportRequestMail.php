<?php

namespace App\Mail;

use App\Models\RcuSupportRequest;
use Illuminate\Bus\Queueable;
use Illuminate\Mail\Mailable;
use Illuminate\Mail\Mailables\Attachment;
use Illuminate\Mail\Mailables\Content;
use Illuminate\Mail\Mailables\Envelope;
use Illuminate\Queue\SerializesModels;
use Illuminate\Support\Facades\Storage;

/**
 * The support request, as support receives it.
 *
 * The photograph is attached rather than linked: support should not need an
 * account, a VPN or a working web app to see what the customer photographed.
 */
class SupportRequestMail extends Mailable
{
    use Queueable, SerializesModels;

    public function __construct(public RcuSupportRequest $req)
    {
    }

    public function envelope(): Envelope
    {
        $who = $this->req->name !== '' ? $this->req->name : 'без имени';

        return new Envelope(
            subject: "Запрос на подбор пульта — {$who}",
        );
    }

    public function content(): Content
    {
        return new Content(view: 'emails.support_request');
    }

    public function attachments(): array
    {
        if (! $this->req->image_path) {
            return [];
        }

        $disk = Storage::disk(config('rcu.support.disk'));
        if (! $disk->exists($this->req->image_path)) {
            return [];
        }

        return [
            Attachment::fromStorageDisk(config('rcu.support.disk'),
                $this->req->image_path)
                ->as('remote-' . $this->req->id . '.jpg')
                ->withMime('image/jpeg'),
        ];
    }
}
