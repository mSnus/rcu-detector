#!/usr/bin/env python3
"""Leave-one-out evaluation of the matching engine.

  python scripts/match_eval.py --fp ../work/fp --index ../work/index/tokens.npz

Every fingerprint is matched against an index containing all the others but
not itself, so a record cannot win by being its own best match.

Ground truth is read from `app/data/eval_truth.tsv` and was written by looking
at every overlay. It is NOT inferred from the filenames: doing that marked the
two genuine cross-image pairs as false matches (different OCR'd model codes
for one remote) and marked a strip of fingernails as the true match for a
HUAYU RM-530F (two bodies from one image). Both errors made the matcher look
broken when it was working.

**This sample is 21 records and cannot calibrate anything.** IDF is a
statistic over the catalog, and over 21 documents it is mostly noise; the
confidence bands in particular are the plan's untested starting numbers. What
this script is good for now is catching a regression -- a change that stops
the known-good pairs matching -- and for reporting the score separation
between true and false pairs, which is the number that matters once the real
catalog exists.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.matching.index import TokenIndex
from app.matching.matcher import Matcher
from app.matching.store import JsonDirStore

TRUTH_PATH = Path(__file__).resolve().parents[1] / "app/data/eval_truth.tsv"


def load_truth(path: Path) -> dict[str, str]:
    """stem -> remote identity, or "BAD" for a non-remote extraction."""
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) == 2:
            out[parts[0].strip()] = parts[1].strip()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fp", default="../work/fp")
    ap.add_argument("--index", default="../work/index/tokens.npz")
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--truth", default=str(TRUTH_PATH))
    args = ap.parse_args()

    store = JsonDirStore(args.fp)
    index = TokenIndex.load(args.index)
    matcher = Matcher(index, store)
    fps = dict(store.iter_all())

    labels = load_truth(Path(args.truth))
    missing = sorted(set(fps) - set(labels))
    if missing:
        print(f"warning: no ground truth for {len(missing)} record(s), "
              f"skipped: {missing[:3]}")

    bad = {r for r, lab in labels.items() if lab == "BAD"}
    # Which records even have a true match available. Recall is meaningless
    # for a record whose only correct answer is itself.
    truth = {
        a: {b for b in fps
            if b != a and b not in bad and labels.get(b) == labels.get(a)}
        for a in fps if a in labels and a not in bad
    }
    answerable = sorted(a for a, t in truth.items() if t)

    print(f"{len(fps)} records, {len(bad)} excluded as bad extractions, "
          f"{len(answerable)} with a true match present\n")

    hit1 = hitk = 0
    true_scores, false_scores = [], []

    for rid in sorted(fps):
        if rid in bad:
            print(f"skip {rid[:30]:32s} -> excluded, extraction is not a remote")
            continue
        if rid not in truth:
            continue
        res = matcher.match(fps[rid], top_k=args.top_k, exclude={rid})
        cands = res.candidates
        want = truth[rid]

        # A candidate that is itself a bad extraction is neither a true nor a
        # false pair -- it is an upstream bug, and counting it as a retrieval
        # error would blame the matcher for it.
        for c in cands:
            if c.record_id in bad:
                continue
            (true_scores if c.record_id in want else false_scores).append(c.score)

        if want:
            got1 = bool(cands) and cands[0].record_id in want
            gotk = any(c.record_id in want for c in cands)
            hit1 += got1
            hitk += gotk
            mark = "HIT " if got1 else ("hit@k" if gotk else "MISS")
        else:
            mark = "  - "

        top = cands[0] if cands else None
        detail = (f"{top.record_id[:28]:30s} {top.score:.3f} "
                  f"(g={top.geometric:.2f} t1={top.tier1:.2f} "
                  f"inl={top.inliers:2d})" if top else "no candidates")
        print(f"{mark} {rid[:30]:32s} -> {detail}  [{res.confidence}]")
        if want and cands and cands[0].record_id not in want:
            print(f"        expected {sorted(want)}")

    n = len(answerable) or 1
    print(f"\nrecall@1 {hit1}/{len(answerable)} ({hit1 / n:.0%})   "
          f"recall@{args.top_k} {hitk}/{len(answerable)} ({hitk / n:.0%})")

    if true_scores and false_scores:
        import statistics as st
        print(f"true-pair scores  min {min(true_scores):.3f} "
              f"median {st.median(true_scores):.3f} max {max(true_scores):.3f}")
        print(f"false-pair scores min {min(false_scores):.3f} "
              f"median {st.median(false_scores):.3f} "
              f"max {max(false_scores):.3f}")
        print(f"separation (min true - max false): "
              f"{min(true_scores) - max(false_scores):+.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
