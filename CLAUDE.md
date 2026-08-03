# RCU Identifier

Photograph a TV remote control, identify its model from a catalog of
10k–50k records (brand, model, photos, mostly scraped from the web).

Read `docs/rcu-identifier-implementation-plan.md` for the full design, and
`rcu-session-01/SESSION-01.md`, `service-python/SESSION-02.md`,
`service-python/SESSION-03.md`, `service-python/SESSION-04.md` and
`service-python/SESSION-05.md` for status and known-bad behaviour. Session 05
is the current one. Session 04 corrects two claims session 03 made; session 05
retires the memory constraint that shaped sessions 03 and 04, so treat any
`--ocr-width 800` advice in those two as historical.

## Approach

Structural fingerprinting, not classification. Each remote reduces to a
normalised point set of buttons (position, size, shape, colour, label) plus
text regions. Matching is two-tier: inverted-index retrieval weighted by IDF,
then RANSAC geometric verification on the top candidates.

Retrieval, not classification, because new remotes must be an INSERT and never
a retraining run.

## Commands

```bash
cd service-python
source .venv/bin/activate
pip install -r requirements.txt

# run extraction over images, writing crops, overlays and fingerprints
python scripts/extract_one.py --dir /var/lib/rcu/catalog_raw --out ../work

# single image, single-pass (faster while tuning)
python scripts/extract_one.py IMG.jpg --out ../work --no-ensemble

# skip OCR entirely while tuning geometry
python scripts/extract_one.py IMG.jpg --out ../work --no-ocr

# rank every extraction for review, worst overlays copied into work/review
python scripts/audit_catalog.py --fp ../work/fp --csv ../work/audit.csv \
    --debug ../work/debug --review-dir ../work/review --top 50

# build the token index, then evaluate retrieval leave-one-out
python scripts/build_index.py --fp ../work/fp --out ../work/index/tokens.npz
python scripts/match_eval.py  --fp ../work/fp --index ../work/index/tokens.npz

# measure the QUERY path: uploads every catalog photo to the running service.
# match_eval above never uploads anything and cannot see query-path bugs.
python scripts/query_drift.py --photos ../photos --fp ../work/fp

# assert the decode invariant (build and service agree; decoding is stable).
# Run over any new catalog drop; exits non-zero on violation.
python scripts/check_decode.py --dir ../photos

# the service (loopback only; Laravel calls it)
RCU_INTERNAL_TOKEN=... RCU_INDEX_PATH=../work/index/tokens.npz \
    RCU_FP_DIR=../work/fp python -m app.main
```

```bash
# Laravel: load work/fp into the catalog table, prune deleted records, and
# tell the running service to reload its index
cd backend-laravel
php artisan rcu:import-catalog --prune --reindex
php artisan test                        # 61; 5 need the service running

# On the legacy catalogue: decide what to extract BEFORE extracting it, and
# take metadata from the catalogue afterwards. files/ is a third non-remotes.
php artisan rcu:legacy-manifest --out=- > ../work/primary.txt
php artisan rcu:import-catalog --legacy --prune --reindex
```

Extract **one image per process**: OCR runs twice per body (both orientations)
on an upscaled crop, and a whole-directory run in one process grows without
bound.

Memory stopped being the binding constraint in session 5, when the box went to
3.9 GB. Measured with the service running (393 MB RSS): a 5.3 MP image at the
default 1100px OCR upscale peaks at 696 MB and completes, and all 18 sample
images rebuild with zero kills. **`--ocr-width 800` is no longer needed** —
sessions 3 and 4 required it at ~540 MB free, and it turned out to cost almost
nothing (356 vs 360 buttons, identical brand/model-code recall, identical
separation), so do not reintroduce it as a precaution.

The upscale is still the peak memory user and remains the first knob to reach
for on a smaller box.

The service plateaus at ~370–390 MB RSS (idle 95 MB, ~315 MB after one query —
a one-time OCR model allocation, not a leak). Sessions 3 and 4 had to stop it
before any rebuild; at 3.9 GB that is no longer necessary, and session 5 did a
full 18-image rebuild with it serving. It is still a per-instance cost worth
knowing when sizing a box.

