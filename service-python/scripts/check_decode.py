"""Assert the decode invariant the catalog and the query path both depend on.

Two properties must hold for every catalog image, or matching degrades in ways
that look like a scoring bug:

  1. The build path and the service path decode to the *same* array.
  2. Decoding is stable -- the same bytes give the same pixels every time.

Neither held before session 6. A JPEG missing its `FF D9` marker left the tail
of the output buffer uninitialised, so the two paths disagreed intermittently
and a rebuild could produce a different fingerprint for an unchanged file.
`URC-177500_Wink` was matched as a Ginzzu because of it.

Run after changing anything in app/pipeline/imageio.py, and over any new
catalog drop:

    python scripts/check_decode.py --dir ../photos

Exits non-zero on violation, so it can gate a build.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.pipeline.imageio import (decode_image, is_truncated_jpeg,  # noqa: E402
                                  read_image)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, default=Path("../photos"))
    ap.add_argument("--repeats", type=int, default=4,
                    help="decodes per file when checking stability")
    args = ap.parse_args()

    paths = sorted(p for ext in ("*.jpg", "*.jpeg", "*.png", "*.webp")
                   for p in args.dir.glob(ext))
    if not paths:
        sys.exit(f"no images in {args.dir}")

    mismatched: list[str] = []
    unstable: list[str] = []
    undecodable: list[str] = []
    truncated = 0

    for path in paths:
        raw = path.read_bytes()
        if is_truncated_jpeg(raw):
            truncated += 1

        build = read_image(path)
        service = decode_image(raw)

        if build is None or service is None:
            undecodable.append(path.name)
            continue

        if not np.array_equal(build, service):
            mismatched.append(path.name)

        # Churn the heap between reads: an uninitialised tail only shows
        # itself when the allocator hands back a different block.
        seen = []
        for _ in range(args.repeats):
            churn = [np.random.bytes(1 << 20) for _ in range(3)]
            seen.append(read_image(path))
            del churn
        if any(s is None or not np.array_equal(seen[0], s) for s in seen[1:]):
            unstable.append(path.name)

    n = len(paths)
    print(f"{n} image(s) checked, {truncated} truncated JPEG(s) repaired on read")

    for label, bad in (("build and service disagree", mismatched),
                       ("decode is not repeatable", unstable),
                       ("undecodable", undecodable)):
        if bad:
            print(f"\n!! {label}: {len(bad)}")
            for name in bad[:20]:
                print(f"     {name}")

    if mismatched or unstable:
        print("\nFAIL -- see app/pipeline/imageio.py")
        return 1

    if undecodable:
        # Not a failure of the invariant: genuinely broken files are logged
        # and skipped by the build, which is the intended behaviour.
        print("\nOK (undecodable files are skipped by the build)")
        return 0

    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
