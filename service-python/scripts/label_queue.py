"""Move the labelling queue between the pipeline and Label Studio (plan 9.1 step 2).

`export_button_dataset.py --split hard` selects *which* crops a person should
correct. This moves them out to a labeller and the corrections back, which is
the whole of step 2 and the gate on the trained detector.

The queue ships **with the classical detector's boxes attached as
predictions**, not as bare images. Correcting a mostly-right layout is minutes
per remote; drawing 30 keycaps from nothing is not, and the queue is 400
records. The point of the exercise is the keys thresholding missed, and those
are added to an existing layout rather than found in an empty one.

    # 1. select the queue (writes images/hard and hard_queue.tsv)
    python scripts/export_button_dataset.py --fp ../work/fp --norm ../work/norm \\
        --out ../work/dataset --split hard --queue-size 400

    # 2. write Label Studio tasks for it
    python scripts/label_queue.py export --fp ../work/fp \\
        --dataset ../work/dataset --out ../work/dataset/label_studio_tasks.json

    # 3. label. See label_studio.md next to this script.

    # 4. bring the corrections back as YOLO labels
    python scripts/label_queue.py import --export ../work/dataset/ls_export.json \\
        --dataset ../work/dataset

Coordinates convert in exactly one place (`ls_box` / `yolo_box` below), because
the two systems disagree twice over: the fingerprint stores centre-normalised
fractions, `(x + w/2)/W`, and Label Studio stores top-left percentages of the
image. Getting that wrong offsets every box by half its size, which looks
plausible on a dense keypad and is not.

Images are the rectified crops in `work/norm`, for the reason
`export_button_dataset.py` gives: they are what `detect_buttons` sees at build
and query time. A model trained on source photographs would expect a
preprocessing step the pipeline never performs.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.pipeline.imageio import read_image  # noqa: E402

# Label Studio needs a name for the tag pair in the labelling config, and the
# export carries it back. Change these only alongside label_studio.md.
FROM_NAME = "label"
TO_NAME = "image"
LABEL = "button"


def ls_box(x: float, y: float, w: float, h: float) -> dict:
    """Fingerprint box -> Label Studio value.

    Fingerprint: centre x/y, width/height, all fractions of the crop.
    Label Studio: top-left x/y, width/height, all *percentages*.
    """
    return {
        "x": (x - w / 2) * 100.0,
        "y": (y - h / 2) * 100.0,
        "width": w * 100.0,
        "height": h * 100.0,
        "rotation": 0,
        "rectanglelabels": [LABEL],
    }


def yolo_box(value: dict) -> tuple[float, float, float, float] | None:
    """Label Studio value -> YOLO `cx cy w h`, the inverse of `ls_box`.

    Returns None for a box that leaves the image. A labeller dragging a corner
    past the edge is ordinary, and YOLO rejects out-of-range coordinates, so
    clamp rather than discard -- except for a box that is entirely outside,
    which is a stray click.
    """
    w = float(value["width"]) / 100.0
    h = float(value["height"]) / 100.0
    x0 = float(value["x"]) / 100.0
    y0 = float(value["y"]) / 100.0

    x1, y1 = min(1.0, x0 + w), min(1.0, y0 + h)
    x0, y0 = max(0.0, x0), max(0.0, y0)
    if x1 <= x0 or y1 <= y0:
        return None

    return ((x0 + x1) / 2, (y0 + y1) / 2, x1 - x0, y1 - y0)


def image_size(path: Path) -> tuple[int, int] | None:
    """Width, height of a crop, decoded through the one decoder.

    Not PIL and not cv2.imread: a truncated JPEG decodes nondeterministically
    and `app/pipeline/imageio.py` is the only call site that appends the
    missing end-of-image marker first. Dimensions specifically would survive a
    second decoder -- which is exactly why a second decoder gets added here and
    then used for pixels later.
    """
    img = read_image(path)
    if img is None:
        return None
    return img.shape[1], img.shape[0]


def cmd_export(args: argparse.Namespace) -> int:
    hard_dir = args.dataset / "images" / "hard"
    if not hard_dir.is_dir():
        sys.exit(f"no labelling queue at {hard_dir} -- run export_button_dataset.py "
                 f"--split hard first")

    crops = sorted(hard_dir.glob("*.jpg"))
    if not crops:
        sys.exit(f"{hard_dir} is empty")

    tasks = []
    no_fp = 0
    unreadable = 0

    for crop in crops:
        fp_path = args.fp / f"{crop.stem}.json"
        if not fp_path.is_file():
            no_fp += 1
            continue
        fp = json.loads(fp_path.read_text())

        size = image_size(crop)
        if size is None:
            unreadable += 1
            continue
        width, height = size

        results = []
        for b in fp.get("buttons") or []:
            results.append({
                "from_name": FROM_NAME,
                "to_name": TO_NAME,
                "type": "rectanglelabels",
                "original_width": width,
                "original_height": height,
                "image_rotation": 0,
                "value": ls_box(b["x"], b["y"], b["w"], b["h"]),
            })

        tasks.append({
            "data": {
                # Served by Label Studio's local-files storage. The path is
                # relative to LOCAL_FILES_DOCUMENT_ROOT, which label_studio.md
                # sets to the dataset directory.
                "image": f"/data/local-files/?d={args.url_prefix}/{crop.name}",
                "record_id": crop.stem,
                "quality": round(float(fp.get("extract_quality") or 0.0), 3),
                "buttons_found": len(fp.get("buttons") or []),
            },
            "predictions": [{
                "model_version": "classical",
                "result": results,
            }],
        })

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(tasks, indent=1))

    boxes = sum(len(t["predictions"][0]["result"]) for t in tasks)
    print(f"{len(tasks)} task(s), {boxes} pre-drawn box(es) -> {args.out}")
    if no_fp:
        print(f"  {no_fp} crop(s) had no fingerprint and were left out")
    if unreadable:
        print(f"  {unreadable} crop(s) could not be decoded")
    empty = sum(1 for t in tasks if not t["predictions"][0]["result"])
    if empty:
        print(f"  {empty} task(s) start with no boxes at all -- these are the "
              f"records the detector found nothing on, and they are the point")
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    raw = json.loads(args.export.read_text())
    if not isinstance(raw, list):
        sys.exit("expected a Label Studio JSON export (a list of tasks)")

    lbl_dir = args.dataset / "labels" / "hard"
    img_dir = args.dataset / "images" / "hard"
    lbl_dir.mkdir(parents=True, exist_ok=True)

    written = 0
    untouched = 0
    deliberately_empty = 0
    dropped = 0
    total_boxes = 0

    for task in raw:
        data = task.get("data") or {}
        record = data.get("record_id")
        if not record:
            # JSON-MIN and some export options drop custom data fields; fall
            # back to the image path, which every export carries.
            image = str(data.get("image") or task.get("image") or "")
            record = Path(image.split("?d=")[-1]).stem
        if not record:
            dropped += 1
            continue

        annotations = task.get("annotations") or []
        # A task nobody opened has no annotation. It must NOT be written as an
        # empty label file: in YOLO that is not "unknown", it is "this crop is
        # entirely background", which is the single most damaging thing the
        # dataset could say. Skipping it also keeps the queue resumable.
        real = [a for a in annotations
                if not a.get("was_cancelled") and not a.get("skipped")]
        if not real:
            untouched += 1
            continue

        # Last annotation wins, which is what a second pass over the same task
        # means.
        result = real[-1].get("result") or []
        lines = []
        for r in result:
            if r.get("type") != "rectanglelabels":
                continue
            box = yolo_box(r["value"])
            if box is None:
                dropped += 1
                continue
            cx, cy, w, h = box
            lines.append(f"0 {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}")

        if not lines:
            # A person looked at this crop and drew nothing. That is a real
            # signal -- a background sample -- and unlike the untouched case it
            # is safe to write, because someone asserted it.
            deliberately_empty += 1

        (lbl_dir / f"{record}.txt").write_text("\n".join(lines) + ("\n" if lines else ""))
        written += 1
        total_boxes += len(lines)

    print(f"{written} corrected label file(s), {total_boxes} box(es) -> {lbl_dir}")
    if untouched:
        print(f"  {untouched} task(s) had no annotation and were skipped "
              f"(an empty label file would teach 'all background')")
    if deliberately_empty:
        print(f"  {deliberately_empty} task(s) were annotated with no boxes, "
              f"kept as background samples")
    if dropped:
        print(f"  {dropped} box(es) or task(s) dropped as unusable")

    if not written:
        sys.exit("nothing imported; has anything been labelled and exported?")

    # Only the corrected crops belong in the training set that this yaml names,
    # which is why it is written here and not by export_button_dataset.py: it
    # cannot exist until a person has been through the queue.
    missing = [p.stem for p in sorted(lbl_dir.glob("*.txt"))
               if not (img_dir / f"{p.stem}.jpg").is_file()]
    if missing:
        print(f"  warning: {len(missing)} label(s) have no image in {img_dir}")

    yaml = args.dataset / "buttons_corrected.yaml"
    yaml.write_text(
        "# Hand-corrected labels only. This is the set plan 9.1 step 3 trains\n"
        "# on, alone or alongside images/train -- never the uncorrected\n"
        "# pseudo-labels by themselves, which teach that a low-contrast keycap\n"
        "# is background.\n"
        f"path: {args.dataset.resolve()}\n"
        "train: images/hard\n"
        "val: images/val\n"
        "names:\n"
        "  0: button\n"
    )
    print(f"wrote {yaml}")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("export", help="write Label Studio tasks for the queue")
    e.add_argument("--fp", type=Path, required=True)
    e.add_argument("--dataset", type=Path, required=True,
                   help="export_button_dataset.py --out directory")
    e.add_argument("--out", type=Path, default=None)
    e.add_argument("--url-prefix", default="images/hard",
                   help="path to the crops relative to LOCAL_FILES_DOCUMENT_ROOT")
    e.set_defaults(fn=cmd_export)

    i = sub.add_parser("import", help="convert a Label Studio export to YOLO labels")
    i.add_argument("--export", type=Path, required=True,
                   help="the JSON Label Studio wrote (full export, not JSON-MIN)")
    i.add_argument("--dataset", type=Path, required=True)
    i.set_defaults(fn=cmd_import)

    args = ap.parse_args()
    if args.cmd == "export" and args.out is None:
        args.out = args.dataset / "label_studio_tasks.json"
    raise SystemExit(args.fn(args))


if __name__ == "__main__":
    main()
