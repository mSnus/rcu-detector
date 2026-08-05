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
        everything = trusted + hard
        by_quality = sorted(everything, key=lambda t: quality_of(t[1]))

        # Stratified: walk the quality-ordered list at an even stride, so the
        # queue spans the range instead of clustering at one end.
        if args.queue_size and args.queue_size < len(by_quality):
            stride = len(by_quality) / args.queue_size
            picked = [by_quality[int(i * stride)] for i in range(args.queue_size)]
        else:
            picked = by_quality

        queue = []
        for p, fp in picked:
            crop = args.norm / f"{p.stem}.jpg"
            if not crop.is_file():
                continue
            shutil.copyfile(crop, hard_dir / crop.name)
            queue.append(f"{crop.name}\t{quality_of(fp):.3f}\t"
                         f"{len(buttons_of(fp))}")

        (args.out / "hard_queue.tsv").write_text(
            "image\tquality\tbuttons_found\n" + "\n".join(queue) + "\n"
        )
        print(f"labelling queue: {len(queue)} images -> {hard_dir}")
        print(f"  spanning quality "
              f"{quality_of(picked[0][1]):.2f}-{quality_of(picked[-1][1]):.2f}"
              if picked else "  (empty)")
        print(f"  manifest -> {args.out / 'hard_queue.tsv'}")


if __name__ == "__main__":
    main()
