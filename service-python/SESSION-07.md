# Session 07 — the full catalogue, and the labelling gate

Session 6 established that the confidence bands could not be calibrated on the
dev sample, because the corpus was three quarters imagecache thumbnails and 19
usable records contain no wrong answers to calibrate against. This session went
after the corpus itself: the whole live catalogue, extracted on `rcud`.

**That build is still running as this is written** — 5416 of 13763 images at
13:26 on 2026-08-05, 39.4%, started 01:54. Everything below the build section
is finished work; the calibration it exists for is not, and item 1 of the
carried-forward list is still exactly where session 5 left it.

## Making a 13763-image build finish this week

The rule since session 3 was one image per process, adopted when a
whole-directory run was OOM-killed at ~540 MB free. It was never measured
against the alternative, and it was expensive: loading the OCR model costs more
than extracting a catalogue-sized image, so the build was paying its largest
per-image cost for the model rather than for the image.

Measured on `rcud` over the same 60 photographs, byte-identical output either
way:

| | wall | per image | CPU/image |
|---|---|---|---|
| one process per image, `--jobs 2` | 11m30s | 11.5 s | ~23 s |
| one process, 60 images serially | 8m05s | 8.1 s | 8.1 s |

Across 13763 images that is roughly 44 hours against roughly 15.

The batch is **bounded** (`build-catalog.sh --batch N`, default 200) rather
than unlimited, because the other half of the old rule is still true: OCR runs
twice per body on an upscaled crop, peak RSS is ~700 MB on a 5 MP image, and
the run that was OOM-killed was the whole directory in one process. Letting
each process exit after N images amortises the model load without betting on
the growth being absent. Measured at 580–800 MB across a 60-image batch with no
upward drift.

Also measured and worth recording as a negative result: `--jobs 2` buys nothing
on this two-core box, because the OCR already uses both cores. Unpinned it ran
~10.9 s/image; with OMP/MKL threads pinned to 1, 8.5; `--jobs 1` batched, 8.1.
Parallelism at the process level was competing with parallelism inside the
model.

## The size floor reaches the query path

Session 6 taught the build to refuse a source whose long side is under 600 px,
after a 16x50 thumbnail extracted 29 confident buttons and self-matched at
0.925. That fix stopped at the build. The query path still accepted the same
image and answered it, for the same reason and with the same false confidence —
rectification upscales every body to 400 px whatever the source was, so
detection traces interpolation artefacts.

Session 6 left this deliberately, on the grounds that it changes an API verdict
and a phone photograph is never that small. It is fixed now, because `/try`
made the query path reachable from a phone by anyone who can load the page, and
"a phone photograph is never that small" is a statement about the client, which
is exactly the kind of thing a service must not assume.

**400, not 500.** It is a verdict on the image and identical next time; Laravel
already maps 4xx to `422 image_rejected` rather than to an outage, per the
session 5 gotcha about disguising verdicts as outages. The rejection carries
the service's own `detail` through to the caller, because "could not read that
image" is wrong for an image the service read perfectly well and refused on its
size, and that difference is the only thing that tells the user what to change.
Only the stated `detail` is passed on — the fallback is the raw response body,
which on a proxy error is not an explanation to repeat back to anyone.

This closes carried-forward item 3. It was the third instance of the same
family: a rule enforced on one of the two paths through the same code.

## Plan 9.1 step 1, and why step 2 cannot be skipped

`scripts/export_button_dataset.py` writes the classical detector's output as a
YOLO dataset. The conversion is a copy — `detect_buttons` already stores boxes
as `(x + w/2)/W`, which is YOLO's format — and the images exported are
`work/norm`, the rectified crops, not the source photographs. Training on
frames the pipeline never sees would teach the model a preprocessing step that
does not exist: the same mistake as calibrating a watermark rule on full images
when OCR sees the crop.

The tempting shortcut is to skip the hand-correction: train only on the records
the classical detector handles well, and let the network generalise to the ones
it handles badly. **It was tried and it does not work.** Rendering the exported
labels back onto their crops shows high-quality extractions are still
systematically incomplete — `CAS-400_0` at quality 0.91 has its entire lower
numeric keypad unboxed, grey keys on a grey panel, and `1CE3-copy_0` at 0.92
misses the D-pad ring. In YOLO an unlabelled keycap is not neutral, it is a
negative, so those files would train the model that low-contrast keys are
background. That is the precise opposite of the reason for training a detector
at all.

Two automatic completeness tests were tried, so that the correction queue could
be ordered by something. Both fail on that same record:

* **orphan text** — OCR regions not inside any box. `CAS-400_0` scores 0.25,
  because OCR missed the grey legends too. The signal shares a cause with the
  failure it would have to detect, so it is blind in exactly the place it
  matters.
