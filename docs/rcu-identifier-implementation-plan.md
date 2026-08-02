# RCU Identifier — Implementation Plan

Photograph a TV remote control, identify its model from a catalog of 10k–50k records.

**Core approach:** structural fingerprinting. Each remote is reduced to a normalised
point set of buttons (position, size, shape, colour, label) plus text regions
(brand, model code). Matching is two-tier: fast inverted-index retrieval by rare
feature tokens, then geometric verification by RANSAC.

---

## 0. Architecture Overview

### 0.1 Components

| Component | Technology | Responsibility |
|---|---|---|
| Client | Browser PWA (Android wrapper later) | Camera capture, framing guide, quality check, upload, result picker |
| Web backend | Laravel / PHP | Auth, upload handling, catalog DB, result storage, feedback, admin UI |
| Recognition service | Python 3.11 + FastAPI | Detection, normalisation, OCR, fingerprint extraction, matching |
| Catalog DB | MySQL (existing) | Brands, models, images, fingerprints |
| Index | In-memory numpy postings, persisted to `.npz` | Inverted index of feature tokens |

Laravel never does computer vision. It calls the Python service over internal HTTP.

### 0.2 Runtime data flow

```
[Client] capture photo
    │ POST /api/identify (multipart)
    ▼
[Laravel] validate, store original, generate request_id
    │ POST http://127.0.0.1:8600/identify
    ▼
[Python service]
    1. detect remote body        → crop
    2. rectify perspective       → canonical upright rectangle
    3. detect buttons            → point set
    4. classify colour + shape
    5. OCR labels + text regions
    6. extract brand / model code
    7. assemble fingerprint
    8. Tier 1: token retrieval   → top 100 candidates
    9. Tier 2: RANSAC verify     → top 5 scored
    ▼
[Laravel] persist result, return candidates + confidence
    ▼
[Client] show answer or top-5 picker; user taps → feedback stored
```

### 0.3 Offline data flow (catalog build)

Runs once, then incrementally for new records. No time pressure — use the slowest,
most accurate settings available.

```
catalog image → dewatermark → split multi-remote → detect body → rectify
             → detect buttons (high-recall settings, ensemble)
             → OCR (3 engines, consensus) → assemble fingerprint → store JSON
             → rebuild token index
```

### 0.4 Repository layout

```
rcu-identifier/
├── backend-laravel/            # existing or new Laravel app
├── service-python/
│   ├── app/
│   │   ├── main.py             # FastAPI entrypoint
│   │   ├── pipeline/
│   │   │   ├── detect.py       # body + button detection
│   │   │   ├── normalize.py    # rectification
│   │   │   ├── color.py        # colour bucketing
│   │   │   ├── ocr.py          # text + symbol recognition
│   │   │   ├── fingerprint.py  # assembly
│   │   │   └── textid.py       # brand / model code extraction
│   │   ├── matching/
│   │   │   ├── tokens.py       # token generation
│   │   │   ├── index.py        # inverted index + IDF
│   │   │   ├── verify.py       # RANSAC geometric verification
│   │   │   └── fuse.py         # score fusion
│   │   └── config.py
│   ├── scripts/
│   │   ├── build_catalog.py    # offline batch build
│   │   ├── build_index.py
│   │   ├── evaluate.py
│   │   └── review_export.py
│   ├── models/                 # ONNX weights
│   ├── data/                   # index artefacts
│   └── requirements.txt
└── docs/
```

---

## 1. Setting Up the Backend

### 1.1 Server prerequisites

Target: Ubuntu 22.04, 4+ CPU cores, 8 GB RAM minimum (16 GB for the catalog build).
GPU optional — helpful for the batch build, unnecessary for serving.

```bash
sudo apt update
sudo apt install -y build-essential git curl \
    libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 \
    python3.11 python3.11-venv python3.11-dev \
    mysql-client nginx
```

`libgl1` and `libglib2.0-0` are required by OpenCV; missing them is the single most
common cause of the `ImportError: libGL.so.1` failure on headless servers.

### 1.2 Install Python and create the virtual environment

```bash
sudo mkdir -p /opt/rcu
sudo chown $USER:$USER /opt/rcu
cd /opt/rcu
git clone <your-repo> .
cd service-python

python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip wheel setuptools
```

### 1.3 Install recognition modules

`requirements.txt`:

```
# core
numpy==1.26.4
scipy==1.13.1
opencv-python-headless==4.10.0.84
Pillow==10.4.0
shapely==2.0.5
scikit-learn==1.5.1
scikit-image==0.24.0

# service
fastapi==0.115.0
uvicorn[standard]==0.30.6
python-multipart==0.0.9
pydantic==2.9.2

# inference
onnxruntime==1.19.2          # swap for onnxruntime-gpu if a GPU is present
ultralytics==8.3.0           # YOLO training + export (phase 9)

# OCR
paddlepaddle==2.6.1
paddleocr==2.8.1

# storage
SQLAlchemy==2.0.35
PyMySQL==1.1.1
orjson==3.10.7

# batch build
tqdm==4.66.5
joblib==1.4.2
```

```bash
pip install -r requirements.txt
python -c "import cv2, paddleocr; print('ok')"
```

**If PaddleOCR gives trouble** (it is the most fragile dependency here), the
fallback order is: `easyocr` → `doctr` → Tesseract via `pytesseract`. Keep the OCR
call behind an interface in `pipeline/ocr.py` so the engine is swappable — you will
want engine ensembling for the catalog build regardless.

