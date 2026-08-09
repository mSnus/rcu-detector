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

## Carried forward

1. **Calibrate `low` and `none`** from real `/try` uploads of absent remotes.
2. **Read the 13 wrong `medium` answers** in `work/bands.csv`.
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
