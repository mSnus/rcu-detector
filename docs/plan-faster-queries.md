# Plan: making `/try` fast

Only the query path matters here. The catalogue build has no time budget and is
deliberately left alone — which is convenient, because it means almost
everything below can ship **without a re-extraction**.

Measured baseline on the deployment box (2 cores), `2749.jpg`, warm:

```
                       measured        share
reading the text        5079 ms         72%     stage 8
verifying candidates    1586 ms         22%     stages 13-16
finding the buttons      344 ms          5%     stage 5
everything else           88 ms          1%
                       --------
                        7097 ms   (the live endpoint reported 7891 ms)
```

## The sequencing rule

**Anything that changes what the pipeline reads or stores changes every
fingerprint**, and a catalogue built under one setting and queried under
another is the query/catalog asymmetry this project keeps rediscovering. That
costs a ~15 h re-extraction.

| step | changes | rebuild |
|---|---|---|
| 1. drop upside-down handling | query only | none |
| 2. cheap geometric prefilter | matching only | none |
| 3. adaptive RANSAC | matching only | none |
| 4. shortlist depth | matching only | none |
| 5. onnxruntime threads | neither, if output is identical | none |
| 6. OCR engine → OpenVINO | what is read | **both paths, full rebuild** |

Steps 1–5 are a day's work and no rebuild. Step 6 is the only one that costs a
catalogue.

## Rejected by measurement — do not re-propose

### Removing or enlarging the OCR bands

The hypothesis was that `_bands()` existed only to fit a small VM and could go
now the box has more memory. Swept at query width 800 over three remotes,
median of three runs each:

```
  <=600     10181 ms      40% slower
  <=900      8833 ms
  <=1400     7261 ms      current setting, best
  <=2000     9159 ms
  whole      8246 ms      14% slower, +150-170 MB peak, text differs
```

1400 is already the optimum: smaller and larger are both worse. Extra memory
buys nothing here, and removing bands would additionally change the text read,
so it would need a full re-extraction to buy a 14% *loss*.

One correction to record: the config comment claiming bands "keep detection
resolution high on long remotes" is **wrong at the current setting**.
`det_limit_type = "min"` means the detector only ever upscales, and our crops
are 800–1100px wide, so `ratio = 1.0` banded or whole. Bands neither cost nor
save resolution. The comment would be true under `limit_type = "max"`.

### Tesseract, or EAST + a small CRNN

Already answered in `RapidOcrOpenvinoEngine`'s docstring, measured on one
800x2346 crop on the same class of box:

```
  rapidocr-onnxruntime 1.3.24 (PP-OCRv3)   1438 ms, 29 regions
  rapidocr-onnxruntime 1.4.4  (PP-OCRv4)   2621 ms, 34 regions
  rapidocr-openvino    1.4.4  (PP-OCRv4)   1336 ms, 34 regions
```

The detector in use is already a lightweight DB net — EAST is older and
heavier, and would land where PP-OCR already is. Tesseract is last in
`engine_order` and is poor on small keycap legends. The real win in this family
is the runtime, not the architecture: see step 6.

## Step 1 — Stop handling upside-down photographs (`/try` only)

The single largest cheap win, because it is paid **twice**.

**In OCR.** When geometry is not confident which way up the remote is, the crop
is read *both* ways up and the words decide. On the timing sample that fired,
and it is why stage 8 measured 5079 ms rather than ~2500.

**In matching.** `query_is_ambiguous()` makes `verify_pair` try each candidate
both ways up. Measured cost, dev box, synthetic keypads:

```
buttons   verify_pair   both ways
   45        3.27 ms      7.51 ms      2.3x
   60        5.09 ms      9.97 ms
```