### 1.4 First-run model download

PaddleOCR downloads weights on first use. Do this once during setup rather than on
the first user request:

```bash
python - <<'PY'
from paddleocr import PaddleOCR
ocr = PaddleOCR(use_angle_cls=True, lang='en', show_log=False)
print("warm")
PY
```

### 1.5 Storage layout

```bash
sudo mkdir -p /var/lib/rcu/{catalog_raw,catalog_norm,uploads,debug,index}
sudo chown -R www-data:www-data /var/lib/rcu
```

- `catalog_raw` — original scraped images
- `catalog_norm` — rectified canonical crops (one per physical remote)
- `uploads` — user photos, retained for the feedback loop
- `debug` — annotated overlays for the admin visualiser
- `index` — `.npz` token index artefacts

### 1.6 Run the service under systemd

`/etc/systemd/system/rcu-service.service`:

```ini
[Unit]
Description=RCU recognition service
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/rcu/service-python
Environment="PYTHONUNBUFFERED=1"
ExecStart=/opt/rcu/service-python/.venv/bin/uvicorn app.main:app \
    --host 127.0.0.1 --port 8600 --workers 2
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now rcu-service
curl http://127.0.0.1:8600/health
```

Bind to `127.0.0.1` only. The service has no authentication of its own and must not
be reachable from outside.

Note on workers: each worker holds its own copy of the in-memory index. At 50k
models the index is roughly 200–400 MB, so 2 workers ≈ 800 MB. Do not raise the
worker count without checking RAM.

### 1.7 Nginx

Only Laravel is exposed publicly. Increase the upload limit for photos:

```nginx
client_max_body_size 12M;
```

---

## 2. Database Schema

### 2.1 New tables

Keep your existing catalog tables untouched; add alongside them.

```sql
-- one row per physical remote crop (a catalog image may yield several)
CREATE TABLE rcu_fingerprints (
    id              BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    model_id        BIGINT UNSIGNED NOT NULL,   -- FK to your existing models table
    source_image    VARCHAR(512) NOT NULL,
    crop_index      TINYINT UNSIGNED DEFAULT 0, -- 0,1 for two-remote images
    norm_path       VARCHAR(512) NOT NULL,      -- rectified canonical crop
    aspect_ratio    FLOAT NOT NULL,
    button_count    SMALLINT UNSIGNED NOT NULL,
    fingerprint     JSON NOT NULL,
    brand_text      VARCHAR(128) NULL,
    model_text      VARCHAR(128) NULL,
    quality_score   FLOAT NOT NULL DEFAULT 0,   -- extraction confidence
    reviewed        TINYINT(1) NOT NULL DEFAULT 0,
    built_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_model (model_id),
    INDEX idx_quality (quality_score),
    INDEX idx_model_text (model_text)
) ENGINE=InnoDB;

-- physical-mould clusters: several brands may sell the identical remote
CREATE TABLE rcu_clusters (
    id              BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    canonical_fp_id BIGINT UNSIGNED NOT NULL,
    member_count    SMALLINT UNSIGNED NOT NULL
) ENGINE=InnoDB;

CREATE TABLE rcu_cluster_members (
    cluster_id      BIGINT UNSIGNED NOT NULL,
    fingerprint_id  BIGINT UNSIGNED NOT NULL,
    PRIMARY KEY (cluster_id, fingerprint_id)
) ENGINE=InnoDB;

-- recognition requests and outcomes
CREATE TABLE rcu_queries (
    id              BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    request_id      CHAR(36) NOT NULL UNIQUE,
    upload_path     VARCHAR(512) NOT NULL,
    candidates      JSON NULL,        -- top-5 with scores
    top_score       FLOAT NULL,
    confidence      ENUM('high','medium','low','none') NOT NULL DEFAULT 'none',
    chosen_model_id BIGINT UNSIGNED NULL,   -- user's pick = ground truth
    latency_ms      INT UNSIGNED NULL,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_created (created_at),
    INDEX idx_chosen (chosen_model_id)
) ENGINE=InnoDB;
```

### 2.2 Fingerprint JSON structure

```json
{
  "v": 1,
  "body": { "aspect": 3.42, "corner_r": 0.06, "shape": "rounded_rect" },
  "buttons": [
    { "x": 0.19, "y": 0.071, "w": 0.20, "h": 0.038,
      "shape": "ellipse", "color": "grey",
      "label": "POWER", "label_pos": "above", "conf": 0.93 },
    { "x": 0.50, "y": 0.512, "w": 0.34, "h": 0.052,
      "shape": "ellipse", "color": "grey",
      "label": "\u25b6", "label_pos": "on", "conf": 0.81 },
    { "x": 0.21, "y": 0.660, "w": 0.13, "h": 0.030,
      "shape": "ellipse", "color": "orange",
      "label": null, "label_pos": null, "conf": 0.88 }
  ],
  "text_regions": [
    { "x": 0.50, "y": 0.905, "size": 0.022, "text": "REMOTE CONTROLLER" }
  ],
  "brand": null,
  "model_code": null,
  "extract_quality": 0.79
}
```

All coordinates are relative to the rectified body: `x, y ∈ [0,1]`, origin at the
top-left of the remote body, `y` running toward the bottom. `w` and `h` are relative
to body width and height respectively.