* **interior coverage gap** — the longest empty horizontal band between
  occupied ones. `CAS-400_0` scores 0.00, because its missing cluster is at the
  bottom of the crop, and a trailing gap is indistinguishable from the body's
  margin.

So nothing in a fingerprint says whether the detector found everything, which
is why the queue (`--split hard`) is **sampled evenly across the quality
range** rather than taken from the bottom of it. Incompleteness does not lower
the quality score; a worst-first queue would never show a labeller the CAS-400
case, which looks fine by every number available.

Step 2 is a person with a mouse. That is now the gate on the only substantial
CV item left.

## The admin visualiser had been returning 500 in production

`/admin/rcu` has declared `auth` middleware since session 5, and no route named
`login` existed for that middleware to redirect an unauthenticated request to.
Every request to the review queue, the catalog browser and the overlays raised
`RouteNotFoundException` and returned 500 — on a public host, since the day it
was deployed. Session 6 recorded the missing login route as a fact about `/try`
and did not follow it to what it meant for the pages that already shipped.

Hand-rolled rather than Breeze: the users table and model come with the
skeleton, this application has exactly one class of human user, and
registration, password reset, email verification and a build toolchain are all
surface area on a box that stores user photographs. Four details are load-
bearing rather than decorative, and each is pinned by a test — the route is
*named* `login` because that name is the contract with the middleware; the POST
is throttled because a login form on a public host is a password oracle; the
session id is regenerated on success because the pre-login id is
attacker-supplied; and a wrong password and an unknown account return the same
message, because distinguishing them tells an attacker which half they already
have.

`rcu:make-user` creates or re-passwords the operator account and generates the
password unless given one. A command that quietly creates an account with a
known password on a public host is worse than one that refuses to run.

That fix immediately exposed a second one. TLS is terminated by the host's
FastPanel nginx, which proxies to the compose nginx on loopback; Laravel was
not trusting the forwarded headers, so it built absolute URLs with the scheme
it believed it was serving, and a guest opening
`https://rcud.pultovnet.ru/admin/rcu` was redirected to `http://…/login`. A
login form, downgraded to plaintext, with a session cookie that then had no
reason to be marked secure. Trusting every proxy is correct *here* precisely
because the application is not reachable except through one; the comment in
`TrustProxies` says what has to change if it is ever published directly,
because a trusted `X-Forwarded-For` from an arbitrary client is a spoofed
client address.

87 tests pass, up from 71.

## The full catalogue build

Running on `rcud` in a `rcud-extract-run-*` compose container since 2026-08-05
01:54 (+0200):

```
build-catalog --manifest /data/work/primary.txt --jobs 1 --batch 200
```

Output lands in `/var/www/pult3_ru_usr/data/www/rcud/work` on the host — `fp/`,
`norm/`, `debug/`, `skipped.txt`, `build.log`. Note that is `rcud/work`, not
the `rcud_data/` directory beside it, which holds an older sample.

Read progress by matching the newest `fp/*.json` stem back to its line in
`primary.txt`, which is alphabetical — **not** by counting fingerprints: they
run 1.26 per manifest line here, 1.46 per image that actually extracted.

At the 11h32m mark:

| | |
|---|---|
| images processed | 5416 of 13763 (39.4%) |
| rate | 7.7 s/image |
| fingerprints | 6817 |
| skipped | 732 — 716 `too small`, 12 `no remote found`, 4 `no features` |
| buttons/record (800 sample) | mean 23.2, median 21, 7 with zero |
| memory available | 1.3 GB |
| disk | 26% of 99 G |

Two of those numbers are the ones session 6 said to watch. `too small` is
**13% of the images processed** (716 of 5416) and 98% of all skips, which says
the manifest is resolving mostly to usable derivatives: on the dev sample that
session 6 tried to calibrate against, 72 of 91 queries — 79% — were thumbnails.
And the button counts spread across 10–33 with no value taken more than 33
times in a sample of 800; the pile-up on a single count that gave the
thumbnails away last session is absent.

ETA is around 07:00 on 2026-08-06.

**When it lands**, resync both consumers before believing any retrieval number:
`build_index.py`, then `php artisan rcu:import-catalog --legacy --prune
--reindex`. A token index and an `rcu_fingerprints` table from different
extraction runs still "work" and return `record_id`s that resolve to no row.

## Two things built while it runs

**`docker/resync-catalog.sh`.** The index and the `rcu_fingerprints` table have
to come from the same extraction, and doing that by hand in the right order at
07:00 after a 30-hour build is how the order gets got wrong. The script ends by
asserting the two counts agree and exits non-zero when they do not. It refuses
to start while an extraction is still writing, found by container name because
`compose --profile build run` does not appear as a service in `compose ps`.

