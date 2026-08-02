#!/usr/bin/env python3
"""Audit a directory of extracted fingerprints and rank them for review.

The catalog is 10k-50k records and nobody is going to look at all of them.
What matters is knowing (a) how the extractor did overall and (b) which few
hundred extractions are worth a human's attention. This script answers both
from the fingerprint JSON that `extract_one.py` writes -- it does no CV of its
own, so auditing the whole catalog costs seconds and can be re-run after any
config change.

Every finding is a *flag*, never a deletion. Session 1's rule applies: when the
signal is weak, say so and let a human decide. Nothing here mutates the
fingerprints.

Usage:
  python scripts/audit_catalog.py --fp ../work/fp
  python scripts/audit_catalog.py --fp ../work/fp --csv ../work/audit.csv
  python scripts/audit_catalog.py --fp ../work/fp --debug ../work/debug \
      --review-dir ../work/review --top 200
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import CFG


# ---------------------------------------------------------------------------
# flags
#
# Each returns a short reason string, or None. Severity is the sort key for the
# review queue: 3 means the record is probably unusable, 1 is a note.
# ---------------------------------------------------------------------------

def _flags(fp: dict) -> list[tuple[int, str]]:
    q = CFG.quality
    a = CFG.audit
    out: list[tuple[int, str]] = []

    stats = fp.get("stats", {})
    body = fp.get("body", {})
    n = stats.get("n_buttons", 0)

    if n == 0:
        out.append((3, "no buttons detected"))
    elif n < q.min_plausible_buttons:
        out.append((3, f"only {n} buttons"))
    elif n > q.max_plausible_buttons:
        out.append((2, f"{n} buttons, over plausible max"))

    # A body covering very little of the frame is usually a sub-region the
    # detector locked onto -- a packaging label, a logo panel, a hand -- and
    # the fingerprint then describes that instead of a remote. This is the
    # cheapest reliable catch for it, and the button count alone will not
    # find it: a packaging strip can yield a perfectly plausible nine.
    area = body.get("area_frac")
    if area is not None and area < a.min_body_area_frac:
        out.append((3, f"body is only {area:.1%} of the image"))

    aspect = body.get("aspect") or 0.0
    if aspect and not (a.aspect_range[0] <= aspect <= a.aspect_range[1]):
        out.append((2, f"implausible aspect {aspect:.2f}"))

    # A body found only by the tight-crop fallback was never really segmented:
    # the crop includes whatever else was in the frame.
    if body.get("full_frame"):
        out.append((1, "full-frame fallback body"))
    if body.get("split"):
        out.append((1, "from a split blob"))

    # Orientation errors are silent and corrupting, so an unresolved one is
    # worth more attention than a low button count.
    if stats.get("orientation_conf", 0) < a.low_orientation_conf:
        out.append((2, f"orientation unresolved "
                       f"(conf {stats.get('orientation_conf', 0):.2f})"))

    if fp.get("brand") and fp.get("brand_source") != "list":
        out.append((1, f"unverified brand {fp['brand']!r}"))
    if not fp.get("brand") and not fp.get("model_code"):
        out.append((1, "no brand and no model code"))

    if fp.get("extract_quality", 1.0) < a.low_quality:
        out.append((2, f"quality {fp['extract_quality']:.2f}"))

    return out


def load(fp_dir: Path) -> list[dict]:
    records = []
    for path in sorted(fp_dir.glob("*.json")):
        try:
            fp = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            records.append({"stem": path.stem, "fp": None, "error": str(exc),
                            "flags": [(3, f"unreadable fingerprint: {exc}")]})
            continue
        records.append({"stem": path.stem, "fp": fp, "error": None,
                        "flags": _flags(fp)})
    return records


def _severity(rec: dict) -> tuple:
    """Sort key: worst first. Highest single severity, then how many flags,
    then lowest quality."""
    flags = rec["flags"]
    top = max((s for s, _ in flags), default=0)
    q = (rec["fp"] or {}).get("extract_quality", 0.0)
    return (-top, -len(flags), q)


def _pct(part: int, whole: int) -> str:
    return f"{100.0 * part / whole:.1f}%" if whole else "-"


def report(records: list[dict], top: int) -> None:
    total = len(records)
    if not total:
        print("no fingerprints found")
        return

    good = [r for r in records if r["fp"]]
    flagged = [r for r in records if r["flags"]]

    print(f"\n{'='*66}\n  {total} fingerprint(s)\n{'='*66}")

    # --- distributions ----------------------------------------------------
    counts = [r["fp"]["stats"]["n_buttons"] for r in good]
    quals = [r["fp"].get("extract_quality", 0.0) for r in good]
    if counts:
        counts_sorted = sorted(counts)
        mid = counts_sorted[len(counts_sorted) // 2]
        print(f"\nbuttons   min={min(counts)}  median={mid}  max={max(counts)}"
              f"  mean={sum(counts)/len(counts):.1f}")
    if quals:
        qs = sorted(quals)
        print(f"quality   min={min(qs):.2f}  median={qs[len(qs)//2]:.2f}  "
              f"max={max(qs):.2f}  mean={sum(qs)/len(qs):.2f}")

    # --- coverage ---------------------------------------------------------
    with_brand = sum(1 for r in good if r["fp"].get("brand"))
    verified = sum(1 for r in good
                   if r["fp"].get("brand_source") == "list")
    with_model = sum(1 for r in good if r["fp"].get("model_code"))
    flipped = sum(1 for r in good
                  if r["fp"]["stats"].get("orientation_flipped"))
    fallback = sum(1 for r in good if r["fp"].get("body", {}).get("full_frame"))

    print(f"\nbrand     {with_brand}/{len(good)} ({_pct(with_brand, len(good))})"
          f"   verified against list: {verified} ({_pct(verified, len(good))})")
    print(f"model     {with_model}/{len(good)} ({_pct(with_model, len(good))})")
    print(f"flipped   {flipped}/{len(good)} ({_pct(flipped, len(good))})")
    if fallback:
        print(f"fallback  {fallback} body/ies from the tight-crop fallback")

    # --- duplicate model codes -------------------------------------------
    # The model code is the fast path at match time, so two different remotes
    # carrying the same code is a correctness problem, not a curiosity.
    codes = Counter(r["fp"]["model_code"] for r in good
                    if r["fp"].get("model_code"))
    dupes = {c: n for c, n in codes.items() if n > 1}
    if dupes:
        print(f"\nduplicate model codes ({len(dupes)}):")
        for code, n in sorted(dupes.items(), key=lambda kv: -kv[1])[:15]:
            owners = [r["stem"] for r in good
                      if r["fp"].get("model_code") == code]
            print(f"   {code:<16} x{n}  {', '.join(owners[:4])}"
                  f"{' ...' if len(owners) > 4 else ''}")

    # --- flag tally -------------------------------------------------------
    tally = Counter(reason.split("(")[0].rstrip()
                    for r in records for _, reason in r["flags"])
    print(f"\nflagged   {len(flagged)}/{total} ({_pct(len(flagged), total)})")
    for reason, n in tally.most_common():
        print(f"   {n:>4}  {reason}")

    # --- review queue -----------------------------------------------------
    queue = sorted(flagged, key=_severity)[:top]
    if queue:
        print(f"\n{'-'*66}\n  review queue (worst {len(queue)})\n{'-'*66}")
        for r in queue:
            q = (r["fp"] or {}).get("extract_quality", 0.0)
            print(f"  {r['stem'][:44]:<44} q={q:.2f}  "
                  f"{'; '.join(reason for _, reason in r['flags'])}")


def write_csv(records: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["stem", "quality", "n_buttons", "n_chromatic",
                    "n_labelled", "label_recall", "aspect", "brand",
                    "brand_source", "model_code", "orientation_flipped",
                    "orientation_conf", "full_frame", "severity", "flags"])
        for r in sorted(records, key=_severity):
            fp = r["fp"] or {}
            st = fp.get("stats", {})
            bd = fp.get("body", {})
            w.writerow([
                r["stem"], fp.get("extract_quality", ""),
                st.get("n_buttons", ""), st.get("n_chromatic", ""),
                st.get("n_labelled", ""), st.get("label_recall", ""),
                bd.get("aspect", ""), fp.get("brand", ""),
                fp.get("brand_source", ""), fp.get("model_code", ""),
                st.get("orientation_flipped", ""),
                st.get("orientation_conf", ""),
                bd.get("full_frame", ""),
                max((s for s, _ in r["flags"]), default=0),
                "; ".join(reason for _, reason in r["flags"]),
            ])
    print(f"\ncsv -> {path}")


def collect_review(records: list[dict], debug_dir: Path, out_dir: Path,
                   top: int) -> None:
    """Copy the worst overlays into one directory, worst first.

    The whole point of session 1 was that bugs are found by looking at
    overlays. This puts the ones worth looking at in a single folder, named so
    they sort in priority order.
    """
    queue = sorted((r for r in records if r["flags"]), key=_severity)[:top]
    if not queue:
        print("\nnothing flagged for review")
        return
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.jpg"):
        old.unlink()

    copied = 0
    for i, r in enumerate(queue):
        src = debug_dir / f"{r['stem']}.jpg"
        if not src.exists():
            continue
        shutil.copy2(src, out_dir / f"{i:04d}_{r['stem']}.jpg")
        copied += 1
    print(f"\nreview overlays -> {out_dir}  ({copied} of {len(queue)} found)")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="audit extracted fingerprints and rank them for review")
    ap.add_argument("--fp", type=Path, required=True,
                    help="directory of fingerprint JSON (work/fp)")
    ap.add_argument("--csv", type=Path,
                    help="write the full per-record table here")
    ap.add_argument("--debug", type=Path,
                    help="directory of debug overlays (work/debug)")
    ap.add_argument("--review-dir", type=Path,
                    help="copy the worst overlays here, worst first")
    ap.add_argument("--top", type=int, default=50,
                    help="size of the review queue (default 50)")
    args = ap.parse_args()

    if not args.fp.is_dir():
        ap.error(f"not a directory: {args.fp}")

    records = load(args.fp)
    report(records, args.top)

    if args.csv:
        write_csv(records, args.csv)
    if args.review_dir:
        if not args.debug:
            ap.error("--review-dir needs --debug")
        collect_review(records, args.debug, args.review_dir, args.top)


if __name__ == "__main__":
    main()