### 2.3 Laravel migrations

Mirror the SQL above as Laravel migrations so schema changes stay versioned:

```bash
cd backend-laravel
php artisan make:migration create_rcu_fingerprints_table
php artisan make:migration create_rcu_queries_table
php artisan migrate
```

Use `$table->json('fingerprint')` and `$table->char('request_id', 36)->unique()`.

---

## 3. Catalog Preparation (Offline Batch)

This is the largest single chunk of work and the one that determines your accuracy
ceiling. Budget more time here than for anything else.

### 3.1 Inventory and audit

Before writing pipeline code, measure what you actually have.

```bash
python scripts/audit_catalog.py --dir /var/lib/rcu/catalog_raw --out audit.csv
```

The audit should report, per image: dimensions, aspect ratio, background uniformity
(std-dev of the border pixels), estimated remote count, presence of a watermark
signature, presence of a caption strip. Then histogram the results.

**Decision gates from the audit:**
- If >90% have uniform light backgrounds → classical detection is enough for phase 1
- If watermark signatures cluster into <10 groups → template-based removal is viable
- Count multi-remote images → sizes the splitting work

### 3.2 Watermark removal (skip if you obtain clean sources)

You expect clean images, so treat this as contingency. If needed:

1. Cluster images by source (URL domain, or by watermark pixel signature)
2. For each cluster, average 200 images to reveal the static watermark → threshold
   into a binary mask
3. Inpaint: `cv2.inpaint(img, mask, 5, cv2.INPAINT_TELEA)` for speed, or LaMa for
   quality
4. Always add known watermark strings to the OCR blocklist, regardless of removal

### 3.3 Body detection and multi-remote splitting

Phase 1 uses classical CV, which works well on light backgrounds:

```python
def detect_bodies(img):
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _, th = cv2.threshold(blur, 0, 255,
                          cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    th = cv2.morphologyEx(th, cv2.MORPH_CLOSE,
                          cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15)))
    contours, _ = cv2.findContours(th, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
    out = []
    img_area = img.shape[0] * img.shape[1]
    for c in contours:
        area = cv2.contourArea(c)
        if area < 0.03 * img_area:          # reject captions, logos, noise
            continue
        rect = cv2.minAreaRect(c)
        (w, h) = rect[1]
        if min(w, h) == 0:
            continue
        if not (1.8 < max(w, h) / min(w, h) < 8.0):   # remotes are elongated
            continue
        out.append(rect)
    return sorted(out, key=lambda r: r[0][0])   # left to right
```

Each detected body becomes its own `rcu_fingerprints` row with an incrementing
`crop_index`. Two colourways of one model produce two rows pointing at the same
`model_id` — this is desirable, not a duplicate.

**Caption strips** (e.g. the `RC-51A` label under a photo) are rejected by the area
and aspect filters above, but OCR them separately: they are a free cross-check
against your DB's model field. Log every mismatch to a review queue; scraped
catalogs typically contain a meaningful number of wrong associations.

### 3.4 Rectification to canonical form

```python
def rectify(img, rect):
    box = cv2.boxPoints(rect)
    box = order_corners(box)                    # tl, tr, br, bl
    (w, h) = rect[1]
    if w > h:                                   # force portrait orientation
        w, h = h, w
        box = np.roll(box, 1, axis=0)
    W, H = 400, int(400 * h / w)
    dst = np.float32([[0, 0], [W, 0], [W, H], [0, H]])
    M = cv2.getPerspectiveTransform(box.astype(np.float32), dst)
    return cv2.warpPerspective(img, M, (W, H)), M
```

**Orientation disambiguation is mandatory** — a remote photographed upside down must
not produce a mirrored fingerprint. Resolve it with, in order of reliability:

1. IR emitter end — usually a small dark window at the narrow top
2. Numeric keypad reading order — if digits are OCR'd, `1` sits above `9`
3. Text baseline direction from OCR
4. Button density — the top half is typically sparser

If all four are inconclusive, generate **both** orientations as separate index
entries at build time and query both at match time. Doubling the index is cheap;
being wrong is not.

### 3.5 Button detection

Phase 1, classical:

```python
def detect_buttons(norm_img):
    gray = cv2.cvtColor(norm_img, cv2.COLOR_BGR2GRAY)
    # adaptive threshold handles the grey-on-grey and black-on-black cases
    th = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                               cv2.THRESH_BINARY_INV, 31, 5)
    th = cv2.morphologyEx(th, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(th, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
    H, W = gray.shape
    buttons = []
    for c in contours:
        a = cv2.contourArea(c)
        if not (0.0004 * H * W < a < 0.06 * H * W):
            continue
        x, y, w, h = cv2.boundingRect(c)
        fill = a / float(w * h)
        if fill < 0.5:                  # reject text strokes and thin lines
            continue
        buttons.append(dict(
            x=(x + w / 2) / W, y=(y + h / 2) / H,
            w=w / W, h=h / H,
            shape=classify_shape(c, w, h),
        ))
    return dedupe_overlapping(buttons)
```

For the offline build run this at **three threshold settings** and merge by
intersection-over-union, keeping any button found by at least two. High recall
matters more than precision here — a spurious button costs one bad token; a missed
one loses several real ones.

Phase 9 replaces this with a trained detector, which is the main accuracy upgrade.