**After rebuilding the catalog, resync both consumers:**
`build_index.py`, then `php artisan rcu:import-catalog --prune --reindex`. The
token index and the `rcu_fingerprints` table must come from the same
extraction run — when they drift, matching still "works" and returns
`record_id`s that resolve to no row, which reads as a database bug and is not.
The import command warns when the two counts disagree.

## Architecture

- `service-python/` — all computer vision. FastAPI on 127.0.0.1:8600, built and
  running as of session 3. The venv is Python 3.10.12, not the 3.11 this file
  claimed through session 2.
- `backend-laravel/` — Laravel. Auth, uploads, catalog DB, admin UI. Calls the
  Python service over internal HTTP on 127.0.0.1:8600. **Never put CV in PHP.**
  Wired end to end in session 5: `/api/identify`, feedback capture, the
  `rcu:import-catalog` command, and the admin visualiser at `/admin/rcu`
  (queries) and `/admin/rcu/catalog` (records, review queue, overlays).
- MySQL holds the catalog; the token index lives in memory in the Python
  service, persisted as `.npz`.

## Working rules

- **Every threshold lives in `app/config.py`.** Do not scatter constants into
  pipeline modules. Tuning is: change a value, re-run, look at the overlay.
- **Look at the overlay before changing code.** Every bug fixed in session 1
  was found by viewing `work/debug/*.jpg`, not by reasoning about the code.
  If you are about to reason about why detection failed, render it instead.
- **Never require OCR labels to match.** Label recall on real phone photos of
  black remotes is low. Labels are a bonus scoring term, only ever.
- **Geometry over text.** Button positions and colours survive bad lighting;
  printed text does not.
- **Orientation errors are silent and corrupting.** When the signal is weak,
  return `ambiguous` and index both orientations. Never guess.
- **Coarse colour names only** (red/orange/grey/…), never RGB triples. Coarse
  survives white-balance shifts between studio and phone.
- Catalog build has no time budget — prefer ensembles and slow accurate
  settings offline. Query path must stay under ~1s.

## Hard-won gotchas

These caused real bugs. Do not reintroduce them.

- Threshold block sizes must be **fractions of crop width**, never absolute
  pixels. A block that works at 400px finds nothing at 900px.
- Detect **both polarities**. Buttons are sometimes lighter than the body
  (AKAI RC-51A) and sometimes darker. Single-polarity detection silently
  returns almost nothing on half the catalog.
- A pass yielding near-zero must be **excluded from the vote**, not allowed to
  veto passes that worked.
- A detection containing 3+ others is a **recessed panel, not a button**. Drop
  the container, keep the contents.
- Two segmentation strategies are needed: Lab colour-distance handles grey
  gradient backgrounds; greyscale Otsu keeps adjacent remotes separate where a
  watermark bridges them. Run both, keep the more plausible result.
- Button **density carries no orientation signal**. Keypads sit at the top on
  some layouts and the bottom on others.
- The model-code regex **must** use the `BUTTON_VOCAB` blocklist. Without it,
  VGA1 / HDMI2 / AV2 get confidently misread as model codes.
- A low-contrast button thresholds as a **ring**, and a broken ring is traced
  as a stroke, so `contourArea` measures the outline and `min_fill` throws the
  button away. Measure the convex hull. This returned zero buttons on a
  five-button remote.
- Anything that gates on an **absolute count** eventually meets a remote where
  the true count is below it. `min_pass_yield` zeroed out a whole remote this
  way, exactly as absolute block sizes did in session 1.
- Background is estimated from the **image border**, so a product cropped
  flush to the edges makes the estimate land on the remote and the mask
  inverts. Tightly-cropped catalog images need the full-frame fallback.
- Do **not** filter text regions by `role == "label"` in brand or model-code
  extraction. It reads as obviously correct and is wrong: the 0.09 assignment
  radius makes nearly every region on a dense remote a "label", including the
  SONY wordmark and the RM-PJ20R code, while body text stays a "caption".
- Short tokens must match a brand **exactly**. At the fuzzy threshold, SPACE
  scores 88.9 against "Pace" and IRIS 88.9 against "Irbis".
