# RCU Identifier

Photograph a TV remote control, identify its model from a catalog of
10k–50k records (brand, model, photos, mostly scraped from the web).

Read `docs/rcu-identifier-implementation-plan.md` for the full design, and
`rcu-session-01/SESSION-01.md`, `service-python/SESSION-02.md`,
`service-python/SESSION-03.md`, `service-python/SESSION-04.md`,
`service-python/SESSION-05.md`, `service-python/SESSION-06.md`,
`service-python/SESSION-07.md` and `service-python/SESSION-08.md` for status
and known-bad behaviour. Session 08 is the current one. Session 04 corrects two
claims session 03 made; session 05 retires the memory constraint that shaped
sessions 03 and 04, so treat any `--ocr-width 800` advice in those two as
historical.

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

# ask what a confidence band actually promises: uploads every catalog photo,
# then sweeps the thresholds and reports the precision each one would buy.
# Needs a catalog with confusable records; a small clean sample returns 100%
# in every band and tells you nothing.
python scripts/calibrate_bands.py --photos ../files --manifest ../work/primary.txt \
    --fp ../work/fp --csv ../work/bands.csv

# assert the decode invariant (build and service agree; decoding is stable).
# Run over any new catalog drop; exits non-zero on violation.
python scripts/check_decode.py --dir ../photos

# plan 9.1 step 2: the labelling queue, out to Label Studio and back.
# Read scripts/label_studio.md; the hand-correction is not optional and the
# reasons are measured in export_button_dataset.py.
python scripts/export_button_dataset.py --fp ../work/fp --norm ../work/norm \
    --out ../work/dataset --split hard --queue-size 400
python scripts/label_queue.py export --fp ../work/fp --dataset ../work/dataset
python scripts/label_queue.py import --export ../work/dataset/ls_export.json \
    --dataset ../work/dataset

# assert a box survives that round trip before spending an afternoon on it
python scripts/check_label_roundtrip.py --fp ../work/fp --norm ../work/norm

# the service (loopback only; Laravel calls it)
RCU_INTERNAL_TOKEN=... RCU_INDEX_PATH=../work/index/tokens.npz \
    RCU_FP_DIR=../work/fp python -m app.main
```

```bash
# Laravel: load work/fp into the catalog table, prune deleted records, and
# tell the running service to reload its index
cd backend-laravel
php artisan rcu:import-catalog --prune --reindex
php artisan test                        # 87; 5 need the service running

# after any extraction: resync both consumers and check they agree (host, not
# a container). --calibrate then sweeps the bands over a sample of real uploads.
cd .. && ./docker/resync-catalog.sh --calibrate --sample 500

# the phone test page. No auth -- dev boxes only, never anywhere public.
# Set RCU_TRY_PAGE=true in .env (host) and docker-compose reads it too, then
# rebuild: `docker compose exec` runs the image, not the checkout.
docker compose build laravel && docker compose up -d laravel   # -> /try

