"""Measure the confidence bands against a catalog instead of assuming them.

`CFG.fuse.high_score` and friends are the implementation plan's starting
numbers. Nothing has ever been measured against them, and session 5 showed why
that matters: `none` fell 4 -> 1 and `high` rose 10 -> 14 without either
threshold being touched, purely because scores rose underneath them. A band
that moves when the pipeline improves is not reporting confidence, it is
reporting the pipeline.

This uploads every catalog photograph to a running service, records the top
score and the top-to-second margin, and asks the only question the bands exist
to answer: **when the service says `high`, how often is it right?**

    python scripts/calibrate_bands.py --photos ../files \\
        --manifest ../work/legacy_search.txt \\
        --fp ../work/legacy_search/fp --csv ../work/bands.csv

Two populations come out of one pass:

* **true**  -- the score the query's own record scored. What a band must accept.
* **decoy** -- the best score any *other* record scored. What a band must
  reject, and the proxy for a remote that is not in the catalog at all. It is
  only a proxy: the true record is still in the index competing for rank. It
  understates nothing that matters, because a genuine absent-remote query has
  strictly less competition and so scores its best wrong answer no lower.

The sweep then reports, for every candidate threshold, the precision of `high`
and the recall it costs. Pick from that table; do not pick from intuition.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import CFG  # noqa: E402
from scripts.query_drift import (correct_answers, load_truth,  # noqa: E402
                                 post_image, records_for)


def band_of(top: float, margin: float, high_s: float, high_m: float,
            med_s: float, low_s: float) -> str:
    """`matcher._band`, reimplemented over explicit thresholds so the sweep can
    vary them. Kept deliberately in the same shape as the original: if that
    logic changes, this must change with it."""
    if top > high_s and margin > high_m:
        return "high"
    if top > med_s:
        return "medium"
    if top > low_s:
        return "low"
    return "none"


def photo_paths(root: Path, manifest: Path | None) -> list[Path]:
    """Manifest lines are relative to `files/`, not bare names."""
    if manifest:
        return [root / line.strip() for line in manifest.read_text().splitlines()
                if line.strip()]
    return sorted(p for ext in ("*.jpg", "*.jpeg", "*.png")
                  for p in root.glob(ext))


def quantiles(xs: list[float]) -> str:
    if not xs:
        return "n/a"
    s = sorted(xs)
    q = lambda f: s[min(len(s) - 1, int(f * len(s)))]  # noqa: E731
    return (f"min {s[0]:.3f}  p10 {q(0.10):.3f}  median {q(0.50):.3f}  "
            f"p90 {q(0.90):.3f}  max {s[-1]:.3f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--photos", type=Path, required=True)
    ap.add_argument("--manifest", type=Path, default=None,
                    help="paths relative to --photos, one per line")
    ap.add_argument("--fp", type=Path, required=True)
    ap.add_argument("--truth", type=Path, default=None,
                    help="record_id<TAB>model_id from `php artisan "
                         "rcu:export-truth`. Without it a correct answer under "
                         "a second filename counts as a miss, and every number "
                         "printed below is a floor rather than a measurement.")
    ap.add_argument("--url", default="http://127.0.0.1:8600")
    ap.add_argument("--token", default=None)
    ap.add_argument("--timeout", type=float, default=180.0)
    ap.add_argument("--top-k", type=int, default=10)
    ap.add_argument("--csv", type=Path, default=None)
    ap.add_argument("--sample", type=int, default=0,
                    help="query a random N of the photographs, 0 for all. "
                         "A full catalogue is hours of uploads; a sample of a "
                         "few hundred prices a band well enough to pick one.")
    ap.add_argument("--seed", type=int, default=0,
                    help="sampling seed, so a rerun queries the same photos")
    args = ap.parse_args()

    fps = {p.stem: json.loads(p.read_text()) for p in sorted(args.fp.glob("*.json"))}
    truth = load_truth(args.truth)
    if truth:
        print(f"truth: {len(truth)} record(s) in "
              f"{len(set(truth.values()))} product group(s)")
    else:
        print("truth: NONE -- keyed on the filename stem, so a correct answer "
              "under a second filename will count as a miss")
    if not fps:
        sys.exit(f"no fingerprints in {args.fp}")

    photos = photo_paths(args.photos, args.manifest)

    # Sampled uniformly over the manifest, never truncated to the first N. The
    # manifest is alphabetical, so a prefix is a sample of one corner of the
    # brand distribution -- and a build that stopped early would be measured as
    # if it had finished.
    sampled_from = 0
    if args.sample and args.sample < len(photos):
        sampled_from = len(photos)
        photos = random.Random(args.seed).sample(photos, args.sample)
        photos.sort()

    url = f"{args.url.rstrip('/')}/identify?top_k={args.top_k}"

    rows: list[dict] = []
    errors: list[str] = []
    skipped = 0

    for i, photo in enumerate(photos, 1):
        if not photo.is_file():
            skipped += 1
            continue
        mine = correct_answers(photo.stem, fps, truth)
        if not mine:
            # Extraction found no remote in this photograph, so there is
            # nothing it could correctly retrieve. Counted, never silent.
            skipped += 1
            continue

        result = post_image(url, photo, args.token, args.timeout)
        if "_error" in result:
            errors.append(f"{photo.name}: {result['_error']}")
            continue

        cands = result.get("candidates") or []
        top = cands[0]["score"] if cands else 0.0
        second = cands[1]["score"] if len(cands) > 1 else 0.0
        top_id = cands[0]["record_id"] if cands else None
        correct = top_id in mine

        self_score = max((c["score"] for c in cands if c["record_id"] in mine),
                         default=None)
        decoy_score = max((c["score"] for c in cands if c["record_id"] not in mine),
                          default=None)

        rows.append({
            "photo": photo.stem,
            "top_id": top_id,
            "correct": int(correct),
            "top": round(top, 4),
            "margin": round(top - second, 4),
            "self_score": None if self_score is None else round(self_score, 4),
            "decoy_score": None if decoy_score is None else round(decoy_score, 4),
            "service_confidence": result.get("confidence"),
            "latency_ms": result.get("latency_ms"),
        })
        print(f"[{i:4d}/{len(photos)}] {photo.stem:38s} "
              f"{'OK ' if correct else 'BAD'} top {top:.3f} "
              f"margin {top - second:.3f}  {result.get('confidence')}",
              flush=True)

    if not rows:
        # Say which of the two it was. A run where every query was refused with
        # a 4xx looks identical to a dead service from here, and the advice for
        # the two is opposite: one is a verdict on the images, the other is the
        # service. This cost a debugging round the first time -- three sampled
        # photographs were all imagecache thumbnails, every one refused on its
        # size, and the message asked whether the service was running.
        if errors:
            print(f"{len(errors)} query error(s):", file=sys.stderr)
            for e in errors[:10]:
                print(f"  {e}", file=sys.stderr)
        sys.exit(f"no queries succeeded ({skipped} skipped, {len(errors)} "
                 f"errored). If the errors above are 4xx, the service is fine "
                 f"and the images were refused; if there are none, check the "
                 f"service is running and pointed at --fp.")

    if args.csv:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        with args.csv.open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)

    n = len(rows)
    n_correct = sum(r["correct"] for r in rows)
    print("\n" + "=" * 78)
    print(f"{n} queries, {skipped} skipped (no photo or no catalog record), "
          f"{len(errors)} error(s)")
    if sampled_from:
        # Stated on the result, not only in the invocation: every number below
        # is an estimate with a sampling error, and the next reader of this
        # output will not have the command line.
        print(f"SAMPLE: {len(photos)} of {sampled_from} photographs "
              f"(seed {args.seed}) -- the precisions below are estimates")
    print(f"self-retrieval recall@1 {n_correct}/{n} ({100*n_correct/n:.0f}%)")

    true_scores = [r["self_score"] for r in rows if r["self_score"] is not None]
    decoys = [r["decoy_score"] for r in rows if r["decoy_score"] is not None]
    print(f"\ntrue  score   n={len(true_scores):3d}  {quantiles(true_scores)}")
    print(f"decoy score   n={len(decoys):3d}  {quantiles(decoys)}")

    # Separation as match_eval defines it: how far the true pair sits above the
    # best false pair, on average. Negative means the bands cannot work at all.
    both = [(r["self_score"], r["decoy_score"]) for r in rows
            if r["self_score"] is not None and r["decoy_score"] is not None]
    if both:
        sep = sum(t - d for t, d in both) / len(both)
        print(f"mean separation (true - best decoy) {sep:+.3f} over {len(both)} queries")

    cfg = CFG.fuse
    print(f"\ncurrent bands: high >{cfg.high_score} & margin >{cfg.high_margin}, "
          f"medium >{cfg.medium_score}, low >{cfg.low_score}")
    print(f"{'band':8s} {'n':>4s} {'correct':>8s} {'precision':>10s}")
    for band in ("high", "medium", "low", "none"):
        sel = [r for r in rows if band_of(r["top"], r["margin"], cfg.high_score,
                                          cfg.high_margin, cfg.medium_score,
                                          cfg.low_score) == band]
        if not sel:
            print(f"{band:8s} {0:4d} {'-':>8s} {'-':>10s}")
            continue
        c = sum(r["correct"] for r in sel)
        print(f"{band:8s} {len(sel):4d} {c:8d} {100*c/len(sel):9.0f}%")

    # A band is a promise about precision. Sweep the two knobs that make it and
    # report what each promise costs in coverage.
    print("\nhigh-band sweep (what `high` would mean at each threshold):")
    print(f"{'score':>6s} {'margin':>7s} {'n high':>7s} {'correct':>8s} "
          f"{'precision':>10s} {'recall of all correct':>22s}")
    for hs in (0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85):
        for hm in (0.00, 0.05, 0.10, 0.15, 0.20):
            sel = [r for r in rows if r["top"] > hs and r["margin"] > hm]
            if not sel:
                continue
            c = sum(r["correct"] for r in sel)
            print(f"{hs:6.2f} {hm:7.2f} {len(sel):7d} {c:8d} "
                  f"{100*c/len(sel):9.0f}% {100*c/max(1, n_correct):21.0f}%")

    print("\nfloor sweep (`none` must reject wrong answers, not right ones):")
    print(f"{'low_score':>9s} {'n none':>7s} {'correct lost':>13s} "
          f"{'wrong rejected':>15s}")
    n_wrong = n - n_correct
    for ls in (0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50):
        sel = [r for r in rows if r["top"] <= ls]
        lost = sum(r["correct"] for r in sel)
        rejected = len(sel) - lost
        print(f"{ls:9.2f} {len(sel):7d} {lost:13d} "
              f"{rejected:6d} / {n_wrong:<6d}")

    if errors:
        print(f"\n{len(errors)} error(s):")
        for e in errors[:20]:
            print(f"  {e}")


if __name__ == "__main__":
    main()
