# Session 08 — a catalogue that exists, and what it is not yet good at

Session 7 ended with the thing every previous session was waiting for: a
complete catalogue, extracted end to end, with both consumers in step and the
confidence bands measured against it.

    12311 fingerprints = 12311 catalog rows      in step
    12515 index docs
    recall@1 241/254 (95%) over live uploads
    high   n=195  precision 100%
    medium n= 59  precision  78%

The service holds 108 MB idle instead of 680, `/reindex` no longer takes it out
for ten seconds, and `high` sits at 0.65/0.10 where 254 real queries put it.

That is the floor this session starts from. Everything below is what the
catalogue is still bad at.

## Where the remaining errors actually are

Session 7 fixed nine defects in extraction and every one was found the same
way: opening a record in the review queue and looking at the overlay. That
route is now much less productive -- the obvious failures are gone -- so this
session should change instrument.

Three sources of evidence, in the order they are worth using:

1. **`low` and `none` are uncalibrated and cannot be calibrated by
   self-retrieval.** Every query in the session 7 run was a catalogue
   photograph, so the right answer always existed and nothing scored below
   `medium`: all 13 wrong answers were above 0.50 and no floor from 0.10 to
   0.50 rejected any of them. The band that tells a user "not in the
   catalogue" is therefore untested. It needs queries for remotes that are
   genuinely absent -- one is already known, a `/try` upload of a Supra
   STV-LC1504 scoring 0.42 -- and `rcu_queries` now records failures as
   failures, so real uploads can supply the rest.

2. **`medium` is 78% and nothing is known about why.** 13 wrong answers out of
   59 is a population, not an anecdote, and `work/bands.csv` holds every one
   with its terms. Reading those 13 is the cheapest available lead on what the
   matcher gets wrong when it is not sure.

3. **Plan 9.1, still.** `KGH-14`'s arrows, play, home and burger are printed
   flat with no keycap at all, and no classical operator distinguishes them
   from printed letters -- an edge pass was measured and does not help
   (895 -> 910 detections over 23 records, **-1** on `KGH-14` itself). The
   pseudo-label export and the Label Studio round trip are built and verified;
   what is missing is an afternoon of hand-correction.

## Multi-crop photographs (done)

`HTR-U29A_1` was a 0.195-area strip carrying 3 buttons beside the 19-button
remote it was cut from, and `Sherwood_TX-757` yielded four crops of which three
were slivers at area 0.003-0.012 holding *more* buttons than the real remote —
rectification upscales any body to 400px, so a 0.3% fragment is enlarged ~20x
and `detect_buttons` traces the interpolation noise. The same artefact as the
imagecache thumbnails, arriving from a different direction.

The tempting fix, keep only the leftmost body, was measured and rejected: it
costs 816 records to remove 28 (gotcha in CLAUDE.md). What shipped is a
sibling-relative button floor plus the split-path area floor. 47 photographs
re-extracted, **213 crops -> 124, none left empty**, catalogue 12311 -> 12218,
both consumers resynced and in step. `Sherwood_TX-757` now yields exactly the
two real remotes and self-retrieves at 0.9168 against a next-best 0.4135;
`HTR-U29A` at 0.9235 against 0.6466.

## The 13 wrong `medium` answers: mostly not wrong

Item 2 below is answered. Reading them, 10 of the 13 are the same remote
photographed twice under a second filename:

    RS41C0_0        -> RS41C0_1_0          margin 0.0
    19SECAP-org_0   -> 19SECAP-org_1_0     margin 0.0
    olto_Y-72C3     -> olto_Y-72C3_1_0     margin 0.0
    M39Q77FDLcopy_0 -> M39Q77FDLcopy_0     margin 0.0   <- identical, scored wrong

`calibrate_bands.py` keys truth on the filename stem, so a correct answer under
a second filename counts as a miss. Only `WRTECH_WR330 -> MAG-245`,
`ELEKTA_RC02-51 -> Konka_H-1482` and possibly `2590 -> 2589_0` are real errors,
which puts `medium` nearer 95% than 78%. **Every one has margin <= 0.0034**, so
the tie rule added since routes all of them to `low` regardless. Fix the truth
function to key on `model_id` before quoting a `medium` precision again.

## Carried forward

1. **Calibrate `low` and `none`** from real `/try` uploads of absent remotes.
   The mechanism is the "None of these" button, which writes
   `rcu_queries.none_of_these`. It is visible again on `rcud` —
   `RCU_TRY_SIMPLE=false`, and the container was recreated so the flag actually
   took.
2. ~~Read the 13 wrong `medium` answers~~ — done, above. What remains is keying
   `calibrate_bands.py` truth on `model_id` rather than the filename stem.
3. **Plan 9.1 step 2** — hand-correct ~400 crops, then train. Draw the queue
   from the `rcud` catalogue, not the 21-record dev sample.
4. Button drift between query and catalog: unchanged since session 5, measured
   as not costing recall.

## Do not repeat

* **Do not run anything heavy on `rcud` while a build or a calibration is
  running.** Two catalogue builds were OOM-killed by measurements running
  alongside them, and a calibration was killed by the kernel because it had
  loaded its own copy of the fingerprints. The box has two cores and 3.9 GB.
* **Do not judge an extraction change by `extract_quality`.** It is computed
  per crop, and a fragment can score well: `RMT-V141K` scored 0.851 across
  seven crops holding 728 buttons between them. Count buttons and crops.
* **Do not believe a retrieval metric before looking at the score
  distribution.** Session 6's calibration was 100% precise in every band and
  entirely fictional; session 7's is 232 distinct scores out of 254.