### 3.6 Colour bucketing

Coarse names only, as you proposed. Sample the median HSV over the inner 60% of each
button to avoid rim highlights:

```python
BUCKETS = {                  # hue ranges in OpenCV's 0..179 scale
    "red":    [(0, 8), (172, 179)],
    "orange": [(9, 22)],
    "yellow": [(23, 33)],
    "green":  [(34, 85)],
    "blue":   [(86, 130)],
    "purple": [(131, 158)],
}

def bucket(h, s, v):
    if v < 60:  return "black"
    if s < 45:  return "white" if v > 190 else "grey"
    for name, ranges in BUCKETS.items():
        if any(lo <= h <= hi for lo, hi in ranges):
            return name
    return "grey"
```

Calibrate the `s < 45` and `v < 60` cut-offs against real phone photos, not catalog
images — white balance under tungsten light shifts everything.

### 3.7 OCR of labels and symbols

Two distinct problems, handled separately:

**Text labels** (POWER, MENU, VOL, digits). Run OCR over the whole normalised crop,
then assign each detected text box to the nearest button and derive `label_pos`:

```python
def assign_label(text_box, buttons):
    tb = center(text_box)
    best, best_d = None, 1e9
    for b in buttons:
        d = dist(tb, (b["x"], b["y"]))
        if d < best_d:
            best, best_d = b, d
    if best_d > 0.09:            # too far — a group label, not a button label
        return None
    if inside(tb, best):        pos = "on"
    elif tb[1] < best["y"]:     pos = "above"
    elif tb[1] > best["y"]:     pos = "below"
    elif tb[0] < best["x"]:     pos = "left"
    else:                       pos = "right"
    return best, pos
```

**Symbols** (▶ ◀ ■ ⏸ ⏻ ⏏ ⏪ ⏩). OCR handles these badly. Crop each button, resize to
32×32 greyscale, and classify with a small CNN over ~20 classes. Bootstrap the
training set from catalog crops whose neighbouring text disambiguates them
(PLAY/STOP/REW labels next to icon buttons) and from a few hundred hand-labelled
examples. This is a couple of hours of work and materially outperforms OCR on the
transport-control cluster.

For the offline build, run **two or three OCR engines and take consensus** — keep a
label only when engines agree, or when a single engine reports confidence >0.9.

### 3.8 Brand and model code extraction

**Brand.** Ordered strategy:

1. Fuzzy-match every OCR'd token against your known-brand list
   (`rapidfuzz.process.extractOne`, threshold ~85). This is primary.
2. If nothing matches, treat isolated text ≥3× the median text height as a candidate
   brand and store it as unverified.
3. Maintain a template bank of brand wordmarks as images and match visually —
   stylised logotypes frequently defeat OCR.

**Model code.** Regex plus filters:

```python
MODEL_RE = re.compile(r'\b[A-Z]{1,5}[- ]?\d{2,5}[A-Z]{0,3}\b')

BUTTON_VOCAB = {
    "HDMI","HDMI1","HDMI2","HDMI3","VGA","VGA1","VGA2","AV","AV1","AV2",
    "USB","SCART","YPBPR","DVI","3D","PIP","SD","HD","CH","VOL","TV","DVD",
    "VCR","SAT","AUX","P/S","CLK","OSD","16:9","4:3","MPEG","DTV","ATV",
}

def find_model_code(text_regions, buttons):
    out = []
    for tr in text_regions:
        for m in MODEL_RE.finditer(tr["text"].upper()):
            tok = m.group()
            if tok.replace("-", "").replace(" ", "") in BUTTON_VOCAB:
                continue                      # VGA1, HDMI2, ...
            if len(tok) < 5:
                continue                      # too short to be distinctive
            if not re.search(r'\d', tok) or not re.search(r'[A-Z]', tok):
                continue                      # must mix letters and digits
            if button_within(tr, buttons, radius=0.05):
                continue                      # sits inside the keypad → a label
            score = 1.0
            if tr["y"] > 0.75:  score += 0.5  # codes live near the bottom
            if is_isolated(tr, text_regions): score += 0.5
            out.append((tok, score))
    return max(out, key=lambda t: t[1])[0] if out else None
```

The `BUTTON_VOCAB` blocklist is not optional. Without it a projector remote labelled
VGA1 / HDMI2 will be confidently misread as having model code "VGA1".

### 3.9 Fingerprint assembly and storage

```bash
python scripts/build_catalog.py \
    --src /var/lib/rcu/catalog_raw \
    --dst /var/lib/rcu/catalog_norm \
    --workers 8 \
    --ensemble \
    --resume
```

Requirements for this script:

- Idempotent and resumable — it will crash partway through 50k images at least once
- Writes a per-image JSON sidecar plus a row in `rcu_fingerprints`
- Emits an `extract_quality` score: weighted mix of button count plausibility
  (8–60 is normal), label recall, and body-detection confidence
- Writes an annotated debug overlay for every image below the quality threshold

At 8 workers and ~1.5 s per image, 50k images take roughly 3 hours. Perfectly
acceptable for a one-off.

### 3.10 Review pass

Sort by `extract_quality` ascending and review the worst 5%. Build a minimal review
page in Laravel (see 6.5) showing the original next to the annotated overlay, with
buttons to accept, re-run with alternate settings, or exclude.

