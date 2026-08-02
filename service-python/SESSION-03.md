# Session 03 — retrieval verified, query path, and the orientation root cause

Corresponds to plan sections 4–5 and the session-3 list at the end of
`SESSION-02.md`. All four items are addressed; item 4 (orientation) turned out
to have a single upstream cause.

## Run it

```bash
# rebuild the catalog, one image per process (a --dir run is OOM-killed)
for f in ../photos/*.jpg; do
    python scripts/extract_one.py "$f" --out ../work
done
python scripts/build_index.py --fp ../work/fp --out ../work/index/tokens.npz
python scripts/match_eval.py  --fp ../work/fp --index ../work/index/tokens.npz
python scripts/audit_catalog.py --fp ../work/fp --csv ../work/audit.csv \
    --debug ../work/debug --review-dir ../work/review --top 50

# service
RCU_INTERNAL_TOKEN=... RCU_INDEX_PATH=../work/index/tokens.npz \
    RCU_FP_DIR=../work/fp python -m app.main
```

`--ocr-width N` is new: it lowers the OCR upscale for one run. The upscale is
the peak memory user, and this box can no longer get a 4000px image through at
the default. See "Memory" below.

## What landed

1. **Retrieval, RANSAC and the service were already written and had never been
   run.** They work. `match_eval.py` gives recall@1 8/8 on the records that
   have a true match present. The FastAPI service answers `/health`,
   `/identify`, `/fingerprint`, `/reindex` and `/debug/{id}`.
2. **Query-path OCR economy** (`extract_remotes(fast_ocr=True)`,
   `CFG.ocr.query_min_width`, `CFG.ocr.query_both_orientations`). One OCR pass
   at a lower upscale instead of two at full: ~18 s -> ~6 s of the query.
3. **`angle_cls` — the orientation root cause.** See below. Unresolved crops
   went 11/21 -> 2/21.
4. **`index_both_orientations` is now False.** 42 docs -> 23, recall
   unchanged.

## The orientation root cause

Session 2 recorded orientation as "the dominant systemic issue: 11 of 21
unresolved", and attributed it to weak taper and a text signal "not yet
carrying its weight". The second half was right for the wrong reason. The text
signal was not weak. It was exactly zero, and it was zero by construction.

PP-OCR runs detection -> **angle classification** -> recognition. The middle
stage rotates each detected text line upright before recognition, and both
RapidOCR and PaddleOCR enable it by default. So a crop and the same crop
rotated 180 degrees recognise *identically*, and `orientation_from_text` was
comparing a number against itself. Measured on the Sony RM-PJ20, one band:

| | upright | flipped |
|---|---|---|
| `use_cls=True` (was) | 6 vocab hits | 6 |
| `use_cls=False` (now) | 6 vocab hits | 0 |

The tell was in the data before the cause was: three separate crops scored an
*exact* tie (Sony 8/8, ONKYO 8/8, AIWA 10/10), and returned the identical word
list both ways up — `3D, GAME, INPUT, LIGHT, MENU, MODE, PATTERN, PHOTO`. One
exact tie is luck; three is a mechanism.

`CFG.ocr.angle_cls` is now False and both adapters honour it. Across the
sample the text margin went from 0.00–0.23 to 0.36–0.93, and every crop that
previously fell back to geometry now resolves on text.

Two consequences worth stating plainly:

- **No scoring change was needed.** The first hypothesis was that
  `_text_score`'s raw-confidence term was drowning the vocabulary term — and
  it measurably was, diluting URC-177500's real 0.333 vocab margin to 0.032.
  But that dilution is a *symptom*: with the classifier off, flipped text
  reads as low-confidence garbage (36.71 vs 2.32) and the confidence term now
  reinforces the vocab signal instead of cancelling it. Tuning the weights
  would have papered over the cause, exactly as CLAUDE.md warns.
- **Seven records were being stored upside down with confidence 1.00.** The
  fix changed the flip verdict on 8 records, 7 of them genuine remotes. All 7
  were verified by eye in `work/norm`: `aiwa`, `HUAYU RM-530F` and `SONY
  PROJECTOR` wordmarks now sit at the bottom, upright. The old confident
  answers were confidently wrong — the precise failure
  `resolve_orientation`'s own docstring warns about.

## Results on the 21 records

