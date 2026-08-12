# Session 09 — latency, and the rest of what session 8 started

Session 8 was about the catalogue being *right*. This one is about a query
being *fast*, plus the three things session 8 left in flight.

## Inherited from session 8

### The rebuild — done

1964 photographs re-extracted, no kills, both consumers resynced and in step:

```
fp files 12237  =  catalog rows 12237  =  index_records 12237   (12379 docs)
```

Live through `/identify`, after rebuilding **`rcu-service`** as well:

```
STV-22LED5-org   -> STV-22LED5-org_0         high  0.7356 | next 0.4998
RM-L810          -> RM-L810_0                high  1.3044 | next 0.9750   2 bodies
SHIVAKI-STV-22LEDG9-1 -> ..._1               high  0.9184 | next 0.4669   3 bodies
2750             -> 2750_0                   high  0.9118 | next 0.6108
Sherwood_TX-757  -> Sherwood_TX-757_0        high  0.9168 | next 0.4096
```

**Two traps this cost, both already in CLAUDE.md and both sprung anyway:**

* The `extract` image was rebuilt after the frame-split fix and `rcu-service`
  was not, so the *query* path went on fusing the pairs — `RM-L810` came back
  as `RM-L859-1_0` at high confidence, from one body of 82 buttons. Rebuild
  **every** image that carries the code, not the one you were thinking about.
* The affected-record set was selected with a hand-written fragment list
  rather than with the filter's own score, so 129 photographs whose strapline
  OCR'd too badly for the list were never re-extracted. Zero records *inside*
  the rebuilt set failed the check; the residue was entirely outside it.
  **Select the set to re-extract with the rule that does the filtering.**

Strapline now: **0** regions at or above the threshold, from 130. 360 garbled
fragments remain below it by design, on 351 records of 12237. Lowering the cut
from 70 to ~62 would take ~225 of them, but that is the band where real
legends start appearing — decide it with the boundary listing in front of you,
not from the count.

### `RCU_ASSUME_UPRIGHT` — deployed on `rcud`

`.env` holds `RCU_ASSUME_UPRIGHT=1` and compose passes it to `rcu-service`
only, so the catalogue build cannot see it. A/B on the live endpoint, best of
two per photograph, container recreated between runs:

```
                    UPRIGHT=1     UPRIGHT=0
2749                  5212 ms      14995 ms     -65%
STV-22LED5-org        6818 ms       7316 ms      -7%
Sherwood_TX-757      12832 ms      34224 ms     -63%
3510                  3379 ms       8983 ms     -62%
2750                 25558 ms      48378 ms     -47%
TOTAL                53799 ms     113896 ms     -53%
```

Same top-1 and same band in both configurations on all five. The one that
barely moved (`STV-22LED5-org`, -7%) is the case where geometry was already
confident enough to skip the second pass, so there was nothing to save.

`2750` stays slow at 25 s because it holds four bodies and stages 4-10 run per
body. Bodies-per-photograph is the remaining latency lever and nothing has
been done about it.

**Also deployed:** `min_source_long_side` 600 -> 500 and `fuse.verify_top_m`
25. The floor change affects uploads immediately; it adds no catalogue records
until something re-extracts, and it admits 1222 photographs whose short side
runs as low as 85px — watch that band in the review queue.

### numpy 2.2.6 — the largest single win, and it was not the one expected

Bumping numpy looked like an 11% saving on verification. On the live endpoint,
same five photographs, same best-of-two method:

```
                   numpy 1.26.4    numpy 2.2.6
2749                   5212 ms        2515 ms
STV-22LED5-org         6818           3267
Sherwood_TX-757       12832           4957
3510                   3379           1315
2750                  25558          10810
TOTAL                 53799 ms       22864 ms      -58%
```

Identical answers, identical scores to four decimals, identical bands. So the
pipeline is far more numpy-bound than the verification profile suggested — the
11% was measured on RANSAC alone, and the gain is spread across every array
stage. Combined with `RCU_ASSUME_UPRIGHT`, `/try` is **113896 ms -> 22864 ms
on this set, -80%**.

