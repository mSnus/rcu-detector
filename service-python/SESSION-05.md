# Session 05 — Laravel wired end to end, and the record that could never be queried

Session 4's list put Laravel first. That is done: uploads, catalog DB, admin
UI and the service client are working against the live service, with 48 tests
passing including five that make real calls.

Doing it surfaced one genuine defect — a catalog record that extracted
perfectly and could never be matched — plus a smaller one that disguised it.

## Run it

The box gained 1 GB this session (3.9 GB total). **`--ocr-width 800` is no
longer needed**, so the build is finally the plain loop it always should have
been:

```bash
cd service-python && source .venv/bin/activate
for f in ../photos/*.jpg; do python scripts/extract_one.py "$f" --out ../work; done
python scripts/build_index.py --fp ../work/fp --out ../work/index/tokens.npz
python scripts/match_eval.py  --fp ../work/fp --index ../work/index/tokens.npz

cd ../backend-laravel
php artisan rcu:import-catalog --prune --reindex
php artisan test
```

## The memory ceiling is gone

Sessions 3 and 4 were shaped by ~540 MB of headroom: five of eighteen images
had to run at `--ocr-width 800`, and the service had to be stopped before any
rebuild. Measured this session with the service **running** (393 MB RSS):

| | session 4 | session 5 |
|---|---|---|
| total RAM | 2.9 GB | 3.9 GB |
| available with service up | ~194 MB | ~1.6 GB |
| 5.3 MP image at default OCR width | OOM-killed | peak 696 MB, exit 0 |
| full 18-image rebuild, service up | not attempted | 18/18, zero kills |

### What the 800px compromise actually cost: almost nothing

Rebuilt the whole catalog at default width and compared against the 800px
fingerprints (kept at `work/fp.bak-ocr800/`):

| | ocr-width 800 | default (1100) |
|---|---|---|
| total buttons | 356 | 360 |
| labelled buttons | 234 | 237 |
| brand | 11/21 | 11/21 |
| model code | 7/21 | 7/21 |
| recall@1 | 8/8 | 8/8 |
| separation | +0.077 | +0.077 |

Only two records moved at all: `RM-L859-1_0` 2 -> 4 buttons (already known-BAD)
and `RM-PJ20_big_light_0` 16 -> 18. The Sony true pair gained an inlier
(0.807 -> 0.813). So the workaround sessions 3 and 4 worried about was costing
essentially nothing measurable — worth knowing before spending effort on a
problem that was not one.

The full-width build is now promoted to `work/`, mostly because it removes the
"run these five specially" step from the build procedure, not because it
scores better.

## The real defect: a truncated JPEG is unqueryable

`RM-PJ20_big_light.jpg` has no final `FF D9` end-of-image marker. It is 1 of
the 18 sample photographs.

- `cv2.imread` (catalog build, from a path) reads it fine. The record
  extracted at quality 0.92 and indexed normally.
- `cv2.imdecode` (service, from the upload buffer) returns None on the *same
  bytes*. `/identify` answered `400 undecodable image`.

So a remote sitting in the catalog could never be matched, and nothing in the
offline pipeline could reveal it: leave-one-out eval reads fingerprints, never
uploads. Truncated files are ordinary in a scraped catalog, so this scales.

Fixed in `_read_image`: when `imdecode` returns None, fall back to writing the
bytes to a temp file and calling `cv2.imread` — the same call the catalog
build makes, so the two paths cannot disagree on any input. Verified
pixel-identical on the truncated sample. Appending `FF D9` also works and is
cheaper, but only for JPEG; it would do nothing for a truncated PNG. The
fallback is only ever reached after a failure, so healthy uploads pay nothing.

Genuinely undecodable bytes still get a clean 400.

> **Superseded — see "Correction: the decode bug was worse than this" below.**
> The diagnosis above is right as far as it goes, and the fix worked for the
> record it was aimed at. But it treats *total decode failure* as the problem,
> and total failure was only the visible tip. The temp-file fallback has been
> replaced by `app/pipeline/imageio.py`.