`calibrate_bands.py` gained `--sample`, since 13763 uploads is hours. Sampled
uniformly over the manifest and never truncated to the first N — the manifest
is alphabetical, so a prefix is one corner of the brand distribution — and the
sample is stated in the output, because every precision under it is then an
estimate and the next reader will not have the command line.

Three fixes fell out of verifying it on the dev stack, which is the argument
for verifying it on the dev stack: `tinker` exits 1 on an unwritable home so
the row count came back empty and read as a database failure; the extract image
predated `calibrate_bands.py` and `run` reported a missing file with no hint
why; and "no queries succeeded; is the service running?" was wrong — all three
sampled photographs were imagecache thumbnails and the service had refused
every one on its size. A 4xx verdict and a dead service now read differently.

That last run is the first live confirmation of the query-path size floor: 15
of 20 legacy dev images came back `400 image too small`, at 11–44 px wide.

**The labelling loop.** `label_queue.py` ships the hard queue out to Label
Studio with the classical boxes attached as *predictions* and brings the
corrections back as YOLO labels; `label_studio.md` is the operator's page;
`check_label_roundtrip.py` asserts a box survives the trip.

The conversion is where this fails silently. A fingerprint stores a button as
centre x/y in fractions of the crop, Label Studio as top-left x/y in
percentages, and confusing them offsets every box by half its own size — which
on a dense keypad still looks like a plausible set of buttons, and would
surface after an afternoon of labelling as a detector that finds keys half a
key up and to the left. 256 boxes round-trip exactly, bar four clamped at the
crop edge; breaking the conversion on purpose moves interior boxes by up to
1.4e-1 and the check fails, so the check tests something.

The importer will not write an empty label file for a task nobody opened. Empty
is not "unknown" to YOLO, it is "this crop is entirely background". A task
submitted with no boxes is a different thing — someone asserted it — and is
kept as a background sample.

Run against a real Label Studio 1.23.0 rather than against a model of one,
which is how both of its traps were found: local file serving 404s every image
until a Local Storage is registered, while the tasks import perfectly and the
project looks healthy; and that storage may not be the document root, so it has
to point at `images/hard`, which is not a path anyone would guess. A hand-drawn
box at 10%/70% came back as `0.140000 0.715000`.

## Serving the build before it finishes

`resync-catalog.sh --snapshot` copies `fp/` and points everything downstream at
the copy, including `rcu:import-catalog --fp`, which otherwise reads
`RCU_FP_DIR` and would import records the index does not contain. Without the
copy the index is built at one moment and the table imported at another, and
the two disagree by whatever landed in between — the same drift the count check
exists to catch, arriving by the one route it cannot see, because both counts
move. The copy is also where a half-written fingerprint is caught: parsed, and
the unparseable left for the next snapshot.

Run on `rcud` at 14:35, mid-build: **7451 records live**, index 8095 docs, both
consumers in step. `/try` is enabled there and has no authentication — the
trade-off was put explicitly and that was the choice.

A catalogue photograph posted to the public `/api/identify` self-retrieved at
0.9118, `high`, with catalogue metadata resolved. Two things that measurement
turned up:

* **Latency is 8.2 s against a ~1 s budget.** The extraction is holding 196% of
  the box's two cores, so this is contention and not a query-path regression.
  It is also the first evidence about how the service behaves on a real index,
  and it must be re-measured once the build is done rather than filed as known.
* The field is `photo`, not `image`; the API says so plainly, which is the
  behaviour session 5 asked for.

The import's index-skew warning fired on a run that had done nothing wrong: it
asked the service for a record count it was about to replace, so it printed
`index holds 61 record(s), catalog now holds 7451. Rebuild the index` directly
above `service reindexed: 8095 docs`. Reordered. Harmless at 21 records, an
invitation to redo a 30-hour build at 7451.

Also visible for the first time at scale: **55 filenames name more than one
product** and cannot be keyed, and 20 fingerprints have no product row. Both
are counted at the point of exclusion, which is the rule session 6 established;
neither has been looked into.

## Carried forward

1. **Low-contrast keycap detection (plan 9.1)** — steps 1 and 2's *tooling* are
   done and verified end to end. What is left is an afternoon of a person
   correcting boxes, then training. Still the only substantial CV item, and
   still the only thing that moves `MR-18B_0_1` off 4 buttons and separation
   off +0.077. Draw the queue from the `rcud` catalogue, not from the 21-record
   dev sample.
2. **Calibrate the bands**, now that a catalogue with confusable neighbours is
   nearly extracted. `./docker/resync-catalog.sh --calibrate` is the whole of
   it; the corpus arrives tomorrow morning.
3. Button drift between query and catalog: unchanged since session 5, measured
   as not costing recall.
4. `work/fp.bak-preflip` is kept deliberately (it documents the CLAHE rotation
   fix, 5 of 21 records differ). No other stale copies remain.