- The unverified-wordmark fallback needs the **isolation test and the
  `LABEL_VOCAB` blocklist**, not just the height test. `median_h` is a
  statistic over however much text the OCR happened to read, so it moves
  between the catalog and query paths: on SMART_TV_T96 the query reads 18
  regions to the catalog's 25, the median drops, and the `APP` button legend
  clears `big_text_ratio` and becomes the brand "App" — on the query side
  only. A false brand is worse than no brand, because `brand_agreement`
  believes it.
- The model-code regex needs **two letter groups** before the digits, or
  RM-D1110 is stored as D1110 — a different remote as far as the index cares.
- Trace the **real pipeline**, not the saved `work/norm` crop. The saved crop
  is a re-encode; a trace against it hid the bug and pointed the wrong way.
- The OCR **angle classifier must stay off** (`CFG.ocr.angle_cls = False`).
  PP-OCR runs det → cls → rec, and cls rotates each text line upright before
  recognition, so a crop and the same crop upside down recognise *identically*
  and the text orientation signal is not weak but exactly zero. Both RapidOCR
  and Paddle default it ON. This single default was the whole of session 2's
  "11 of 21 unresolved". Symptom to recognise: two orientations scoring an
  exact tie, with the same word list both ways up.
- A **symptom that measures real** can still be the wrong thing to fix. The
  confidence term in `_text_score` provably diluted the vocabulary signal —
  and fixing it would have been wrong, because the dilution only existed while
  the angle classifier was making both orientations read alike. Chase the
  cause, not the term that correlates with it.
- Query and catalog extraction take **different paths through the same code**
  (`ensemble`, `fast_ocr`). A bug can therefore live on one side only: the
  HE5500 extracted correctly into the catalog and returned zero buttons on the
  query, because single-pass detection uses one block size. Test both.
- `detect_buttons` is **not invariant under a 180 degree rotation**, so never
  re-run it on a flipped crop — rotate the detections (`flip_buttons`). CLAHE
  is the entire cause: its tile grid is anchored to the crop corner, so
  rotating the pixels moves every tile boundary and shifts the equalised image
  by up to 18 grey levels. `adaptiveThreshold` held to one CLAHE output is
  exactly invariant. That shift is enough to move one ensemble pass from 12
  detections to 27 and the final count from 6 to 25, which made a record's
  fingerprint depend on a flip verdict taken *after* detection.
- A pass count that swings wildly for no visible reason is a **cliff, not a
  signal**. Before crediting either number, check whether the input changed at
  all — two counts that differ 2x on bit-identical pixels mean the vote
  threshold is what you are measuring.
- A JPEG with no final `FF D9` marker decodes into a **partly uninitialised
  buffer**. libjpeg writes the scanlines it has and leaves the rest as it
  found it, so the missing region is heap garbage, no error is raised, and the
  *same file decodes differently between two calls* — up to 18% of pixels on
  this sample. It broke three things at once: the build (`imread`) and the
  service (`imdecode`) saw different images, `URC-177500_Wink` extracted 15
  buttons upright or 12 flipped depending on the decode and was matched as a
  Ginzzu, and a rebuild could produce different fingerprints from unchanged
  inputs. 4 of 18 samples are truncated; a scraped catalog will be full of
  them. **All decoding goes through `app/pipeline/imageio.py`**, which appends
  the marker first so libjpeg terminates deterministically. Never call
  `cv2.imread` or `cv2.imdecode` anywhere else; `scripts/check_decode.py`
  asserts it.
- Three samples lack `FF D9` because they are **PNGs with a `.jpg`
  extension**. Check the SOI bytes (`FF D8` vs `89 50`) before calling a file
  truncated, or the "repair" corrupts a perfectly good image.
- **Offline metrics cannot see query-path bugs.** `match_eval.py` reads stored
  fingerprints and never uploads anything, so recall@1 8/8 and separation
  +0.077 held steady across *two* query-path defects — a decode bug that
  misidentified a catalog remote, and an orientation-trust bug that made
  another unretrievable. Use `scripts/query_drift.py`, which posts every
  catalog photograph to the running service; it reports identity recall
  (against `eval_truth.tsv`) and self-retrieval separately.
