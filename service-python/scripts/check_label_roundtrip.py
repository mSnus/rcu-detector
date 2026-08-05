"""Assert that a box survives the trip out to Label Studio and back.

The two systems disagree twice over: a fingerprint stores a button as centre
x/y and size, as fractions of the crop; Label Studio stores a rectangle as
top-left x/y and size, as percentages. A conversion that gets that wrong
offsets every box by half its own size, which on a dense keypad still looks
like a plausible set of buttons — and would be discovered after an afternoon of
labelling, in the form of a detector that finds keys half a key up and to the
left.

So this simulates the whole loop with no labeller in it: export the tasks,
accept every prediction verbatim as if a person had pressed submit, import, and
compare the resulting YOLO labels against the fingerprints they started as.
Anything that is not the identity is a bug in `label_queue.py`.

    python scripts/check_label_roundtrip.py --fp ../work/fp --norm ../work/norm

Exits non-zero on violation. Run it before a labelling session, not after.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

HERE = Path(__file__).resolve().parent

# A box the detector placed a hair outside the crop is clamped to the edge on
# the way back, deliberately: Label Studio and YOLO both work in-frame. That
# moves its centre by half the overhang, so the identity cannot be exact for
# those. The overhang `export_button_dataset.py` tolerates is 0.01 of the crop,
# so half of it is the most a legitimate clamp can move anything.
CLAMP_TOLERANCE = 0.005
EXACT = 1e-6


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fp", type=Path, required=True)
    ap.add_argument("--norm", type=Path, required=True)
    ap.add_argument("--keep", type=Path, default=None,
                    help="keep the scratch dataset here instead of a temp dir")
    args = ap.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        ds = Path(args.keep) if args.keep else Path(tmp) / "dataset"

        def run(*cmd: str) -> None:
            r = subprocess.run([sys.executable, *cmd], capture_output=True, text=True)
            if r.returncode != 0:
                sys.exit(f"{' '.join(cmd)} failed:\n{r.stdout}\n{r.stderr}")

        run(str(HERE / "export_button_dataset.py"), "--fp", str(args.fp),
            "--norm", str(args.norm), "--out", str(ds), "--split", "hard",
            "--queue-size", "0")
        run(str(HERE / "label_queue.py"), "export", "--fp", str(args.fp),
            "--dataset", str(ds))

        tasks = json.loads((ds / "label_studio_tasks.json").read_text())
        if not tasks:
            sys.exit("no tasks were exported; is the queue empty?")

        # What Label Studio writes when a labeller accepts a prediction as it
        # stands. Plus the two cases that must NOT be treated alike: a task
        # submitted with no boxes (a background sample, keep it) and a task
        # nobody opened (unknown, skip it).
        export = [{"id": i, "data": t["data"],
                   "annotations": [{"id": i, "was_cancelled": False,
                                    "result": t["predictions"][0]["result"]}]}
                  for i, t in enumerate(tasks)]
        export[0]["annotations"][0]["result"] = []
        untouched = export[-1]["data"]["record_id"] if len(export) > 1 else None
        if untouched:
            export[-1]["annotations"] = []

        ls_export = ds / "ls_export.json"
        ls_export.write_text(json.dumps(export))

        run(str(HERE / "label_queue.py"), "import", "--export", str(ls_export),
            "--dataset", str(ds))

        lbl = ds / "labels" / "hard"
        drift = 0.0
        clamped = 0
        checked = 0
        bad: list[str] = []

        for task in export[1:] if untouched is None else export[1:-1]:
            rec = task["data"]["record_id"]
            fp = json.loads((args.fp / f"{rec}.json").read_text())
            want = [(b["x"], b["y"], b["w"], b["h"]) for b in fp["buttons"]]

            out = lbl / f"{rec}.txt"
            if not out.is_file():
                bad.append(f"{rec}: no label file was written")
                continue
            got = [tuple(float(v) for v in line.split()[1:])
                   for line in out.read_text().splitlines()]

            if len(want) != len(got):
                bad.append(f"{rec}: {len(want)} boxes in, {len(got)} out")
                continue

            for a, b in zip(want, got):
                d = max(abs(x - y) for x, y in zip(a, b))
                checked += 1
                if d <= EXACT:
                    continue
                # Only a box that started outside the crop may move at all.
                cx, cy, w, h = a
                outside = (cx - w / 2 < 0 or cx + w / 2 > 1
                           or cy - h / 2 < 0 or cy + h / 2 > 1)
                if outside and d <= CLAMP_TOLERANCE:
                    clamped += 1
                    drift = max(drift, d)
                    continue
                bad.append(f"{rec}: box moved by {d:.2e} "
                           f"({'edge' if outside else 'interior'} box {a})")

        # The distinction the importer exists to make, checked rather than
        # assumed: an annotated-empty task is a background sample and must be
        # written; an untouched task is unknown and must not, because an empty
        # label file tells YOLO the crop is entirely background.
        bg = lbl / f"{export[0]['data']['record_id']}.txt"
        if not bg.is_file():
            bad.append("a task annotated with no boxes was not written "
                       "(background samples are real data)")
        elif bg.read_text().strip():
            bad.append("a task annotated with no boxes came back with boxes")
        if untouched and (lbl / f"{untouched}.txt").is_file():
            bad.append(f"{untouched}: an unannotated task was written as an "
                       f"empty label, which teaches 'all background'")

    print(f"{len(export)} task(s), {checked} box(es) round-tripped")
    print(f"{clamped} box(es) clamped to the crop edge, worst move {drift:.2e}"
          if clamped else "no box needed clamping")

    if bad:
        print(f"\n!! {len(bad)} problem(s):")
        for line in bad[:20]:
            print(f"     {line}")
        print("\nFAIL -- see ls_box/yolo_box in scripts/label_queue.py")
        return 1

    print("\nOK -- coordinates and the empty/untouched distinction both hold")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
