# Session 02 — OCR, text/button arbitration, branding, audit

Corresponds to plan sections 3.7–3.8 and the session-2 list at the end of
`SESSION-01.md`. Retrieval and geometric verification remain session 3.

## Run it

```bash
pip install -r requirements.txt          # or ./install.sh for the OCR engine
python scripts/extract_one.py --dir ../photos --out ../work
python scripts/audit_catalog.py --fp ../work/fp --csv ../work/audit.csv \
    --debug ../work/debug --review-dir ../work/review --top 50
```

`--no-ocr` skips text entirely while tuning geometry. `--ocr-consensus` runs
every installed engine and keeps only agreed regions, for the offline build.

## What landed

1. `pipeline/ocr.py` — engine behind a swappable adapter interface. RapidOCR
   (PP-OCR under onnxruntime) is the default; Paddle, EasyOCR, docTR and
   Tesseract are registered alternatives, and `get_engine()` takes the first
   that imports. Reads in overlapping horizontal bands, because a remote
   upscaled to 1100px wide is ~4500px tall and detecting over the whole thing
   at once OOM-killed the first run.
2. `pipeline/labels.py` — text/button arbitration and label assignment. The
   over-detection from session 1 is fixed: the AKAI black remote's 57
   "buttons" were mostly lettering.
3. `pipeline/branding.py` — brand matching against `data/brands.txt` and
   model-code extraction behind the `BUTTON_VOCAB` blocklist.
4. `scripts/audit_catalog.py` — ranks every extracted fingerprint for review
   and copies the worst overlays into one folder. It does no CV, so auditing
   the whole catalog costs seconds and can be re-run after any config change.

## Results on the 18 sample images

21 bodies from 18 images. All 18 now yield at least one body; three yielded
none at the start of the session.

| | |
|---|---|
| buttons | min 2, median 16, max 54 |
| quality | min 0.30, median 0.84, mean 0.80 |
| brand | 11/21, of which 10 verified against the list |
| model code | 7/21 |

Correct model codes now include RM-D1110, RM-PJ20R, RM-PJ20, RM-L859,
RM-530F, RC-371M and MR-18B.

## Bugs found and fixed this session

Every one was found by rendering something — an overlay, a threshold mask, or
the audit's review queue — and none by reasoning about the code.

1. **Buttons detected as rings and thrown away.** The HONEYWELL HE5500
   returned *zero* buttons. Rendering the threshold masks showed all five
   present as clean outlines: a broken ring is traced as a stroke, so
   `contourArea` measured the stroke and `min_fill` discarded it. Rescued by
   measuring the convex hull when a contour is round, hollow and large.
   0 → 6 detections (5 buttons plus the IR window). Guards keep lettering out;
   the text-heavy crops moved by ≤3 buttons.

2. **`min_pass_yield` is an absolute count.** A remote with genuinely few
   buttons has *every* pass fall below it and returns nothing at all. Same
   class of bug as session 1's absolute block sizes. Now falls back to the
   best pass rather than returning nothing.

3. **Tightly-cropped images returned no body at all.** Both segmentation
   strategies estimate the background from the image border, so when the
   product is cropped flush the estimate lands on the *remote* and the mask
   inverts — the ROLSEN RSF-3106RT segmented to just its white wordmark.
   Added a full-frame fallback for when nothing else is found. ROLSEN now
   extracts all 6 of its buttons with `brand=Rolsen`.

4. **A tight crop was rejected as "the image frame".** The Prestigio KF-7777A
   fills 82% of its frame and was discarded by `frame_area_frac`. The frame
   rule now also requires the blob to be rectangle-solid *or* insufficiently
   elongated — a real remote has rounded corners and fills ~0.83.

5. **Short brand names fuzzy-matched button legends.** SPACE scored 88.9
   against "Pace" and IRIS 88.9 against "Irbis", both over the threshold of
   85. Tokens under `fuzzy_min_len` must now match exactly. Every brand this
   pipeline reads correctly matches exactly anyway, so recall was unaffected
   and a whole class of false brands disappeared.

6. **`"+"` became a brand.** The unverified-wordmark fallback had no length
   guard, so the largest text region on a crop — a single glyph — became the
   brand. Now needs `min_wordmark_alpha` letters.

7. **Model codes truncated.** `MODEL_RE` allowed one letter group before the
   digits, so RM-D1110 was stored as D1110 and RM-PJ20R as PJ20R — different
   remotes as far as the index is concerned. Two groups are now allowed, and
   the digit run extends to six for URC-177500.

8. **The correct model code was excluded and OCR garbage kept.** MR-18B was
   dropped because it sits within `button_radius` of a keycap, leaving a
   misread "LIVE ZOOM" → LIVE20OM to win by default. Proximity to a button is
   evidence, not proof: it is now a score penalty, and a separator in the
   token is a bonus.

9. **Two remotes fused into one fingerprint.** Introduced while fixing (4) and
   caught by looking at the overlay: relaxing the frame rule on fill alone let
   a near-square blob holding two remotes *and* their packaging through as a
   single body (RM-L859-1, 38 buttons). Elongation separates a genuine tight
   crop (4.07) from a montage (1.80).

## A wrong turn worth recording

Both `find_brand` and `find_model_code` looked like they should ignore regions
that `assign_labels` had already tied to a button — a wordmark is not a button
legend. This is wrong, and it is wrong in a way that reads as obviously right.
Label assignment uses a 0.09 radius, so on a button-dense remote nearly every
region becomes a "label", including the SONY wordmark and the RM-PJ20R code,
while genuine body text stays a "caption". Filtering on `role` dropped the
correct model code and kept the OCR garbage. There is a note in `config.py` so
nobody tries it again.

The general lesson repeats session 1's: the diagnosis came from tracing the
real pipeline. An earlier trace against the *saved* `work/norm` crop showed no
LIVE20OM at all and pointed the wrong way, because the saved crop is a
re-encode and not what the pipeline actually saw.

## Known-bad, for session 3

- **Orientation is the dominant systemic issue: 11 of 21 unresolved.** The
  audit surfaces it as the single largest flag. Taper is weak on
  parallel-sided remotes and the text baseline signal is not yet carrying its
  weight. Ambiguous crops must be indexed both ways up until this improves.
- **Adjacent remotes bridged by packaging still merge.** RM-L859-1 has two
  remotes and a retail label; the vertical-projection split probe cannot find
  a valley through the label, and the body detector settles on the label strip
  (2.0% of the image). The audit now ranks it second-worst, but the extraction
  is still wrong.
- **Stylised wordmarks defeat OCR.** AIWA reads as "EMIE". Plan 3.8's third
  strategy — visual wordmark templates — is unimplemented and is the main
  remaining gain in brand recall.
- **Colour bucketing is still untuned against real phone photos.** Unchanged
  from session 1: every `ColorConfig` threshold was set against studio images.
- **Memory.** This box has ~650 MB free and a whole-directory run gets
  OOM-killed; extracting one image per process gets through it. OCR runs twice
  per body (both orientations), which is the peak.

## Next session

1. Retrieval: the inverted token index, IDF weighting, `.npz` persistence
2. RANSAC geometric verification over the top candidates
3. FastAPI service on 127.0.0.1:8600
4. Orientation, again — it is now the largest single source of bad records
