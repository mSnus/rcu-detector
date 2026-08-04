<?php

namespace App\Services;

use RuntimeException;
use Throwable;

/**
 * The recognition service could not answer.
 *
 * Deliberately distinct from "answered, but found nothing": a 503 here means
 * try again later, whereas confidence=none is a real answer about a real
 * photo and must not be retried.
 */
class RcuServiceException extends RuntimeException
{
    public function __construct(
        string $message,
        public readonly ?int $status = null,
        ?Throwable $previous = null,
        /*
         * The service's own explanation, when it gave one. FastAPI puts it in
         * `detail`. Worth carrying separately from the exception message,
         * which is built for a log line and contains a response dump: on a
         * rejection this is a statement about the caller's own image ("image
         * too small: 200x300"), and it is the only thing that tells them what
         * to do differently.
         */
        public readonly ?string $detail = null,
    ) {
        parent::__construct($message, 0, $previous);
    }

    /**
     * True when the service rejected this particular upload.
     *
     * A 4xx is a verdict on the image and will be identical next time; a 5xx
     * or a connection failure is about the service and may not be. Reporting
     * the first as "unavailable, try again shortly" sends the user to retry a
     * request that cannot succeed, and hides a real defect behind what looks
     * like an outage -- which is exactly how a decode bug in the service
     * presented while this was being wired up.
     */
    public function isRejection(): bool
    {
        return $this->status !== null && $this->status >= 400 && $this->status < 500;
    }
}