Do not skip this. A few hundred badly extracted fingerprints will pollute the IDF
statistics and degrade matching for everything else.

### 3.11 De-duplication clustering

Many records will be the identical physical remote under different brand names.

```bash
python scripts/cluster_duplicates.py --threshold 0.93
```

Compare fingerprints pairwise within aspect-ratio and button-count bands (this keeps
it tractable — never do a full 50k × 50k comparison), cluster with single-linkage
above the threshold, and write `rcu_clusters`.

At match time, return the **cluster** and let the user disambiguate by brand. Asking
the matcher to separate physically identical objects is asking it to fail.

---

## 4. Matching Engine

### 4.1 Token generation

Two token families, generated from every fingerprint at build time and at query time
by the same code path.

**Family A — grid tokens (primary, simple).** Valid because rectification already
put everything in canonical coordinates.

```python
def grid_tokens(fp, gx=12, gy=32):
    toks = []
    for b in fp["buttons"]:
        cx, cy = int(b["x"] * gx), int(b["y"] * gy)
        size = 0 if b["w"] < 0.12 else (1 if b["w"] < 0.25 else 2)
        toks.append(hash64(("G", cx, cy, b["color"], size)))
        if b["label"]:
            toks.append(hash64(("L", cx, cy, norm_label(b["label"]))))
    return toks
```

**Family B — triplet invariants (fallback, robust).** Used when body detection is
uncertain, e.g. a hand covering an edge. These survive arbitrary affine distortion
because they encode only ratios and angles.

```python
def triplet_tokens(fp, k=5):
    pts = np.array([[b["x"], b["y"]] for b in fp["buttons"]])
    cols = [b["color"] for b in fp["buttons"]]
    toks = []
    tree = cKDTree(pts)
    for i, p in enumerate(pts):
        _, idx = tree.query(p, k=min(k + 1, len(pts)))
        for a, b in itertools.combinations(idx[1:], 2):
            d1, d2 = norm(pts[a] - p), norm(pts[b] - p)
            if d1 == 0 or d2 == 0:
                continue
            ratio = round(min(d1, d2) / max(d1, d2), 1)      # scale-invariant
            ang = round(angle(pts[a] - p, pts[b] - p) / 15)  # 15° buckets
            toks.append(hash64(("T", ratio, ang,
                                cols[i], *sorted([cols[a], cols[b]]))))
    return toks
```

Roughly 180–450 tokens per remote. At 50k models that is 9–22 M postings — fine in
memory as sorted numpy arrays.

### 4.2 Index build

```bash
python scripts/build_index.py --out /var/lib/rcu/index/tokens.npz
```

Store as CSR-style postings, not a Python dict (a dict of 20 M entries costs several
GB; numpy arrays cost a few hundred MB):

```
tokens.npz:
  token_ids   int64[N_unique]    sorted, binary-searchable
  offsets     int64[N_unique+1]
  postings    int32[N_total]     fingerprint ids
  idf         float32[N_unique]  log(N_docs / df)
```

Rebuild after any catalog change. It takes seconds.

### 4.3 Tier 1 — coarse retrieval

```python
def retrieve(query_tokens, index, top_n=100):
    scores = np.zeros(index.n_docs, dtype=np.float32)
    for t in set(query_tokens):
        pos = np.searchsorted(index.token_ids, t)
        if pos >= len(index.token_ids) or index.token_ids[pos] != t:
            continue
        w = index.idf[pos]
        if w < MIN_IDF:            # ignore near-universal features
            continue
        lo, hi = index.offsets[pos], index.offsets[pos + 1]
        if hi - lo > MAX_DF:       # skip pathologically common tokens
            continue
        np.add.at(scores, index.postings[lo:hi], w)
    scores /= np.sqrt(index.doc_norms)      # length normalisation
    return np.argpartition(-scores, top_n)[:top_n]
```

**The IDF weighting is the heart of the system.** A 3×4 grey keypad appears in most
of the catalog and contributes almost nothing. An orange pentagon appearing in
eleven records contributes enormously. You get this behaviour for free from the
statistics — no hand-tuned rules about which features are "important".

`MIN_IDF` and `MAX_DF` exist purely for speed; tune them so tier 1 stays under 10 ms.

### 4.4 Tier 2 — geometric verification

For each of the top 100 candidates, estimate a transform from putative button
correspondences and count inliers:

```python
def verify(q_fp, c_fp):
    src, dst = [], []
    for qb in q_fp["buttons"]:
        best = nearest_compatible(qb, c_fp["buttons"])   # same colour, similar size
        if best is not None:
            src.append([qb["x"], qb["y"]])
            dst.append([best["x"], best["y"]])
    if len(src) < 4:
        return 0.0, 0
    M, mask = cv2.findHomography(np.float32(src), np.float32(dst),
                                 cv2.RANSAC, 0.04)
    if M is None:
        return 0.0, 0
    inliers = int(mask.sum())
    coverage = inliers / max(len(q_fp["buttons"]), len(c_fp["buttons"]))
    label_bonus = label_agreement(q_fp, c_fp, mask)   # 0..0.3
    return coverage + label_bonus, inliers
```

Rules that matter:

- **Never require label agreement** — labels are a bonus term only. On real phone
  photos of black remotes, OCR recall will be low, and hard label requirements will
  destroy your recall along with it.