- Source watermarks must be stripped **at the OCR boundary** (`drop_watermark`
  in `pipeline/ocr.py`), not later: otherwise the stamp becomes index tokens, a
  button label, a brand, and — worst — feeds the text orientation signal, which
  it always resolves upright because the stamp is upright however the remote is
  lying. Toggle with `CFG.ocr.watermark_filter`, `RCU_WATERMARK_FILTER=0`,
  `RCU_WATERMARK_TERMS=...`, or `extract_one.py --no-watermark-filter`; every
  build prints which. The **height ratio is the load-bearing test**, not the
  fuzzy match: `EXIT` scores 67 against PULTOVNET and `NETFLIX` 60, so
  similarity alone deletes real buttons. Stamps run 1.4x-4.4x median text
  height, real legends 1.2x and below.
- **Calibrate on the crop, not the source image.** OCR runs on the rectified
  crop, so any rule using `y` must be measured there. A watermark band fitted
  on full images (stamp always at y 0.47-0.51) looked perfect and was useless:
  on the crops the same stamps land at y 0.29-0.50, and one sailed through and
  was stored as a caption. The "0 fragments remaining" check that missed it was
  run against full images too. Same family as the `work/norm` re-encode trap.
- A brand read via the **unverified wordmark path is worth less than nothing**.
  Measured over 57 records it fired once and invented "Ind" from OCR garbage on
  a 166x599 image, having passed every structural guard. Off by default
  (`CFG.brand.unverified_wordmark`); turn it on only with a measurement.
- A **query's orientation confidence is not the catalog's** and must never be
  trusted at the same threshold. A record's orientation comes from text OCR'd
  at full upscale both ways up; a query's comes from geometry, because
  `fast_ocr` drops the text signal — and geometry is confidently wrong often
  enough to matter. Applying the index threshold to the query meant
  `RM-PJ20_big_light` (query flipped at 1.00, record upright at 1.00) was
  never tried the other way up and never retrieved at all, and
  `Huayu_Motorola_Cisco_15` scored 0.488 instead of 0.901. Every query now
  goes both ways (`CFG.index.trust_query_orientation = False`). This costs no
  memory, unlike doubling the index, which is why the two sides get different
  rules.
- Do not map every service error to **503**. A 4xx is a verdict on the image
  and identical next time; a 5xx or a connection failure is about the service.
  Conflating them told users to retry what could not succeed and disguised the
  decode bug above as an outage for as long as it took to read the log.
- Broken images must be logged, skipped **and counted**. Skipping without
  counting is how a catalog quietly loses records: the summary reports what
  succeeded and nothing reports what vanished. `extract_one.py` distinguishes
  unreadable from readable-but-no-remote-found and appends both to
  `<out>/skipped.txt`.
- A catalog `record_id` is always `<photo stem>_<crop index>`, so strip
  exactly one trailing `_<int>` to recover the source image — and **never**
  short-circuit that by testing whether `<record_id>.jpg` exists. It sometimes
  does and means something else: this sample holds both
  `ROLSEN_RSF-3106RT.jpg` and `ROLSEN_RSF-3106RT_0.jpg`, two different
  remotes, and the existence test points one record at the other's photograph.
- In `routes/web.php` the literal `catalog` segment must be registered
  **above** the unconstrained `{requestId}` routes, which otherwise match the
  string "catalog" and swallow the entire section.
- `reviewed` and `model_id` are the only two catalog columns a **person** owns
  rather than the extractor. A rebuild that overwrites them empties the review
  queue and unlinks the catalog joins, and neither can be recomputed.
- The legacy `files/` directory is **not a directory of remotes**. A third of
  it is replacement-model promo banners (`Zamena_*`) and scanned instruction
  sheets, hung off the same products at `delta >= 1`. The delta=0 rule was
  known, documented in `config/rcu.php` and pinned by a test — and the thing
  that decides *what gets extracted* ignored all of it, because that decision
  lives in `docker/build-catalog.sh`, which had no database and just globbed
  the directory. Measured on the sample drop: 165 fingerprints of which 56
  (34%) could never be keyed, versus 109 and zero once the build reads a
  manifest. **A rule enforced at the consuming end is not enforced.** The
  producing end is `rcu:legacy-manifest`; both ends now call
  `App\Support\LegacyCatalog::primaryPhotos()` so there is one definition.
