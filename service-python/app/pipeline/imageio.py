"""The one place an image becomes pixels.

Both the catalog build and the query service must decode a given file to
*exactly* the same array. They did not, and the reason is worse than a
mismatched flag.

A JPEG missing its final `FF D9` end-of-image marker is truncated: libjpeg
decodes the scanlines that are present and leaves the rest of the output
buffer as it found it. What lands in the missing region is therefore whatever
was in that heap allocation, so the "same" photograph decodes differently
between two calls in one process, between `cv2.imread` and `cv2.imdecode`, and
between the build and the service. Measured on this sample: up to 18% of
pixels differing between two decodes of one file, intermittently, with no
error reported anywhere.

7 of the 18 sample photographs lack the marker. Scraped catalogs are full of
them.

The consequences were real and had been mistaken for matching problems:
`URC-177500_Wink` extracted 15 buttons upright at one decode and 12 flipped at
another, and a fingerprint built from such an image partly encodes heap
garbage, so re-running the build produces a different record for the same
input.

Appending the marker before decoding fixes it. libjpeg then terminates at the
last complete MCU row and fills the remainder deterministically. Verified: all
7 truncated samples decode identically across 10 reads with the heap churned
between them, and the file that previously failed to decode at all now
decodes.

Everything that turns bytes into an array goes through here. Do not call
`cv2.imread` or `cv2.imdecode` anywhere else.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

# JPEG start-of-image and end-of-image markers.
_SOI = b"\xff\xd8"
_EOI = b"\xff\xd9"


def is_truncated_jpeg(raw: bytes) -> bool:
    """True for JPEG bytes with no end-of-image marker.

    Trailing whitespace or padding is tolerated: some scrapers append it, and
    a file is only truncated if the marker is genuinely absent.
    """
    return raw.startswith(_SOI) and not raw.rstrip(b"\x00\r\n\t ").endswith(_EOI)


def normalise_bytes(raw: bytes) -> bytes:
    """Make truncated JPEG bytes decode deterministically.

    Non-JPEG input is returned untouched -- a truncated PNG is a different
    problem and this marker means nothing to it.
    """
    if not is_truncated_jpeg(raw):
        return raw

    return raw.rstrip(b"\x00\r\n\t ") + _EOI


def decode_image(raw: bytes) -> np.ndarray | None:
    """Bytes -> BGR array, or None if genuinely undecodable.

    The single decode path for the whole project. Returning None rather than
    raising keeps the callers free to log and skip, which is what a catalog
    build over 10k-50k scraped images has to do.
    """
    if not raw:
        return None

    return cv2.imdecode(np.frombuffer(normalise_bytes(raw), np.uint8),
                        cv2.IMREAD_COLOR)


def read_image(path: str | Path) -> np.ndarray | None:
    """Read a file through the same decoder the service uses.

    Deliberately not `cv2.imread`: that is what let the build and the query
    path disagree in the first place.
    """
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return None

    return decode_image(raw)