**A note on what this record does now.** It identifies, but at
`confidence=none` — the query path extracts 20 buttons where the catalog
stored 18, and its own record is not the top candidate (`Sony_RM-PJ20_big_0`,
the other photo of the same remote, is, at 0.275). That is the known
query/catalog path asymmetry, now visible on this record for the first time
because it could never be queried before. Not chased this session.

### The bug that hid it

`IdentifyController` mapped every `RcuServiceException` to `503
recognition_unavailable`. A 400 is a verdict on the image and will be
identical next time; reporting it as an outage tells the user to retry
something that cannot succeed, and it made a service defect look like
downtime. 4xx now returns `422 image_rejected`; 5xx and connection failures
still return 503.

## Broken images: log, skip, and *count*

`extract_one.py` already logged and skipped unreadable images, and already
wrapped each image in try/except so one bad file cannot take a batch down.
What it did not do was **count** them: `process_image` returned `[]` and the
summary said "18 image(s) -> 21 remote(s) extracted" with no mention of
anything dropped. At 10k-50k images a silent skip is a record quietly missing
from the catalog that nothing downstream ever reports.

Now `process_image` returns `None` for unreadable and `[]` for readable-but-no-
remote-found — different problems, counted separately — and the run ends with

```
4 image(s) -> 2 remote(s) extracted, 2 skipped
  skipped: 2 unreadable
  -> <out>/skipped.txt
```

`skipped.txt` is appended per run, because console scrollback is gone long
before anyone reads it on a big build.

## Laravel

Everything below existed as scaffolding before this session; the catalog half
was not wired to anything.

**New: `php artisan rcu:import-catalog`** loads `work/fp/*.json` into
`rcu_fingerprints`. Upserts on `record_id`, so a rebuild updates in place.
`--prune`, `--reindex`, `--dry-run`.

Two things it gets right that are easy to get wrong:

- **`reviewed` and `model_id` are preserved across a rebuild.** They are the
  only two columns a person owns rather than the extractor. Overwriting them
  empties the review queue and unlinks the catalog joins, losing work that
  cannot be recomputed.
- **The source image is resolved by stripping exactly one trailing `_<int>`,
  never by testing whether `<record_id>.jpg` exists.** This catalog contains
  both `ROLSEN_RSF-3106RT.jpg` and `ROLSEN_RSF-3106RT_0.jpg`, two different
  remotes, whose records are `ROLSEN_RSF-3106RT_0` and
  `ROLSEN_RSF-3106RT_0_0`. The existence test resolves the first to the
  second's photograph, silently pointing two records at one image and
  orphaning another. There is a test for exactly this.

An empty fingerprint directory is a hard failure, so `--prune` can never be
the thing that empties the catalog: it reads as "the extraction run has not
happened yet", which is far more often true.

**Candidates now resolve to catalog rows.** `/identify` returns a `catalog`
object per candidate (source image, button count, quality, review state,
`model_id`) plus the `orientation` block the presenter was dropping. A
record_id that resolves to nothing is reported as `catalog: null` rather than
dropped or faked — it means the index and the table came from different
extraction runs, which is worth surfacing, and the import command warns about
that skew directly.

**Admin catalog browser** at `/admin/rcu/catalog`: ordered worst-extraction-
first because that is the review queue, filters for `needs review` / `no
brand` / `orientation unresolved`, search over record, brand and model code.
Per-record page shows the rectified crop, the build overlay and the raw
fingerprint, with a reviewed toggle.

Routing trap, since it will bite again: the literal `catalog` segment **must**
be registered above the unconstrained `{requestId}` routes, which otherwise
match the string "catalog" and swallow the whole section.

## Where it stands

