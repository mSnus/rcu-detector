# Correcting the button queue in Label Studio

Plan 9.1 step 2. The trained detector exists to find keys the classical
detector cannot, so the labels it learns from have to contain those keys —
which means a person adds them. `export_button_dataset.py` explains at length
why this cannot be automated away; read that first if the temptation to skip it
is still alive.

Roughly an hour per hundred records, correcting rather than drawing.

The whole loop below was run end to end against Label Studio 1.23.0 before it
was written down: 8 tasks imported with 172 pre-drawn boxes, one corrected by
hand, one submitted empty, exported, and imported back to YOLO labels with the
hand-drawn box landing exactly where it was put. Run
`scripts/check_label_roundtrip.py` before a session to assert that still holds.

## 1. Select the queue

```bash
cd service-python && source .venv/bin/activate

python scripts/export_button_dataset.py --fp ../work/fp --norm ../work/norm \
    --out ../work/dataset --split hard --queue-size 400
```

Writes `../work/dataset/images/hard/` and `hard_queue.tsv`. The queue is
sampled evenly across the quality range, not taken from the worst end: missed
keycaps do not lower the quality score, so the records most worth correcting
look fine by every number available.

## 2. Write the tasks

```bash
python scripts/label_queue.py export --fp ../work/fp --dataset ../work/dataset
```

Writes `label_studio_tasks.json`, one task per crop, each carrying the
classical detector's boxes as a **prediction**. You correct a mostly-right
layout instead of drawing 30 keycaps into an empty frame.

## 3. Run Label Studio

Not a project dependency — it is a labelling tool that runs for an afternoon,
not part of the service. Keep it out of `requirements.txt` and out of the
compose stack.

```bash
export DATASET=$(cd ../work/dataset && pwd)

docker run -it --rm -p 8080:8080 \
  -v "$DATASET":/label-studio/files:ro \
  -v "$DATASET/.label-studio":/label-studio/data \
  -e LABEL_STUDIO_LOCAL_FILES_SERVING_ENABLED=true \
  -e LABEL_STUDIO_LOCAL_FILES_DOCUMENT_ROOT=/label-studio/files \
  heartexlabs/label-studio:latest
```

Then at <http://localhost:8080>:

1. Create a project.
2. **Settings → Labeling Interface → Code**, and paste the config below. The
   `name` and `toName` are the contract with `label_queue.py`; changing them
   means changing `FROM_NAME`/`TO_NAME` there too.
3. **Settings → Cloud Storage → Add Source Storage → Local files**, absolute
   path **`/label-studio/files/images/hard`**. Do not sync it — the tasks
   reference the files themselves.
4. **Import** `label_studio_tasks.json`.

Step 3 is not optional and its path is not the obvious one. Without a
registered local storage, `/data/local-files/` returns **404 for every image**
and the tasks import perfectly, so the project looks fine until a labeller opens
one and sees an empty frame. And the path must be a *subdirectory* of the
document root: Label Studio rejects the root itself with `Absolute local path
cannot be the same as LOCAL_FILES_DOCUMENT_ROOT by security reasons`, which
does not obviously read as "use the images directory". Verified against
1.23.0 — 404 before adding the storage, 200 after.

```xml
<View>
  <Image name="image" value="$image" zoom="true" zoomControl="true"/>
  <RectangleLabels name="label" toName="image">
    <Label value="button" background="#ff5a5f"/>
  </RectangleLabels>
</View>
```

Port 8080 is the compose nginx on a box running this stack. Publish Label
Studio somewhere else there, or stop the stack first.

## 4. What to correct

The failure this dataset exists to fix is the **missing** box, not the
imprecise one:

- **Add** every keycap the detector missed. Matte keys on a matte body are the
  entire point. An unlabelled keycap is a *negative* to YOLO — leaving one
  teaches the model that low-contrast keys are background.
- **Delete** boxes that are not buttons: a screen-printed legend, the IR
  window, a recessed panel outline, a battery-door seam.
- **Do not** fuss over a few pixels of edge alignment. It costs time the queue
  does not have and IoU 0.9 against IoU 0.95 is not what is wrong with the
  detector.
- A D-pad is one box per pressable direction plus the centre, not one box for
  the ring.
- If a crop is not a remote at all — an instruction sheet, a promo banner —
  annotate it with **no boxes** and submit. That is a background sample and it
  is useful. Do not skip it: a skipped task is indistinguishable from one
  nobody reached.

## 5. Bring the corrections back

Export from Label Studio as **JSON** (the full export, not JSON-MIN — the
importer reads `annotations`), then:

The export contains only the tasks that have an annotation, which is what you
want: an untouched task carries no assertion about its buttons, and writing it
out as an empty label file would tell YOLO the crop is entirely background.

```bash
python scripts/label_queue.py import --export ../work/dataset/ls_export.json \
    --dataset ../work/dataset
```

Writes `labels/hard/*.txt` in YOLO format and `buttons_corrected.yaml`. Tasks
nobody annotated are skipped and counted, never written as empty label files:
an empty file does not mean "unknown" to YOLO, it means "this image is entirely
background".

The import is resumable. Export and import again after another session; only
the annotated tasks change.

## 6. Train

```bash
yolo detect train data=../work/dataset/buttons_corrected.yaml \
    model=yolov8n.pt epochs=100 imgsz=640 batch=16
yolo export model=runs/detect/train/weights/best.pt format=onnx
```

Then plan 9.1 steps 4 and 5: augment for the phone-photo domain, and rebuild
every catalog fingerprint with the new detector — a fingerprint from the old
detector and one from the new are not comparable, so the whole catalog and the
index are rebuilt together, exactly as after any extraction change.