- Penalise unmatched candidate buttons. A candidate with 40 buttons matching a query
  with 12 should score poorly even if all 12 match perfectly.
- Reject transforms with implausible scale or shear — a valid homography here is
  close to affine.

### 4.5 Model-code fast path

Run this **before** tier 1. If `find_model_code` returns a token that hits
`rcu_fingerprints.model_text` or your catalog's model field:

- Exact match, single hit → return immediately, confidence `high`
- Exact match, several hits → return those as the candidate set, skip tier 1
- Fuzzy match (Levenshtein ≤ 2) → boost those candidates by +0.4 in fusion

This path is near-100% precise and costs one indexed lookup. It is also why the
"photograph the back" prompt (7.4) is so valuable.

### 4.6 Score fusion and confidence

```
final = 0.55 * geometric_score
      + 0.25 * tier1_idf_score_normalised
      + 0.15 * brand_agreement          # 1.0 match, 0.0 conflict, 0.5 unknown
      + 0.05 * aspect_ratio_agreement
      + model_code_bonus                # 0 or 0.4
```

Confidence bands — calibrate these on your own test set (section 8), do not accept
these numbers as given:

| Band | Condition | UI behaviour |
|---|---|---|
| high | `final > 0.75` and margin over 2nd > 0.15 | Show the answer, offer "not this one" |
| medium | `final > 0.50` | Show top 3 as a picker |
| low | `final > 0.30` | Show top 5 + "photograph the back" prompt |
| none | otherwise | Ask for a re-shoot with guidance |

**A brand conflict should never be fatal.** Weight it, do not filter on it — OCR
misreads and rebadged remotes both exist.

### 4.7 Optional embedding channel

Cheap insurance, worth adding once the fingerprint path works. Embed every
normalised crop with CLIP or DINOv2 (~5 ms per query at 50k with brute-force
cosine), and add it as a fifth fusion term at low weight. Its value is catching
cases where fingerprint extraction failed outright — a query with 3 detected buttons
has no useful fingerprint, but still has a usable embedding.

---

## 5. Python Service API

### 5.1 Endpoints

```
GET  /health                → {"status":"ok","index_docs":48213,"version":"1.4.0"}
POST /identify              → multipart image + options → candidates
POST /fingerprint           → multipart image → raw fingerprint (debug/admin)
POST /reindex               → rebuild in-memory index from DB
GET  /debug/{request_id}    → annotated overlay PNG
```

### 5.2 Response contract

```json
{
  "request_id": "0f6c...",
  "confidence": "medium",
  "latency_ms": 340,
  "extracted": {
    "brand": "AKAI",
    "model_code": null,
    "button_count": 34,
    "quality": 0.71
  },
  "candidates": [
    { "model_id": 18422, "cluster_id": 3301, "brand": "AKAI",
      "model": "RC-51A", "score": 0.68, "inliers": 24,
      "image_url": "/catalog/18422.jpg" }
  ],
  "hint": "photograph_back"
}
```

### 5.3 Startup

Load the index once at startup into module-level state; never per request. Add a
readiness probe so Laravel can fail fast with a clean message while the service is
still warming up.

### 5.4 Internal authentication

A shared secret in an `X-Internal-Token` header, checked by FastAPI middleware.
Combined with binding to `127.0.0.1`, this is sufficient.

---

## 6. Laravel Backend

### 6.1 Setup

```bash
composer create-project laravel/laravel backend-laravel
cd backend-laravel
php artisan install:api
```

`.env` additions:

```
RCU_SERVICE_URL=http://127.0.0.1:8600
RCU_SERVICE_TOKEN=<random-64-hex>
RCU_UPLOAD_DISK=rcu
RCU_MAX_UPLOAD_KB=10240
```

### 6.2 Upload endpoint

```php
// routes/api.php
Route::post('/identify', [IdentifyController::class, 'store'])
    ->middleware('throttle:20,1');
```

```php
public function store(Request $request, RcuService $service)
{
    $validated = $request->validate([
        'photo' => ['required', 'image', 'mimes:jpeg,png,webp',
                    'max:' . config('rcu.max_upload_kb')],
    ]);

    $path = $request->file('photo')->store('uploads', 'rcu');
    $query = RcuQuery::create([
        'request_id'  => (string) Str::uuid(),
        'upload_path' => $path,
    ]);

    try {
        $result = $service->identify($path, $query->request_id);
    } catch (ServiceUnavailableException $e) {
        return response()->json(['error' => 'recognition_unavailable'], 503);
    }

    $query->update([
        'candidates' => $result['candidates'],
        'top_score'  => $result['candidates'][0]['score'] ?? null,
        'confidence' => $result['confidence'],
        'latency_ms' => $result['latency_ms'],
    ]);

    return new IdentifyResource($query, $result);
}
```

### 6.3 Service client

```php
class RcuService
{
    public function identify(string $path, string $requestId): array
    {
        return Http::withHeaders([
                'X-Internal-Token' => config('rcu.service_token'),
            ])
            ->timeout(20)
            ->retry(2, 200)
            ->attach('image', Storage::disk('rcu')->get($path), 'photo.jpg')
            ->post(config('rcu.service_url') . '/identify', [
                'request_id' => $requestId,
            ])
            ->throw()
            ->json();
    }
}
```

Keep it synchronous for now — sub-second latency does not justify a queue. Move to a
job only if you add multi-frame capture or heavier models.