What made it installable is a packaging change, not a version bump: pip
resolves the metadata of everything *named* in a requirements file even when a
later step installs it `--no-deps`, so `rapidocr-onnxruntime`'s declared
`numpy<2.0.0` was constraining the pins from the listing alone. The two OCR
wrappers now live in `requirements-ocr.txt`, installed `--no-deps` by the
Dockerfile and by install.sh — which had no such step before, and is why they
could not simply be deleted from `requirements.txt`.

Both installers now **assert** the outcome (`cv2.__version__` starts `4.10.`)
rather than trusting install ordering. `--no-deps` means nothing else would
notice the full OpenCV build shadowing the headless one, and the OpenCV
version changes every fingerprint.

### `ocr_only_best` — deployed

A query answers from one body and discards the rest, so only the winner's text
is read. Deployed and measured on the live endpoint:

```
2749              2682 ms      Sherwood_TX-757   3843 ms     (2 bodies)
STV-22LED5-org    3358         3510              1616
2750              4194         <- 10810 before, four bodies
TOTAL            15693 ms
```

Cumulative for the session: **113896 ms -> 15693 ms, -86%**, same answers
throughout.

Two bugs in it, both from one root, both found by checking rather than by
symptom: a body's button count *falls* once it is read, because
`suppress_text_detections` deletes detections that turned out to be printed
words. So a selection made before OCR and one made after are not the same
selection, and the query could be answered from a body whose text was never
read — `MR-18B_0` came back with no labels, no brand, no model code, and
nothing in the response said so. Both paths now rank on `n_detected`, the
pre-suppression count, which is the only number that exists on both sides of
the read.

### Why a near-identical remote never reaches the candidate list

Reported from the live site: a query matched item 12994 (`RC4875_0`) correctly,
but item 11913 (`RC4849_0`) — the same physical remote, listed twice — never
appeared among the candidates.

Not the `verify_top_m` cut, which was the obvious suspect: `RC4849_0` is not
in the 100 that tier 1 retrieves, so nothing downstream could have shown it.

The two extractions of one remote:

```
RC4875_0 (12994)   detected 58 buttons -> 8 cut as text  -> 50
RC4849_0 (11913)   detected 45         -> 16 cut         -> 29
token overlap 55 shared of 679/477, Jaccard 0.050
```

Three fixes were tried on the retrieval side and **all of them fail**:

* *Turning text suppression off* makes it worse — 45 buttons, and `RC4875_0`
  stops being retrieved at all. The extra detections are row-shaped boxes, not
  keycaps, and they generate tokens that are simply wrong.
* *Loosening `keep_area_ratio`* 2.2 -> 0.7 moves the button count 29 -> 31 and
  nothing else.
* *`norm_exponent`*, the document-length normalisation, moves `RC4849_0`
  between ranks 1585 and 1688 of 12050 across its entire range 0.0 to 1.0.

Rank ~1600, not rank 101. There is no retrieval-side tuning that reaches it,
because tokens are position-quantised buttons and 21 missing buttons are 21
grid cells that emit nothing. **This is a detection problem and only plan 9.1
addresses it.**

Two things worth carrying:

* The relationship is **asymmetric**. Querying with `RC4849` finds `RC4875` at
  tier-1 rank 2; querying with `RC4875` never surfaces `RC4849`. A rich token
  set covers a sparse one, not the reverse. So a user photographing this remote
  gets the right answer — it is the sparse *catalogue record* that cannot be
  found, and it would lose to nothing.
* **`extract_quality` is blind to it.** `RC4849_0` scores 0.923 with label
  recall 0.34, both healthy, on a record that lost 40% of its buttons. The
  audit queue cannot surface this class, so nothing will report it.

### The image dedupe — applied

```
fp files 11656  =  catalog rows 11656  =  index_records 11656   (11792 docs)
581 records pruned, from 560 photographs
IRC_new_237 records: 123 -> 3
```

589 photographs in 253 groups collapsed, exactly as measured. The 120-way
exact tie is gone with them: `IRC_new_237_1` used to return 0.9143 against a
runner-up of 0.9143 and now returns **0.9047 against 0.7701**.