- Metadata that fails to match imports as **`title` and `model_id` NULL**,
  which is also what a genuine catalogue gap looks like. That is why the 56
  sat there unnoticed: nothing distinguishes "we extracted an instruction
  sheet" from "this remote has no row". Count what is excluded at the point of
  exclusion, where the reason is still known.
- The Laravel container mounts `work/` **read-only**, so anything Laravel must
  hand the build travels over stdout (`rcu:legacy-manifest --out=-`) with the
  diagnostics on stderr. `getErrorStyle()` silently falls back to stdout when
  there is no real console, so `$this->artisan()` cannot assert that split —
  verify it against the container.
- **`docker compose exec` runs the image, not the checkout.** A measurement
  taken against a stale container is a measurement of old code: the first run
  of the legacy import here reported 165 of 165 unmatched, which was the
  pre-fix nid keying still baked into an image built before the fix landed.
  `docker compose build laravel` before believing any number from `exec`.

## Next up (session 6)

Session 5 wired Laravel end to end, retired the memory ceiling, and found one
real defect the offline pipeline structurally could not see: a truncated JPEG
that indexed fine and could never be queried (gotcha above). Retrieval
unchanged: recall@1 8/8, separation +0.077.

Session 5 measured the query path for the first time (`query_drift.py`) and
found two defects offline metrics structurally could not see. Across the
session: self-retrieval recall@1 **14/18 -> 17/18**, recall@5 **15/18 ->
18/18**, high-confidence answers **10 -> 14**, `none` **4 -> 1**. Offline
recall@1 8/8 and separation +0.077 never moved.

1. **Low-contrast keycap detection (plan 9.1)** — a trained detector. The only
   thing that moves `MR-18B_0_1` off 4 buttons and separation off +0.077, and
   the only substantial CV item left.
2. **Button drift between query and catalog: 7/18 records, mean abs 3.4.**
   Cause is `fast_ocr` reading fewer text regions, so
   `suppress_text_detections` cuts less and the query keeps detections the
   build removed. Measured as **not costing recall** — a known asymmetry, not
   a defect. Revisit only if a record is found where it costs something.
3. Re-validate the confidence bands on a real catalog. More urgent than it
   looks: the bands shifted hard this session without being touched, purely
   because scores rose, which shows they are calibrated against nothing.
4. `work/fp.bak-*` is four stale catalog copies. Keep the ones documenting a
   real behaviour change, drop the rest.

## Previously (session 5's list, all done or superseded)

Session 4 fixed the flip re-detection bug (see the gotcha above), tightened the
unverified-brand fallback, and **struck two long-standing items off the list**,
each disproved by measurement:

- *Text/button arbitration.* Suppression cut **2** detections from
  `MR-18B_0_1`, not 17, and every detection it cuts on `SMART_TV_T96` is
  text-sized rather than a keycap. What looks like deleted keycaps is
  `detect_buttons` never finding matte-black keys in the first place.
- *Visual wordmark templates.* 11/21 is **11 of 11 achievable**: eight of the
  ten brandless records have no manufacturer wordmark printed on them, and the
  other two are the known-BAD extractions. The stylised `aiwa` that session 2
  recorded as reading "EMIE" reads correctly now.

Retrieval unchanged throughout: recall@1 8/8, separation +0.077.

Laravel was session 5's item 1 and is **done**. The other two carry forward
above.

## Do not

- Do not add OCR requirements to the geometric verifier.
- Do not "clean up" the dual-segmentation or dual-polarity paths as
  redundant. Both are load-bearing; see gotchas above.
- Do not bypass `app/pipeline/imageio.py` with a direct `cv2.imread` or
  `cv2.imdecode`, however local the need looks. The whole point is that one
  decoder serves both paths; a second call site is how they diverged before.
- Do not trust `match_eval.py` alone after touching anything on the query
  path. It is self-consistent by construction and stayed at 8/8 through a bug
  that misidentified a catalog remote.
- Do not tune fusion weights to fix a match failure. The cause is nearly
  always upstream in extraction — check the overlay first.