A phone user pointing a camera at a remote holds it roughly upright; the prior
is overwhelming, and the project already states it ("almost every photograph,
from a catalogue shoot or from a phone, is taken roughly upright"). Trading
that edge case for half of two stages is a good trade **on `/try`**.

Ship as a flag, not a deletion — the catalogue keeps indexing both
orientations, so records are unaffected and only the query stops paying.

**Risk, stated plainly:** a genuinely upside-down upload returns nothing rather
than the right answer. Measure how often that happens on real `/try` traffic
before making it the default for any other caller.

## Step 2 — Cheap geometric prefilter before RANSAC

The right target. Profiled per candidate on the dev box:

```
buttons   correspondences   RANSAC   verify_pair
   45          0.39 ms      2.89 ms     3.27 ms      RANSAC is 88%
   60          0.64 ms      3.99 ms     5.09 ms
```

Note this **contradicts the comment in `_correspondences`**, which calls itself
"the hot spot of the whole query". It was, before the numpy vectorisation that
same comment describes; nobody re-measured afterwards. RANSAC is the hot spot
now.

So: score each tier-1 candidate with something that costs microseconds and
needs no fitting, keep the top 10–20, and run RANSAC only on those. Candidate
features, all available in the stored fingerprint and all O(n):

* button-count ratio, and aspect ratio agreement (already computed as a fusion
  term — reuse it as a filter);
* coarse colour histogram distance;
* centroid-distance histogram — the distribution of pairwise button distances,
  quantised. Cheap, rotation-tolerant, and a strong signal of "same keypad".

Expected: 1102 ms → ~170 ms at 100 retrieved / 15 verified.

**Keep it soft.** Rank and truncate; do not hard-reject. A landmark-based
filter is tempting — power, volume ±, channel ± — and the *positions* of those
buttons genuinely are discriminative. But identifying which detection *is* the
power button is unreliable: many are grey rather than red, and the label comes
from OCR, which the project rule says may never be required. As a ranking
feature that costs a little accuracy; as a hard filter it costs the answer.

## Step 3 — Adaptive RANSAC termination

Currently a fixed count:

```python
iters = int(min(max_iters, max(32, n * 8)))     # max_iters = 400
```

At 50+ correspondences it always runs the full 400, however good the fit is —
and a correct match usually reaches consensus in the first handful of samples.

The implementation is vectorised (all iterations sampled at once), so this is
not a plain early-exit loop: run in blocks of ~32, and after each block apply
the standard adaptive bound from the best inlier ratio so far, stopping when
further sampling cannot plausibly improve on it.

**Two constraints.** It must stay deterministic — `ransac_seed` is fixed
precisely so inlier counts do not wander. And inlier counts feed the fused
score and therefore the confidence bands, so this must be validated for score
*stability*, not just for speed: same top-1 and same band on a sample of real
uploads, or the bands need recalibrating.

## Step 4 — Is 100 candidates ten times more than we need?

`CFG.index.top_n = 100`, at ~22 ms each on the deployment box. It was never
shown to be necessary: tier 1 is a deliberately weak bag-of-tokens overlap and
the depth exists to absorb its noise, which nobody has measured.

Largely subsumed by step 2 — a prefilter makes depth cheap, because retrieving
100 and verifying 15 costs what verifying 15 costs. Still worth measuring, to
know whether the shortlist itself is losing answers.

**Measure:** 539 `model_id`s hold more than one record. Filter to products
photographed *twice* — different photo stems **and** different image hashes —
and each pair is a real query with a known answer. Retrieve with one, record
the tier-1 rank of the other. Report the distribution, not the mean.

**Trap:** never measure this by self-retrieval. An identical fingerprint ranks
itself first and would "prove" any depth sufficient — the same shape of error
as keying truth on the filename stem, which made `medium` look like 78% when it
was nearer 95%.

**Related, and unmeasured:** `min_idf = 0.15` and `max_df_frac = 0.25` discard
near-universal tokens at query time. Both are documented as speed controls and
neither has a measurement behind it. If "red button, upper-left" is as common
as it sounds, these are discarding exactly the landmark signal step 2 wants.
Sweep both against recall@1; costs a reindex (~3 min), never a re-extraction.

## Step 5 — onnxruntime threads

`CFG.ocr.threads = 1`, with the comment that two are slower. Re-measured here,
bands ≤1400, query width 800, three crops:

```
  threads=1    6888 ms
  threads=2    6342 ms      8% faster
```

Small but free. **Verify the text read is byte-identical between the two before
adopting** — thread count can change floating-point reduction order, and if the
output moves at all this stops being free and becomes step 6.

## Step 6 — OpenVINO, the only change worth a rebuild

Roughly 2x on identical models, and PP-OCRv4 under OpenVINO beats PP-OCRv3
under onnxruntime on *both* time and regions found. It is also the only one of
the three that gets faster with a second thread.

`rapidocr_openvino` is **not currently installed** on the dev box —
`available_engines()` returns `['rapidocr']` alone, so the `engine_order` entry
for it is aspirational. Install first, re-measure on this hardware, then decide.

This changes what the pipeline reads — 34 regions against 29 is more labels,
more tokens, possibly a different brand or model code — so it must be adopted
on **both paths at once with a full rebuild behind it**. Worth scheduling
alongside any other change that needs a rebuild rather than on its own.

## Expected result

```
                     now      after 1      after 1-4    after 6
text                5079 ms    2540 ms       2540 ms     ~1270 ms
verification        1586 ms     690 ms        ~150 ms      ~150 ms
buttons + rest       432 ms     432 ms         432 ms       432 ms
                    -------    -------       -------     -------
                    7097 ms    3662 ms       3122 ms     ~1852 ms
```

Steps 1–4 more than halve `/try` with no rebuild and no new dependency. Step 6
takes it under two seconds and is the only one that costs a catalogue.

## What to validate, every step

`query_drift.py`, which uploads every catalogue photograph to the running
service, and identity recall against `eval_truth.tsv`. **Not `match_eval.py`**:
it reads stored fingerprints, never uploads, and stayed at 8/8 through two
query-path defects that made a record unretrievable.