Before pruning, the mixed-group check: 130 of 253 groups have every member
sharing a model code, 95 share none, 28 have too few titles to judge. The 95
are almost all the expected pattern -- one universal remote listed once per
brand code set, so the titles name *different* codes by design. Two arguments
settle the rest: every member of a group has the same photograph, so
deduplication removes metadata rows and no visual information whatever; and
the genuine errors it does hit (`IRC_new_1169`, "G0891CESA for Sharp DV-6311S"
sitting on an IRC image) are records that answer *wrongly* today, so dropping
them shrinks the wrong-answer surface rather than the catalogue's reach. `rcu:legacy-manifest` now collapses
byte-identical photographs (253 groups, 842 photographs, 589 redundant), but
the live catalogue was built before it. Nothing needs re-extracting — the
dedupe only removes:

```bash
docker compose build laravel          # exec runs the image, not the checkout
docker compose exec laravel php artisan rcu:legacy-manifest --out=- > work/primary.txt
# then delete fp/norm/debug for photos no longer in the manifest, reindex,
# and rcu:import-catalog --legacy --prune --reindex
```

**Before that prune, check the mixed groups.** Dedupe assumes every member of
a hash group is the same remote. `IRC_new_1169_0` is titled *"G0891CESA пульт
для Sharp DV-6311"* and sits inside an IRC group — a placeholder image on an
unrelated product, which will lose its row and inherit a wrong canonical. List
every group whose titles share no common model token before pruning; it is one
query and it decides whether this is one bad row or a hundred.

## Latency: what shipped, and what it measured

`/try` cost ~7.1 s of internal work against a ~1 s budget. Text was 72% of it
and verification 22%; nothing else was worth touching. See
`docs/plan-faster-queries.md` for the full plan and the rejected branches.

Shipped, all query-path only, no rebuild:

```
baseline                        28456 ms   (4 photographs, extraction only)
+ assume upright                10933 ms   -62%
+ assume upright + openvino      8405 ms   -70%
```

* **`RCU_ASSUME_UPRIGHT=1`** — off by default. Skips the second OCR pass *and*
  the both-ways-up verification, and suppresses the flip verdict itself.
* **`fuse.verify_top_m = 25`** — verify the best 25 by tier-1 score rather
  than all 100 retrieved.
* **`verify.ransac_adaptive`** — implemented, measured, left **off**. 42% off
  RANSAC in isolation, ~20 ms once `verify_top_m` is in front of it, and it
  moves 1 pair in 192 before any early exit because batched `np.linalg.solve`
  is not bit-identical to one large solve.
* **`rapidocr-openvino`** installed and in requirements `--no-deps`. 35%
  faster than onnxruntime here, reads slightly *more* text.

### Rejected by measurement — do not re-propose

* **Removing or enlarging the OCR bands.** 1400 is the optimum in both
  directions: 600 → 10181 ms, 900 → 8833, 1400 → 7261, 2000 → 9159, whole →
  8246. The config's claim that bands preserve detection resolution is wrong
  at `det_limit_type="min"` — the detector only ever upscales, so `ratio` is
  1.0 banded or whole.
* **A cheap geometric prefilter** (count ratio, colour histogram, radial
  keypad profile). Worse than tier-1 score at ranking the correct answer, and
  cost recall@1 8/8 → 6/8 at `verify_top_m=5`. Kept as the docstring of
  `app/matching/prefilter.py`, because the reason generalises: the features
  that are cheap are the ones that break when detection recall differs between
  query and record, which is the normal case.
* **Tesseract, EAST + small CRNN.** The win in that family is the runtime, not
  the architecture — see `RapidOcrOpenvinoEngine`.

## Bands, recalibrated on honest truth

`rcu:export-truth` writes `record_id -> model_id` and `correct_answers()`
unions a photograph's crops with every record the catalogue calls the same
product. 326 queries against the 11656-record catalogue:

```
self-retrieval recall@1  319/326 (98%)
separation               +0.407 mean over 321 queries
high    n=309   precision 100%
medium  n= 11   precision  91%      <- read 78% under the old truth
low     n=  5   precision   0%
none    n=  1   precision   0%
```

