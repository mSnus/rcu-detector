"""Export the classical detector's output as a YOLO dataset (plan 9.1 step 1).

The trained detector exists to find what `detect_buttons` cannot: matte black
keys on a matte black body. So the one thing this export must not do is teach a
model to reproduce the classical detector, failures included.

The obvious shortcut is to skip the hand-labelling: train only on the records
the classical detector handles well and let the network generalise to the ones
it handles badly. **That does not work, and it was measured before this file
was finished.** Rendering the exported labels back onto their crops shows that
high-quality extractions are still systematically incomplete -- `CAS-400_0` at
quality 0.91 has its entire lower numeric keypad unboxed, grey keys on a grey
panel, and `1CE3-copy_0` at 0.92 misses the D-pad ring. In YOLO an unlabelled
keycap is not neutral, it is a negative, so those files would train the model
that low-contrast keys are background: the exact opposite of the point.

Two automatic completeness tests were tried and both fail on that same record:

  * orphan text -- OCR regions not inside any box. CAS-400 scores a low 0.25,
    because OCR missed the grey legends too. The signal and the failure share
    a cause, so it cannot detect its own blind spot.
  * interior coverage gap -- the longest empty horizontal band between occupied
    ones. CAS-400 scores 0.00, because its missing cluster is at the bottom of
    the crop and a trailing gap is indistinguishable from the body's margin.

So there is no way to tell from a fingerprint whether the detector found
everything, which is why plan 9.1 step 2 says hand-correct and why that step is
not optional. What this script does is make that hour count.

  * `--split train` writes the pseudo-labelled set. It is a **starting point
    for correction, not a training set** -- read the warning above before
    pointing `yolo train` at it.
  * `--split hard` writes the labelling queue. Because incompleteness is NOT
    predicted by quality, the queue is sampled across the whole quality range
    rather than taken from the bottom of it: labelling only the worst records
    would systematically miss the CAS-400 case, which looks fine by every
    number available.

The images exported are the rectified crops in `<work>/norm`, not the source
photographs, because that is what `detect_buttons` runs on at both build and
query time. Training on the source frames would teach the model a preprocessing
step the pipeline never performs. Same family of mistake as calibrating a
watermark rule on full images when OCR sees the crop.

    python scripts/export_button_dataset.py --fp ../work/fp --norm ../work/norm \\
        --out ../work/dataset --val-frac 0.15

Then, after a person has corrected the hard split:

    yolo detect train data=../work/dataset/buttons.yaml model=yolov8n.pt \\
        epochs=100 imgsz=640 batch=16
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def quality_of(fp: dict) -> float:
    return float(fp.get("extract_quality") or 0.0)


def buttons_of(fp: dict) -> list[dict]:
    return fp.get("buttons") or []


def orphan_legends(fp: dict) -> int:
    """Legends with no detected keycap under them.

    OCR reads a legend whether or not detection found the key it is printed
    on, so a legend-shaped text region with no button near it is direct
    evidence of a missed button -- the one thing `extract_quality` cannot see.
    `RC4849_0` scores 0.923 having lost 40% of its buttons; its orphan share is
    0.24 against 0.06 for the same remote extracted properly.
    """
    from app.config import CFG
    r, cap = CFG.label.max_assign_dist, CFG.label.caption_width_frac
    bs = buttons_of(fp)
    n = 0
    for t in fp.get("text_regions") or []:
        if t.get("w", 0.0) >= cap:
            continue                      # a caption strip, not a legend
        if not any(abs(b["x"] - t["x"]) < r and abs(b["y"] - t["y"]) < r
                   for b in bs):
            n += 1
    return n


def orphan_share(fp: dict) -> float:
    o = orphan_legends(fp)
    return o / max(1, len(buttons_of(fp)) + o)


def is_printed_page(fp: dict) -> bool:
    """A crop whose legends outnumber its keys is a page, not a remote.

    `2098_1` is a VCR code table photographed beside the remote: 62 stored text
    regions against 44 "buttons", and 50 orphan legends that are table rows.
    It tops the orphan ranking and a labeller sent it would label nothing, so
    it is excluded from the queue rather than allowed to crowd out real
    detection failures. (That such crops are records at all is a separate
    defect -- the density rule cannot see them because they are full-size.)
    """
    nb = len(buttons_of(fp))
    return bool(nb) and len(fp.get("text_regions") or []) > nb


def yolo_lines(fp: dict) -> list[str]:
    """One `class cx cy w h` line per button, all normalised to the crop.

    The fingerprint already stores centre-normalised boxes -- `detect_buttons`
    writes `(x + w/2) / W` -- so this is a copy, not a conversion. Do not
    "fix" it into a corner-based form.
    """
    out = []
    for b in buttons_of(fp):
        x, y, w, h = b["x"], b["y"], b["w"], b["h"]
        # A box the detector placed partly outside the crop is a detection
        # artefact, not a keycap, and YOLO rejects out-of-range coordinates.
        if not (0 <= x <= 1 and 0 <= y <= 1 and 0 < w <= 1 and 0 < h <= 1):
            continue
        if x - w / 2 < -0.01 or x + w / 2 > 1.01:
            continue
        if y - h / 2 < -0.01 or y + h / 2 > 1.01:
            continue
        out.append(f"0 {x:.6f} {y:.6f} {w:.6f} {h:.6f}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fp", type=Path, required=True)
    ap.add_argument("--norm", type=Path, required=True,
                    help="rectified crops; what detect_buttons actually sees")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--val-frac", type=float, default=0.15)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--good-quality", type=float, default=0.80,
                    help="at or above this, the extraction is trusted as a "
                         "pseudo-label")
    ap.add_argument("--hard-quality", type=float, default=0.55,
                    help="below this, the record goes to the labelling queue "
                         "instead")
    ap.add_argument("--min-buttons", type=int, default=5,
                    help="a trusted record must have found at least this many. "
                         "A high-quality score on 2 buttons usually means the "
                         "detector found the two it could see and missed the "
                         "rest, which is a false label, not a sparse remote.")
    ap.add_argument("--queue-orphan-frac", type=float, default=0.5,
                    help="fraction of the labelling queue drawn from the "
                         "records with the most legends left with no keycap "
                         "under them; the rest is stratified by quality")
    ap.add_argument("--split", choices=["all", "train", "hard"], default="all")
    ap.add_argument("--queue-size", type=int, default=400,
                    help="how many images to put in the labelling queue "
                         "(plan 9.1 asks for ~400). Sampled evenly across the "
                         "quality range, not taken from the bottom of it. 0 "
                         "queues everything.")
    args = ap.parse_args()

    fps = sorted(args.fp.glob("*.json"))
    if not fps:
        sys.exit(f"no fingerprints in {args.fp}")

    trusted: list[tuple[Path, dict]] = []
    hard: list[tuple[Path, dict]] = []
    skipped = 0

    for p in fps:
        fp = json.loads(p.read_text())
        q, n = quality_of(fp), len(buttons_of(fp))

        if q >= args.good_quality and n >= args.min_buttons:
            trusted.append((p, fp))
        elif q < args.hard_quality:
            hard.append((p, fp))
        else:
            # Deliberately neither. In the middle band the extraction is not
            # good enough to believe and not bad enough to be worth a person's
            # time, and a pseudo-label that is mostly right is the worst kind.
            skipped += 1

    print(f"{len(fps)} fingerprints: {len(trusted)} trusted, {len(hard)} hard, "
          f"{skipped} in the middle band (neither)")

    if not trusted and args.split != "hard":
        sys.exit("no records cleared --good-quality; nothing to train on")

    rng = random.Random(args.seed)

    if args.split in ("all", "train"):
        rng.shuffle(trusted)
        n_val = max(1, int(len(trusted) * args.val_frac)) if trusted else 0
        splits = {"val": trusted[:n_val], "train": trusted[n_val:]}

        written = {"train": 0, "val": 0}
        empty = 0
        for split, rows in splits.items():
            img_dir = args.out / "images" / split
            lbl_dir = args.out / "labels" / split
            img_dir.mkdir(parents=True, exist_ok=True)
            lbl_dir.mkdir(parents=True, exist_ok=True)

            for p, fp in rows:
                crop = args.norm / f"{p.stem}.jpg"
                if not crop.is_file():
                    continue
                lines = yolo_lines(fp)
                if not lines:
                    # An image with no boxes is a valid YOLO background sample,
                    # but a *trusted* record with no boxes is a contradiction.
                    empty += 1
                    continue
                shutil.copyfile(crop, img_dir / crop.name)
                (lbl_dir / f"{p.stem}.txt").write_text("\n".join(lines) + "\n")
                written[split] += 1

        print(f"train {written['train']} images, val {written['val']} images"
              + (f", {empty} trusted record(s) had no usable boxes" if empty else ""))

        (args.out / "buttons.yaml").write_text(
            "# Pseudo-labels from the classical detector, high-quality "
            "extractions only.\n"
            "# The hard split in labels/hard is NOT included: correct it by "
            "hand first,\n"
            "# then add it as another train directory.\n"
            f"path: {args.out.resolve()}\n"
            "train: images/train\n"
            "val: images/val\n"
            "names:\n"
            "  0: button\n"
        )
        print(f"wrote {args.out / 'buttons.yaml'}")

    if args.split in ("all", "hard"):
        hard_dir = args.out / "images" / "hard"
        hard_dir.mkdir(parents=True, exist_ok=True)

        # Every record is a candidate, not just the low-quality ones. Missed
        # keycaps do not lower the quality score -- CAS-400_0 lost a whole
        # keypad at 0.91 -- so a queue drawn from the bottom of the range would
        # never show a labeller the failure the detector most needs corrected.
        everything = [t for t in trusted + hard if not is_printed_page(t[1])]
        n_pages = len(trusted) + len(hard) - len(everything)

        # Half the queue is the failure the detector exists to fix, half is a
        # spread across the catalogue.
        #
        # Ordering by quality alone cannot find the first half: every one of
        # the worst orphan records scores 0.81-0.95, because a missed keycap
        # does not lower the quality score. `242254901404_0` is a black-on-black
        # Philips with 22 buttons found and 21 legends left with no key under
        # them, at quality 0.933 -- a labeller walking a quality-stratified
        # queue would never be shown it.
        #
        # But not orphans alone either: a training set drawn entirely from one
        # failure mode teaches the detector that mode and nothing else.
        n_targeted = int(args.queue_size * args.queue_orphan_frac)
        by_orphan = sorted(everything, key=lambda t: -orphan_share(t[1]))
        targeted = by_orphan[:n_targeted]

        rest = by_orphan[n_targeted:]
        by_quality = sorted(rest, key=lambda t: quality_of(t[1]))
        n_spread = max(0, args.queue_size - len(targeted))
        if n_spread and n_spread < len(by_quality):
            stride = len(by_quality) / n_spread
            spread = [by_quality[int(i * stride)] for i in range(n_spread)]
        else:
            spread = by_quality[:n_spread]
        picked = targeted + spread
        print(f"queue: {len(targeted)} by orphan legends, {len(spread)} "
              f"stratified by quality, {n_pages} printed page(s) excluded")

        queue = []
        for p, fp in picked:
            crop = args.norm / f"{p.stem}.jpg"
            if not crop.is_file():
                continue
            shutil.copyfile(crop, hard_dir / crop.name)
            queue.append(f"{crop.name}\t{quality_of(fp):.3f}\t"
                         f"{len(buttons_of(fp))}\t{orphan_legends(fp)}\t"
                         f"{orphan_share(fp):.3f}")

        (args.out / "hard_queue.tsv").write_text(
            "image\tquality\tbuttons_found\torphan_legends\torphan_share\n"
            + "\n".join(queue) + "\n"
        )
        print(f"labelling queue: {len(queue)} images -> {hard_dir}")
        print(f"  spanning quality "
              f"{quality_of(picked[0][1]):.2f}-{quality_of(picked[-1][1]):.2f}"
              if picked else "  (empty)")
        print(f"  manifest -> {args.out / 'hard_queue.tsv'}")


if __name__ == "__main__":
    main()