### 6.4 Feedback capture

```php
Route::post('/identify/{requestId}/choose', function ($requestId, Request $r) {
    $q = RcuQuery::where('request_id', $requestId)->firstOrFail();
    $q->update(['chosen_model_id' => $r->integer('model_id')]);
    return response()->noContent();
});
```

Every tap in the picker is a labelled training pair. This dataset is what powers
phase 9, and it costs nothing to collect — make sure it is wired in from day one,
including a "none of these" option, which is the most informative signal of all.

### 6.5 Admin visualiser

A single Blade page, and one of the highest-value things you will build:

- Upload a photo, or pick a past query
- Show side by side: original, rectified crop, annotated overlay (button boxes
  coloured by detected bucket, labels drawn, model-code region highlighted)
- Show the extracted fingerprint JSON
- Show the top 10 candidates with score breakdown per fusion term and inlier count
- Show the matched button correspondences drawn as lines between query and candidate

When a match is wrong, this tells you in seconds whether the cause was detection,
colour, OCR, or scoring. Without it you are guessing.

---

## 7. Client

### 7.1 Browser PWA capture

Start here — no store approval, instant iteration, works on both platforms.

```html
<input type="file" accept="image/*" capture="environment">
```

For the guided experience use `getUserMedia` with a live preview:

```js
const stream = await navigator.mediaDevices.getUserMedia({
  video: { facingMode: { ideal: "environment" },
           width: { ideal: 1920 }, height: { ideal: 1080 } }
});
```

Note that `getUserMedia` requires HTTPS — provision a certificate before you start
testing on a phone.

### 7.2 Framing guide

Overlay a portrait rectangle at roughly 60% of frame height with the instruction
"fill the frame, keep the remote flat and face-up". Consistent framing improves
detection more than any algorithm change, because it constrains scale and pose.

### 7.3 Pre-upload quality checks

Run client-side; re-prompt rather than uploading a bad photo:

```js
function blurScore(imageData) {          // variance of Laplacian
  // reject below an empirically set threshold
}
function glareRatio(imageData) {         // fraction of near-255 pixels
  // >3% in a connected blob → warn about reflections
}
```

Also downscale to 1600 px on the long edge before upload — full-resolution images
buy nothing and cost seconds on mobile data.

### 7.4 Result UI

- **high** → the model, its catalog photo, "Yes" / "Not this one"
- **medium / low** → a grid of 3–5 candidate photos, tap to choose. With your
  catalog imagery this is a genuinely pleasant interaction, not a failure state.
- **low / none** → "Flip the remote over and photograph the back label". This
  triggers the model-code fast path and resolves the majority of hard cases.

Design around top-5, not top-1. Many remotes share a mould and differ only in
silkscreen; a picker is the honest interface for that reality.

### 7.5 Android wrapper (later)

Once the PWA is stable, wrap it in a WebView or a thin native shell for camera
control and offline queuing. No recognition logic moves to the client — that stays
server-side as planned.

---

## 8. Evaluation and Tuning

### 8.1 Build a real test set

**Do this before optimising anything.** Pick 300 models spanning brands, sizes and
eras. Photograph 50 of them with an actual phone, in actual living rooms: mixed
lighting, some glare, some at an angle, some held in hand.

```
tests/groundtruth.csv:  photo_path, true_model_id, notes
```

50 real photos beats 5,000 synthetic ones for telling you where you actually stand.

### 8.2 Metrics

```bash
python scripts/evaluate.py --gt tests/groundtruth.csv --out report.html
```

Track: top-1, top-5, mean reciprocal rank, and — critically — the intermediate
diagnostics: body detection rate, mean button recall vs catalog, OCR label recall,
model-code detection rate and precision.

Aggregate accuracy tells you *whether* it works. The intermediates tell you *what to
fix*.

### 8.3 Threshold tuning

Sweep confidence thresholds against the test set and plot precision against the
fraction of queries auto-answered. Choose the operating point where high-confidence
precision exceeds ~95% — a wrong confident answer costs far more user trust than an
honest picker.

### 8.4 Error analysis loop

For every failure, open the admin visualiser and classify the cause into: body
detection, orientation, button detection, colour, OCR, tokenisation, or scoring.
Tally by category weekly and fix the largest bucket. Resist the urge to tune fusion
weights — that is almost never the real problem.

### 8.5 Realistic expectations

| Stage | Top-1 | Top-5 |
|---|---|---|
| Phase 1, classical CV | 35–50% | 65–80% |
| After trained button detector | 55–70% | 85–92% |
| After symbol classifier + feedback data | 60–75% | 88–94% |

Anyone quoting better than this on 50k scraped classes has not measured it.

---

## 9. Accuracy Upgrades (After the Baseline Works)

### 9.1 Trained button detector

The largest single improvement. Classical thresholding fails on matte black buttons
on matte black bodies, which is a large share of your catalog.

1. Generate pseudo-labels: run the classical detector over 3,000 catalog images
2. Hand-correct ~400 of them (a few hours in Label Studio or CVAT)
3. Train YOLOv8-nano at 640 px on the button class

```bash
yolo detect train data=buttons.yaml model=yolov8n.pt \
     epochs=100 imgsz=640 batch=16
yolo export model=runs/detect/train/weights/best.pt format=onnx
```

