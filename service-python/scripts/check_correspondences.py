"""Assert the vectorised correspondence search agrees with the readable one.

`_correspondences` builds its cost matrix in numpy because the Python double
loop it replaced was the hot spot of the whole query path: 320,058 calls to
`_pair_cost` for one match against 8262 records, 1.0 s of the 1.3 s that match
took.

An optimisation that changes the answer is not an optimisation, and this one
could change it invisibly. The greedy assignment below the matrix walks the
pairs in cost order and takes the first free (i, j); two orderings that differ
only in how they break ties therefore produce *different* one-to-one
assignments, different inlier counts, and different scores -- for no reason
anybody could later trace. `pairs.sort()` on (cost, i, j) breaks ties by i then
j, which is why the replacement uses np.lexsort with cost as the last key.

So the reference implementation stays in the module, and this compares the two
over real fingerprint pairs from the catalogue:

    python scripts/check_correspondences.py --fp ../work/fp --pairs 400

Exits non-zero on any disagreement.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.matching import verify as V  # noqa: E402


def reference(q_buttons: list[dict], c_buttons: list[dict]):
    """The Python double loop `_correspondences` used to run, verbatim."""
    pairs = []
    for i, qb in enumerate(q_buttons):
        for j, cb in enumerate(c_buttons):
            cost = V._pair_cost(qb, cb)
            if cost is not None:
                pairs.append((cost, i, j))
    pairs.sort()

    used_q: set[int] = set()
    used_c: set[int] = set()
    src, dst, idx = [], [], []
    for _, i, j in pairs:
        if i in used_q or j in used_c:
            continue
        used_q.add(i)
        used_c.add(j)
        src.append([float(q_buttons[i]["x"]), float(q_buttons[i]["y"])])
        dst.append([float(c_buttons[j]["x"]), float(c_buttons[j]["y"])])
        idx.append((i, j))
    return (np.asarray(src, dtype=np.float32),
            np.asarray(dst, dtype=np.float32), idx)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fp", type=Path, required=True)
    ap.add_argument("--pairs", type=int, default=400)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    files = sorted(args.fp.glob("*.json"))
    if len(files) < 2:
        sys.exit(f"need at least two fingerprints in {args.fp}")

    rng = random.Random(args.seed)
    fps = [json.loads(p.read_text()) for p in files]
    # Buttons only; that is all a correspondence search sees.
    sets = [f.get("buttons") or [] for f in fps]
    sets = [s for s in sets if s]

    checked = 0
    empties = 0
    bad: list[str] = []
    t_ref = t_new = 0.0

    for _ in range(args.pairs):
        a = rng.randrange(len(sets))
        b = rng.randrange(len(sets))

        t0 = time.perf_counter()
        r_src, r_dst, r_idx = reference(sets[a], sets[b])
        t_ref += time.perf_counter() - t0

        t0 = time.perf_counter()
        n_src, n_dst, n_idx = V._correspondences(sets[a], sets[b])
        t_new += time.perf_counter() - t0

        checked += 1
        if not r_idx:
            empties += 1

        if r_idx != n_idx:
            bad.append(f"pair {a},{b}: {len(r_idx)} vs {len(n_idx)} correspondences"
                       f" -- first difference at "
                       f"{next((k for k, (x, y) in enumerate(zip(r_idx, n_idx)) if x != y), 'length')}")
            continue
        if not np.array_equal(r_src, n_src) or not np.array_equal(r_dst, n_dst):
            bad.append(f"pair {a},{b}: same indices, different coordinates")

    # An all-empty run would pass every assertion and prove nothing.
    print(f"{checked} pair(s) compared over {len(sets)} fingerprint(s), "
          f"{empties} yielded no correspondences")
    print(f"reference {t_ref*1000:.0f} ms total, vectorised {t_new*1000:.0f} ms "
          f"({t_ref / max(t_new, 1e-9):.1f}x)")

    if empties == checked:
        print("\nFAIL -- every pair was empty, so nothing was actually compared")
        return 1

    if bad:
        print(f"\n!! {len(bad)} disagreement(s):")
        for line in bad[:20]:
            print(f"     {line}")
        print("\nFAIL -- see _correspondences in app/matching/verify.py")
        return 1

    print("\nOK -- identical assignments, ties included")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