`medium` was never 78%: the truth function was keying on the filename stem, so
a right answer under a second filename scored as a miss. It is 91%, on a much
smaller n because 309 of 326 now land in `high` at 100%.

**`low_score` 0.30 -> 0.45**, on the floor sweep against the 7 wrong answers
and the 321 correct ones:

```
floor   wrong rejected   correct lost
 0.30        1 / 7            0
 0.45        4 / 7            0
 0.50        6 / 7            0
```

Zero correct answers are lost anywhere up to 0.50, so this was slack being
given away rather than a trade. 0.45 rather than the 0.50 the data allows, for
the same reason `high` sits at 0.65 and not 0.55: every query here is a
catalogue photograph matched against itself, and a phone photograph scores
lower. The gap is for the difference between measurement and deployment.

**This is still not a calibration of `none` against absent remotes.** That is
a different question and needs uploads of remotes the catalogue does not hold.
`rcu_queries` holds 112 rows and **4** marked `none_of_these` (2 medium, 2
low). Roughly 30 would make it measurable, and only real `/try` traffic can
supply them.

## Plan 9.1: the labelling queue, aimed

Built from the live catalogue rather than the 21-record dev sample, and aimed
at the failure `extract_quality` cannot see.

**The signal.** OCR reads a legend whether or not detection found the key it is
printed on, so a legend-shaped text region with no button near it is direct
evidence of a missed button. On the pair that exposed all this:

```
RC4875_0   50 buttons   3 orphan legends   share 0.06   quality 0.926
RC4849_0   29 buttons   9 orphan legends   share 0.24   quality 0.923
```

Four times the signal where quality shows none. Over the catalogue the share
runs p50 0.03, p90 0.12, p99 0.30 — and **the worst records score 0.81-0.95
quality**, so a quality-stratified queue would never show a labeller one of
them. `242254901404_0` is a black-on-black Philips: 22 buttons found, 21
legends left with no key under them, quality 0.933.

**One confound, and it is worth knowing separately.** The raw ranking is topped
by crops that are printed pages — `2098_1` is a VCR code table photographed
beside the remote, 62 text regions against 44 "buttons", 50 orphans that are
all table rows. Excluded from the queue (a labeller would label nothing), but
15 such crops are catalogue records, and the density rule cannot see them
because they are full-size. That is a small residual defect class of its own.

**The queue**, `work/dataset/` on `rcud`, 400 images / 48 MB:

```
200 by orphan share   (203 with share >= 0.20)
200 stratified by quality
2045 orphan legends to add, against 12313 buttons already boxed
quality median 0.918, range 0.229-0.975
```

Half and half deliberately: a training set drawn entirely from one failure
mode teaches the detector that mode and nothing else.

**What is left is the part no tooling replaces** — `label_queue.py export`,
correct the boxes in Label Studio, `label_queue.py import`, then train.
`check_label_roundtrip.py` asserts a box survives the trip before anyone
spends an afternoon on it. The project's own rule stands: do not train on the
uncorrected pseudo-labels, however good the quality scores look.

## `none`, calibrated at last — without scraping anything

An absent remote cannot be simulated by querying the catalogue with its own
photograph, because the answer is always there. It can be simulated *exactly*
by removing it: query with a record's fingerprint while excluding every record
of the same product, and what comes back is the best answer available for a
remote the matcher cannot hold. 400 queries:

```
best available score   p50 0.613   p90 0.957   max 1.257
band reported          high 179 (45%)   medium 172   low 66   none 91
```

45% confident answers for remotes that are not there — which sounds fatal and
is not, because of what those answers *are*:

```
same remote, a code in common            37     RS41C0 -> RS41CO
sibling model, code differs by 1 char    55     RAV463 -> RAV462
sibling model, code differs by 2         37     RC022-02R -> RC022-01R
same family, differs by 3-4              16
unrelated code                           21     <- 5% of 400
no comparable code in either title       13
```