| | |
|---|---|
| catalog records | 21 (table and index in sync) |
| buttons | 360 |
| brand / model code | 11/21, 7/21 |
| recall@1 | 8/8 |
| separation | +0.077 |
| flagged for review | 12/21 by audit, 4 by quality < 0.75 |
| Laravel tests | 48 passing, 5 of them against the live service |

## Correction: the decode bug was worse than this

Continuing into item 2 (query-vs-catalog drift) turned up the rest of it. The
truncated-JPEG problem is not "imdecode is stricter than imread". It is:

**A JPEG with no `FF D9` marker decodes into a partly uninitialised buffer.**
libjpeg writes the scanlines that are present and leaves the remainder as it
found it, so what lands in the missing region is whatever was in that heap
allocation. No error is reported anywhere.

That makes decoding **nondeterministic**. Measured on this sample: two decodes
of one file differing on up to 18% of pixels, intermittently — sometimes
identical, sometimes not, depending on what the allocator hands back.

Consequences, all of which had been read as matching problems:

- The build path (`cv2.imread`) and the service path (`cv2.imdecode`) saw
  different images for the same file.
- `URC-177500_Wink` extracted **15 buttons upright** at one decode and **12
  flipped** at another. Queried, it returned `GINZZU_GM-501_0` at confidence
  `none` — a remote in the catalog, misidentified as a different brand.
- A fingerprint built from such a file partly encodes heap garbage, so
  rebuilding the catalog from unchanged inputs could produce different
  records.

4 of the 18 sample photographs are genuinely truncated. Three more lack the
marker because they are **PNG files with a `.jpg` extension** (`89 50 4E 47`,
ending in `IEND`) — complete and fine, and correctly left alone.

### The fix

`app/pipeline/imageio.py` is now the only place bytes become pixels.
`decode_image` appends the missing `FF D9` before decoding, which makes
libjpeg terminate at the last complete MCU row and fill the rest
deterministically; `read_image` routes the build through the identical call.
`cv2.imread`/`cv2.imdecode` appear nowhere else.

Verified: all 4 truncated samples decode identically across 10 reads with the
heap churned between them, build and service agree on 18/18 images, and the
file that previously failed outright now decodes. `scripts/check_decode.py`
asserts both properties and exits non-zero on violation.

### What it was costing

`scripts/query_drift.py` (new) uploads every catalog photograph to the running
service. This had never been measured: every quality number the project
reports comes from `match_eval.py`, which reads stored fingerprints and never
uploads an image, so **the query path was entirely unmeasured**.

| | before | after |
|---|---|---|
| identity recall@1 (right physical remote) | 16/17 | **17/17** |
| self-retrieval recall@1 | 14/18 | 16/18 |
| high-confidence answers | 10/18 | 11/18 |

`URC-177500_Wink` went from a `none`-confidence misidentification to a
**0.995 high-confidence hit**. Offline metrics did not move at all — recall@1
8/8, separation +0.077 — which is exactly why nothing caught this: they are
self-consistent by construction.

The two remaining self-retrieval misses are not misses in product terms.
`RM-PJ20_big_light` and `ROLSEN_RSF-3106RT_0` each return the *other*
photograph of the same physical remote, which is the right answer;
`query_drift.py` scores identity against `eval_truth.tsv` for that reason and
reports both numbers.

## A query's orientation confidence was never worth trusting

Item 3 below said the `fast_ocr` comment was wrong. Chasing it found a second
real defect, on the same measurement.

`query_is_ambiguous` applied the **index side's** confidence threshold
(`orientation_trust_conf = 0.6`) to the query, so a query claiming confidence
1.00 was tried one way up only — in both retrieval and verification, which
share the flag.

Those two confidences are not the same quantity:

- a **catalog** record's orientation is resolved from text, OCR'd at full
  upscale both ways up;
- a **query's** is resolved by `fast_ocr`, which drops the text signal
  entirely, leaving geometry — and geometry is confidently wrong often enough
  to matter.

`RM-PJ20_big_light` reads flipped at conf 1.00 against a record stored upright
at conf 1.00. Both "trusted", opposite, so neither was ever tried the other
way up and the record was **never retrieved at all**.