4. Augment hard for the phone-photo domain: perspective ±25°, brightness, synthetic
   glare blobs, motion blur, JPEG artefacts, random backgrounds
5. Rebuild all catalog fingerprints with the new detector and rebuild the index

### 9.2 Symbol classifier

32×32 greyscale CNN over ~20 icon classes (see 3.7). Small, fast, and it recovers
the transport-control cluster that OCR consistently misses.

### 9.3 Embedding channel

Add the CLIP/DINOv2 term from 4.7 as a fusion component and as a safety net when
`extract_quality` is low.

### 9.4 Feedback-driven retraining

Once you have a few thousand user photos with confirmed model IDs:

- Add them to the detector training set — real photos beat synthetic augmentation
- Mine hard negatives: pairs the system confused, used to tune verification
- Fine-tune the embedding with metric learning, using catalog-photo / user-photo
  pairs as positives

---

## 10. Deployment and Operations

### 10.1 Monitoring

Log per request: latency, confidence band, extraction quality, button count, whether
the user accepted the top answer. Alert on: p95 latency > 2 s, high-confidence rate
dropping below baseline, service restarts.

The acceptance rate on the top candidate is your single best health metric — it
detects regressions no synthetic test will.

### 10.2 Index updates

New catalog records: run `build_catalog.py --only <ids>`, then `POST /reindex`. No
downtime; the index swap is atomic if you build into a new object and rebind.

### 10.3 Backups

Back up `rcu_fingerprints` and `rcu_queries` with your normal MySQL dumps. The index
`.npz` is derived and need not be backed up. User uploads should be retained — they
are your most valuable training asset.

### 10.4 Privacy

Photos may capture living rooms and people. State retention terms clearly, strip
EXIF (including GPS) on ingest, and set a retention window for uploads that were
never converted into training data.

---

## 11. Milestones

| # | Milestone | Deliverable | Est. |
|---|---|---|---|
| M0 | Catalog audit | `audit.csv`, decisions on watermarks and multi-remote counts | 2–3 d |
| M1 | Pipeline on 300 models | Fingerprints, admin visualiser, no matching yet | 1–2 w |
| M2 | Matching engine | Tier 1 + tier 2, evaluated on 300-model subset | 1 w |
| M3 | Real test set | 50 phone photos, baseline numbers, first error analysis | 3–5 d |
| M4 | Full catalog build | All 10–50k fingerprinted, reviewed, deduplicated | 1 w |
| M5 | End-to-end MVP | Laravel API, PWA client, picker UI, feedback capture | 1–2 w |
| M6 | Trained detector | YOLO button model, catalog rebuild, measured improvement | 1–2 w |
| M7 | Production | Monitoring, backups, retention policy | 3–5 d |

**Do not skip M3.** Deferring the real test set until after the full catalog build is
the most likely way to waste a month optimising the wrong component.

---

## 12. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| OCR recall on phone photos far below catalog | High | Labels are a bonus term, never a requirement; symbol classifier; multi-frame capture |
| Button detection fails on black-on-black | High | Trained detector (9.1); this is the known weak point of phase 1 |
| Orientation ambiguity produces mirrored fingerprints | High | Four-signal disambiguation; index both orientations when uncertain |
| Physically identical remotes across brands | Medium | Cluster and disambiguate by brand in the UI, not in the matcher |
| Scraped catalog has wrong model associations | Medium | Caption-strip OCR cross-check; review queue |
| False model codes from button text (VGA1, HDMI2) | Medium | Vocabulary blocklist, position priors, isolation test |
| PaddleOCR dependency fragility | Low | Engine kept behind an interface; EasyOCR and docTR as drop-ins |

---

## Appendix A — Quick Start

```bash
# 1. environment
cd /opt/rcu/service-python && source .venv/bin/activate

# 2. audit what you have
python scripts/audit_catalog.py --dir /var/lib/rcu/catalog_raw --out audit.csv

# 3. build fingerprints for a pilot subset
python scripts/build_catalog.py --src /var/lib/rcu/catalog_raw \
    --limit 300 --workers 8 --ensemble

# 4. build the index
python scripts/build_index.py --out /var/lib/rcu/index/tokens.npz

# 5. run the service
uvicorn app.main:app --host 127.0.0.1 --port 8600 --reload

# 6. test one photo
curl -F "image=@test.jpg" -H "X-Internal-Token: $TOKEN" \
     http://127.0.0.1:8600/identify | jq
```

## Appendix B — Tuning Parameter Reference

| Parameter | Default | Where | Effect |
|---|---|---|---|
| `GRID_X × GRID_Y` | 12 × 32 | tokens.py | Finer = more discriminative, less distortion-tolerant |
| `MIN_IDF` | 1.5 | index.py | Higher = faster tier 1, may drop weak-but-valid evidence |
| `MAX_DF` | 5000 | index.py | Skips near-universal tokens |
| `RANSAC_THRESH` | 0.04 | verify.py | In normalised units; higher tolerates more distortion |
| `LABEL_ASSIGN_DIST` | 0.09 | ocr.py | Max distance from text to its button |
| `BUTTON_AREA_MIN` | 0.0004 | detect.py | Fraction of body area; raise to suppress noise |
| `HIGH_CONF` | 0.75 | fuse.py | Auto-answer threshold — calibrate on the test set |
| `DEDUP_THRESH` | 0.93 | cluster.py | Similarity above which two records are one mould |
