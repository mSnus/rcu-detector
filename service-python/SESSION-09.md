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

### Still to do: apply the image dedupe. `rcu:legacy-manifest` now collapses
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

## Next

1. **Land the inherited work above.** Nothing else should start first.
2. **Enable `RCU_ASSUME_UPRIGHT` on `rcud` and re-measure `/try` end to end**,
   with `query_drift.py` against `eval_truth.tsv` — not `match_eval.py`, which
   never uploads and has stayed at 8/8 through two query-path defects. Then
   measure how often a real upload is genuinely upside down; that is the
   number the flag trades against.
3. **Re-measure `verify_top_m` on the live catalogue.** 25 is set from eight
   pairs over 21 records — a direction, not a calibration. Tier 1 ranked the
   answer first six times of eight, sixth once, and sixteenth once
   (`MR-18B_0_0`, whose partner extracts 4 buttons against 22). The floor is
   set by the worst extraction in the catalogue, not the median.
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