# On the legacy catalogue: decide what to extract BEFORE extracting it, and
# take metadata from the catalogue afterwards. files/ is a third non-remotes.
php artisan rcu:legacy-manifest --out=- > ../work/primary.txt
php artisan rcu:import-catalog --legacy --prune --reindex
```

Extract in **bounded batches**, not one image per process and not a whole
directory in one. `build-catalog.sh --batch N` (default 200) hands N images to
a process and then lets it exit.

One image per process was the rule through session 5 and it was expensive:
loading the OCR model costs more than extracting a catalogue-sized image, so
paying it per image dominated the build. Measured on rcud over the same 60
photographs, byte-identical output either way — one process per image at
`--jobs 2` took 11m30s (11.5 s/image, ~23 s CPU/image); one process running all
60 serially took 8m05s (8.1 s/image). On 13763 photographs that is ~44 h
against ~15 h.

The batch stays bounded because the other half of the old rule is still true:
OCR runs twice per body on an upscaled crop, peak RSS is ~700 MB on a 5 MP
image, and the whole-directory run this replaced was OOM-killed. Letting each
process exit after N images keeps the model load amortised without betting on
the growth being absent. Measured at 580-800 MB across a 60-image batch with no
upward drift.

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
  Session 6 added `/try`, a phone-facing page that photographs a remote and
  runs the real query path — it calls `/api/identify` and the feedback
  endpoint as any client would, deliberately taking no shortcut through the
  service. It has **no authentication** and is off unless `RCU_TRY_PAGE=true`.
  Note the admin visualiser is currently unreachable on any box: it declares
  `auth` middleware and this application has no login route at all.
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
- The legacy `files/` directory is also **not where most of the photographs
  are**. 3069 of 13773 originals survive; the other 10693 exist only as Drupal
  imagecache derivatives, which is all the public site ever serves. Looking
  only in `files/` builds a catalog of 22% of the catalogue and nothing says
  so — the missing 78% look like ordinary "not on disk" lines. The manifest
  searches `rcu.catalog.files_search_path` (original, then
  `imagecache/watermark/files`, then `imagecache/product/files`; first hit
  wins) and reaches 13763. Manifest lines are paths relative to `files/`, not
  bare names. The stem is unchanged in a derivative, so the record still keys
  onto its row — that is the whole reason the fallback is safe, and it is
  pinned by a test. `watermark` is the largest preset Drupal keeps and has the
  PULTOVNET stamp burned in, which is exactly what `RCU_WATERMARK_FILTER`
  strips at the OCR boundary; do not turn that off while building from
  derivatives.
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
- A container that writes into a bind mount must run as the **host uid that
  owns it** (`RCU_UID`/`RCU_GID`), or every fingerprint, overlay and index it
  produces is root-owned and the host needs another container to delete its
  own build output. Only `extract` writes to `work/`; `rcu-service` mounts it
  read-only, because the only writer in the Python tree is
  `scripts/build_index.py`.
- A **thumbnail extracts confident buttons**, because rectification upscales
  every body to `CFG.normalize.out_width` (400px) whatever the source was. A
  16x50 imagecache thumbnail is enlarged 25x and `detect_buttons` traces the
  interpolation artefacts: `2376.jpg` yielded **29 buttons** at quality 0.66,
  indexed, and self-matched at 0.925. Nothing downstream can tell that from a
  real extraction. `CFG.normalize.min_source_long_side` (600) refuses it at
  the build. Long side, not both sides: a remote is elongated and the real
  catalogue standard is 303x1090, so a square rule discards 52 of the 62
  usable images in the dev sample.
- A **constant score across many different records is not a score.** 41 of 91
  queries in the first band calibration returned exactly 0.9250 and 7 exactly
  0.3750. That, and 100% precision in *every* band including `low`, and
  separation +0.559 where the real set gives +0.077, all had one cause: 72 of
  the 91 queries were thumbnails. Before believing a retrieval metric, look at
  the distribution of the scores, not just their mean.
- A record with **zero buttons and zero text regions has no tokens**, so it can
  never be retrieved by anything, yet it still costs a catalog row, an index
  doc and a fingerprint file. 29 of 109 were like this and the build called
  every one of them `1 remote(s) extracted`. Refuse them at extraction and
  count them, with the source dimensions in the reason — the dimensions are
  what identifies the cause.
- **Which crop of a photo to keep is a question about size, not position.**
  "Keep only the leftmost body" is the intuitive rule and it is wrong: crop
  `_0` is the best-quality crop of its photo only **55%** of the time and the
  largest only **43%**, and over 12311 records the rule costs **816** records
  to remove **28** bad ones, because 620 of the later crops are full-size
  bodies — usually a second colour variant in the same shot. What does
  discriminate is button count *relative to the same photograph*
  (`sibling_min_button_ratio`, `sibling_min_buttons`), and it needs both a
  relative and an absolute test: relative alone deletes real sparse faces
  (`ClickPdu_Air_Mouse_G30S` has 4 buttons and means it), absolute alone
  deletes every small remote. The most-buttoned crop cannot fail its own ratio
  test, so no photograph can ever be left with no crop.
- A crop can be junk by having **too many** buttons, not too few. A remote
  photographed beside its instruction manual (`2750`) yields crops of the
  printed page holding 50, 92 and 112 "buttons" against the real remote's 61,
  traced out of halftone and a printed diagram. Rectification is the cause
  again: every body is upscaled to `normalize.out_width` whatever its source
  size. `min_source_long_side` does not catch it, because that guard measures
  the *photograph* and a 0.046-area body inside a photograph that clears it
  walks past. What separates the populations is **buttons per 1000 source
  pixels** (`max_button_density`): over 12218 records the middle 80% sit
  between 0.11 and 0.44, the leaflet crops at 2.2-7.8, the worst record at 36.
  Above 3.0 a button occupies under 330 source pixels, which is a resolution
  statement rather than a threshold. In the band below it the siblings decide
  (`sibling_max_density_ratio`), but only above `sibling_density_floor` —
  judged on the ratio alone the rule fires on good crops whose sibling merely
  has fewer buttons, costing 31 records with label recall above 0.20 instead
  of 1. Unlike the sparse test this one **can** empty a photograph, and on 8 of
  11473 it does; every one is a blister-card fragment or an LCD panel.
- Every crop of one photograph is the **same catalogue product**, because
  metadata joins on the photo stem. `HTR-U29A_0` and `_1` both carried
  `model_id 12257`. A second crop can never be labelled differently, so it is a
  duplicate at best and a mislabelled remote at worst — never a way to cover
  more products.
- **Compose injects environment at container-create time.** Editing `.env` on
  the host and reloading the page changes nothing: `RCU_TRY_SIMPLE=false` sat
  in `.env` while `printenv` in the container still said `true`, and the
  feedback buttons stayed hidden. `docker compose up -d <service>` recreates
  it; `restart` does not. Check `docker compose exec <svc> printenv` before
  believing a flag took. Same family as the next one.
- **`docker compose exec` runs the image, not the checkout.** A measurement
  taken against a stale container is a measurement of old code: the first run
  of the legacy import here reported 165 of 165 unmatched, which was the
  pre-fix nid keying still baked into an image built before the fix landed.
  `docker compose build laravel` before believing any number from `exec`.

## Next up (session 8)

The catalogue is **complete and consistent**: 12079 fingerprints, 12079 catalog
rows, 12211 index docs, both consumers in step. (Session 7 left 12311/12515;
session 8 removed 232 records that were crops of scenery — see SESSION-08.md.)
Session 7 rebuilt it end to end
and fixed nine extraction defects found by reading overlays in the review
queue; see `service-python/SESSION-07.md`, and `SESSION-08.md` for where the
remaining errors are.

Bands are calibrated for the first time, on 254 live uploads: recall@1 95%,
`high` 100% precise over 195 queries, `medium` 78% over 59. `high` moved to
0.65/0.10. `low` and `none` are still uncalibrated and self-retrieval cannot
calibrate them -- the answer always exists.

1. **Calibrate `low` and `none`** from real `/try` uploads of remotes that are
   *not* in the catalogue. `rcu_queries.error` now separates an outage from a
   verdict, so the data is trustworthy.
2. **Read the 13 wrong `medium` answers** in `work/bands.csv` -- a population,
   not an anecdote, and the cheapest lead on what the matcher gets wrong.
3. **Plan 9.1 step 2** — hand-correct the label queue, then train. The tooling
   is built and verified; an edge pass was measured and does not substitute.

## Previously (session 7, complete)

Session 7 went after the corpus session 6 could not find: the whole live
catalogue, extracting on `rcud` since 2026-08-05 01:54 and due around 07:00 on
08-06. See `service-python/SESSION-07.md` for the build's measured position and
health, and for how to read its progress (match the newest `fp/*.json` stem
back to `primary.txt`, never count fingerprints).

Closed this session: the query-path size floor, bounded-batch extraction (44 h
-> 15 h), plan 9.1 step 1, and the missing login route that had been returning
500 from `/admin/rcu` in production since session 5.

1. **Low-contrast keycap detection (plan 9.1)** — step 1 (pseudo-label export)
   is done; step 2 is hand-correction and is *not* skippable, for reasons
   measured in SESSION-07.md. Still the only substantial CV item.
2. **Calibrate the bands** once the build lands. Resync both consumers first —
   `build_index.py`, then `rcu:import-catalog --legacy --prune --reindex` — or
   retrieval returns `record_id`s that resolve to no row.
3. Button drift between query and catalog: unchanged, measured as not costing
   recall.

## Previously (session 6's list, from session 5)

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
- Do not train on the uncorrected pseudo-labels, however good the quality
  scores look. An unlabelled keycap is a *negative* to YOLO, high-quality
  extractions are still systematically incomplete (`CAS-400_0` loses a whole
  keypad at 0.91), and no automatic completeness test found works — both
  candidates fail on that same record. See `export_button_dataset.py`.
- Do not write an empty YOLO label file for a crop nobody labelled. Empty does
  not mean "unknown", it means "entirely background", which is the most
  damaging sentence the dataset can contain. `label_queue.py import` skips and
  counts those; an annotated-with-no-boxes task is different and is kept.
