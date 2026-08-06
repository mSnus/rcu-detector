"""List the records whose body was an implausible fragment, and clear them.

`CFG.body.min_plausible_area_frac` changed what body detection returns for
images where the remote is the same colour as the backdrop: the mask inverts,
the high-contrast interior survives as "foreground", and a fragment of it comes
back as a confident body. Records already extracted under the old rule keep the
fragment, so they have to be extracted again.

Re-extracting is not enough on its own. These images typically produced
*several* phantom bodies -- `LD-22A305F_1` came back as five crops holding 362
"buttons" between them -- and after the fix they produce one. Writing `_0` over
the old `_0` leaves `_1` through `_4` on disk, still indexed, still returned as
matches, now belonging to nothing. So the old artefacts are deleted first and
the whole stem is rebuilt.

    python scripts/reextract_implausible.py --fp ../work/fp --out ../work \\
        --manifest ../work/primary.txt --write ../work/reextract.txt
    # then, over that manifest:
    build-catalog --manifest /data/work/reextract.txt --jobs 1 --batch 200
    # then resync, whose --prune drops the catalog rows that lost their files

Measured over 18 of the 882 affected images: 15 better, 1 worse, 2 unchanged,
median quality +0.081.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import CFG  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fp", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True,
                    help="the build output dir, holding fp/ norm/ debug/")
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--write", type=Path, required=True,
                    help="where to write the manifest of images to redo")
    ap.add_argument("--floor", type=float, default=None,
                    help="override CFG.body.min_plausible_area_frac")
    ap.add_argument("--delete", action="store_true",
                    help="actually remove the stale fp/norm/debug artefacts")
    args = ap.parse_args()

    floor = args.floor if args.floor is not None else CFG.body.min_plausible_area_frac

    crops: dict[str, dict[str, dict]] = defaultdict(dict)
    for f in sorted(args.fp.glob("*.json")):
        rid = f.stem
        stem = rid.rsplit("_", 1)[0]
        try:
            crops[stem][rid] = json.loads(f.read_text())
        except json.JSONDecodeError:
            continue

    affected: list[str] = []
    n_records = 0
    for stem, by_rid in crops.items():
        bodies = [(d.get("body") or {}) for d in by_rid.values()]
        # Already full-frame means the fallback has run; nothing to redo.
        if any(b.get("full_frame") for b in bodies):
            continue
        total = sum(b.get("area_frac") or 0.0 for b in bodies)
        if total < floor:
            affected.append(stem)
            n_records += len(by_rid)

    print(f"{len(crops)} source images, {len(affected)} below the {floor} floor "
          f"({n_records} records)")

    # The manifest lines carry the path the build needs, which is not
    # recoverable from a record id: most are under imagecache/, some are not.
    lines = []
    for line in args.manifest.read_text().splitlines():
        line = line.strip()
        if line and os.path.splitext(os.path.basename(line))[0] in set(affected):
            lines.append(line)

    args.write.write_text("\n".join(lines) + ("\n" if lines else ""))
    print(f"wrote {len(lines)} manifest line(s) -> {args.write}")
    if len(lines) != len(affected):
        # Not fatal, but say so: a record with no manifest line cannot be
        # rebuilt, and its stale artefacts would then be deleted for nothing.
        print(f"  !! {len(affected) - len(lines)} affected stem(s) have no manifest "
              f"line and will be left alone")

    keep = {os.path.splitext(os.path.basename(l))[0] for l in lines}
    removed = 0
    for stem in affected:
        if stem not in keep:
            continue
        for rid in crops[stem]:
            for sub, ext in (("fp", ".json"), ("norm", ".jpg"), ("debug", ".jpg")):
                p = args.out / sub / f"{rid}{ext}"
                if p.exists():
                    if args.delete:
                        p.unlink()
                    removed += 1

    verb = "removed" if args.delete else "would remove"
    print(f"{verb} {removed} stale artefact(s) across fp/ norm/ debug/")
    if not args.delete:
        print("  (dry run -- pass --delete to do it)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