Forcing the flip, measured directly against the index:

| query | as extracted | forced the other way up |
|---|---|---|
| RM-PJ20_big_light | own record not retrieved, `none`, 0.275 on a different remote | **rank 1, 0.737** |
| Huayu_Motorola_Cisco_15 | 0.488, `low` | **0.901, `high`** |
| AIWA_PAS-Y600 | 0.799 `high` | identical — conf 0.32 < 0.6, so already tried both |

`CFG.index.trust_query_orientation = False` now makes every query try both
ways up. Unlike `index_both_orientations` this costs **no memory** — one extra
token retrieval and one extra verify per candidate, at query time only — which
is exactly why the index side is allowed to be selective and the query side is
not.

Offline metrics were checked for a false-pair regression, since trying both
ways lifts everything: median false pair rose 0.212 -> 0.225 but the maximum
held at 0.432, so recall@1 8/8 and separation +0.077 are unchanged.

### Query path, across the whole session

| | start | after decode fix | after orientation fix |
|---|---|---|---|
| self-retrieval recall@1 | 14/18 | 16/18 | **17/18** |
| recall@5 | 15/18 | 17/18 | **18/18** |
| identity recall@1 | 16/17 | 17/17 | **17/17** |
| high confidence | 10/18 | 11/18 | **14/18** |
| `none` confidence | 4 | 3 | **1** |
| median latency | 2908 ms | 3131 ms | 2814 ms |

The one remaining `none` is `RM-L859-1`, the known-BAD packaging-label record.
The one remaining self-retrieval miss is `ROLSEN_RSF-3106RT_0`, which returns
the *other* photograph of the same remote at 1.000 — the two are the same
object and the true pair scores exactly 1.000, so the order between them is
arbitrary. Identity recall is 17/17.

Latency did not move: the extra retrieval is not measurable against OCR.

## Known-bad, for session 6

- **Separation is still governed by `MR-18B_0_1`**, unchanged from session 4.
  The true pair scores 0.510 with inl=0 because one side has 4 buttons. Only
  low-contrast keycap detection moves it.
- **The query path and the catalog path still extract differently** — now with
  a fresh example in `RM-PJ20_big_light_0` (20 buttons queried, 18 stored,
  self-match not top). Worth measuring across all 21 records rather than
  anecdotally.
- Unchanged: adjacent remotes bridged by packaging still merge; colour
  bucketing untuned against phone photos; confidence bands uncalibrated on 21
  records.
- The service still plateaus at ~370-390 MB RSS. No longer a constraint at
  3.9 GB, but it is a per-instance cost worth knowing when sizing.

## Next session

Items 2 and 3 of the original list were done in this session — see the two
corrections above. Revised:

1. **Low-contrast keycap detection (plan 9.1)** — a trained detector. The only
   thing that moves `MR-18B_0_1` off 4 buttons and separation off +0.077, and
   now the only substantial CV item left.
2. **Button drift between query and catalog is still 7/18 records**, mean
   absolute 3.4 buttons, unchanged by either fix. The cause is `fast_ocr`:
   fewer text regions read means less `suppress_text_detections`, so the query
   keeps detections the build removed — `AIWA_PAS-Y600` queries 18 against 15
   stored. It is **not costing recall** (that photo hits at 0.799 high), so it
   is a known asymmetry rather than a defect. Worth revisiting only if a
   record is found where it does cost something.
3. Re-validate the confidence bands once the catalog is real. Now more urgent
   than it looks: the bands moved a lot this session without being touched
   (`none` 4 -> 1, `high` 10 -> 14) purely because scores rose, which is a
   reminder that they are calibrated against nothing.
4. **`work/fp.bak-*` is up to four stale copies of the catalog** (precls,
   preflip, ocr800, predecode). Useful during a session, clutter across
   them; keep the two that document a real behaviour change and drop the rest.