| | session 2 | session 3 |
|---|---|---|
| orientation unresolved | 11/21 | **2/21** |
| index docs | 42 | **23** |
| brand | 11/21 | 11/21 |
| model code | 7/21 | 7/21 |
| recall@1 (of 8 answerable) | 8/8 | 8/8 |
| true/false separation | +0.155 | +0.077 |
| query latency (Sony, HTTP) | ~15.7 s | ~7.5 s |

The two still unresolved are `HONEYWELL_HE5500_0` (six buttons, no text but
the wordmark) and `Huayu_RM-530F_JVC_TV_7_0` (the 2-button bad extraction).

Orientation is no longer the largest audit flag. That is now "no brand and no
model code", at 7 records.

## Bugs found by running the thing

1. **The query path returned zero buttons on the HONEYWELL HE5500.** The
   service used `ensemble=False`, which uses a single adaptive-threshold block
   size (`block_fracs[1]`, 0.075). The HE5500's grey-on-grey buttons yield
   nothing at that one size, and the empty-pass fallback had nothing to fall
   back to — so `/identify` answered `confidence: none, hint: reshoot` on a
   remote the catalog holds correctly. Measurement showed the ensemble is
   nearly free (most images 0.0–0.1 s, worst case 1.6 s on a 4000px image)
   because the cost is thresholding, not the OCR that dominates. The query
   path now uses the ensemble. HE5500 goes 0 -> 6 buttons and identifies at
   0.998 / high.
2. **`RM-PJ20_big_light_0` was the counter-example in a config comment.** The
   comment justifying `index_both_orientations = True` cited that record as
   carrying confidence 1.00 while stored upside down. It was one of the seven
   the classifier fix corrected, which is what made the flag safe to turn off.

## Known-bad, for session 4

- **Text/button arbitration deletes buttons that have legends printed on
  them.** Exposed, not caused, by the OCR fix: once flipped crops stopped
  reading fluently, upright crops started reading *better*, and
  `suppress_text_detections` removed the keycaps underneath. `MR-18B_0_1` went
  21 -> 4 buttons and lost geometric verification entirely (6 inliers -> 0),
  which is most of why separation fell to +0.077. **This is confined to
  packaging photos** — the two affected records are a remote photographed on a
  retail card and the RM-L859-1 label strip already marked BAD. The other 19
  records moved by 0 to -3 buttons. A legend *on* a keycap should attach to
  the button, not delete it.
- **Separation is +0.077 and the sample cannot calibrate it.** 21 records over
  18 photos, with IDF a statistic over 21 documents. The DVD_80 / YDX-107
  false pair sits at 0.433 because both are genuinely large keypads. Treat the
  confidence bands as unvalidated until a real catalog exists.
- **Query latency is 3.1–7.5 s against the plan's ~1 s budget** (p95 alert at
  2 s). This is a deliberate, accepted trade: geometry alone runs in ~0.6 s and
  still gets 7/7 top-1 on the sample, but two of seven drop from high to
  medium confidence, and the model-code fast path is what gives Sony its
  1.400. OCR stays on every query. Revisit if the query volume ever justifies
  it — the escalation design (geometry first, OCR only when not decisive) is
  written up in the session log and is the obvious next move.
- **Adjacent remotes bridged by packaging still merge.** Unchanged from
  session 2. `RM-L859-1_0` is now down to 1 button.
- **Stylised wordmarks still defeat OCR.** Unchanged. Plan 3.8's visual
  wordmark templates remain the main lever on brand recall, which did not move
  this session (11/21).
- **Colour bucketing still untuned against phone photos.** Unchanged since
  session 1.

## Memory

Worse than session 2 recorded. This box has ~540 MB free (two `mysqld` take
~580 MB between them), and **one image per process is no longer sufficient**:
five of eighteen images were OOM-killed individually at the default 1100px OCR
upscale. They were re-extracted with `--ocr-width 800`, so `DVD_80`,
`Prestigio_KF-7777A`, `RM-L859-1`, `RM-PJ20_big_light` and `Sony_RM-PJ20_big`
carry slightly lower label recall than the rest of the sample. Brand and model
code were unaffected (11/21 and 7/21, unchanged), because a wordmark and a
model code are among the largest text on a remote.

The pre-fix fingerprints are kept at `work/fp.bak-precls/` for comparison.

## Next session

1. Text/button arbitration: a legend on a keycap must attach, not delete
2. Brand recall — visual wordmark templates (plan 3.8), still 11/21
3. Laravel side: uploads, catalog DB, admin UI, calling 127.0.0.1:8600
4. Re-validate the confidence bands once the catalog is real; the 21-record
   sample cannot calibrate IDF or the bands