129 of 179 are the same remote or a part number one or two characters away —
physically near-identical hardware, and for a shop selling replacements often
the right thing to offer. The genuinely unrelated answers are **21 of 400,
about 5%**, and several of those are rebadges too (`SANYO MXAE` ->
`EIKI MXAF`, two projector remotes; `Digifors HD73` -> `Iconbit Movie T2`, two
generic DVB-T2 handsets).

**So the failure is not identification, it is presentation.** Faced with an
absent remote the matcher finds its nearest sibling and says `high`. That is
the correct retrieval result and the wrong thing to *say*. A floor cannot fix
it: rejecting everything up to 0.65 would still only reject 55% of absent
remotes, and would cost real answers, since `high` is 100% precise when the
answer is present.

What works is the opposite of a floor — say what was actually found. The
`not_in_catalog` hint added this session is the first half; the second is that
a `high` answer whose model code disagrees with the query's is a *sibling*,
and the UI should offer it as "closest we stock" rather than as the remote.

**Real absent-remote uploads remain worth collecting**, for the one thing this
cannot test: a phone photograph rather than a catalogue photograph. The two
data points so far both behave: a Supra STV-LC1504 at 0.42, and a Technika
DTV1 at 0.299 — the latter from a crop scoring 0.943, correctly `none`, and
the case that produced the hint fix above.

## Next

1. **Land the inherited work above.** Nothing else should start first.
2. **Enable `RCU_ASSUME_UPRIGHT` on `rcud` and re-measure `/try` end to end**,
   with `query_drift.py` against `eval_truth.tsv` — not `match_eval.py`, which
   never uploads and has stayed at 8/8 through two query-path defects. Then
   measure how often a real upload is genuinely upside down; that is the
   number the flag trades against.
3. **Re-measure `verify_top_m` on the live catalogue — still open, and the
   obvious route is a dead end.** 25 is set from eight pairs over 21 records.
   Two attempts to calibrate it against the real catalogue both failed, and
   the reasons are worth not repeating:

   * *Pair records sharing a `model_id`.* Returns **zero** usable pairs:
     metadata joins on the photo stem, so every same-`model_id` group is
     crops of ONE photograph, which are not independent queries.
   * *Pair `X.jpg` with `X_N.jpg`.* Produces 314 pairs and a terrifying
     "48% of answers never retrieved" — which is an artefact, not a finding.
     The heuristic is provably wrong: CLAUDE.md records that
     `ROLSEN_RSF-3106RT.jpg` and `ROLSEN_RSF-3106RT_0.jpg` are **two
     different remotes**. Most of that 48% is different products.

   What is left is the direct A/B: run the same queries with `verify_top_m=0`
   (verify everything) and with 25, and compare the top-1. Self-retrieval is
   fine for that, because the question is whether the cut *changes the
   answer*, not what the absolute recall is. A first attempt over 300 queries
   × 5 settings exceeded ten minutes — sample smaller, or run it detached.
4. **Decide OpenVINO.** It changes what is read, so it needs both paths and a
   full rebuild — schedule it alongside anything else that needs one.
5. **Fix the truth function** in `calibrate_bands.py` before quoting a band
   figure again. Neither the filename stem nor `model_id` is enough: records
   sharing a source image are all correct answers, and 120 of them share one.
6. **Calibrate `low` and `none`** from real `/try` uploads of absent remotes.
   Still the only thing that cannot be measured from the catalogue itself.
7. **Plan 9.1 step 2** — hand-correct the label queue, then train. Unchanged,
   and still the only substantial CV item.

## Do not repeat

* **Do not run anything heavy on `rcud` while a build is running.** Session 8
  OOM-killed a rebuild by recreating a container mid-run. Measurement jobs
  belong on the dev box, which is what the numbers above were taken on.
* **Do not `pip install` a rapidocr wrapper without `--no-deps`.** Both
  declare loose dependencies that pull `opencv-python` over the headless build
  and `numpy` 2.x over the pinned 1.26.4 — silently changing the numeric
  library the catalogue was extracted under.
* **Do not trust a comment about where the time goes.** `_correspondences`
  still calls itself "the hot spot of the whole query"; it was, before the
  vectorisation the same comment describes. RANSAC is 88% of a verification
  now. Re-measure before optimising.
