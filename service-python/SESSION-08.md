# Session 08 — a catalogue that exists, and what it is not yet good at

Session 7 ended with the thing every previous session was waiting for: a
complete catalogue, extracted end to end, with both consumers in step and the
confidence bands measured against it.

    12311 fingerprints = 12311 catalog rows      in step
    12515 index docs
    (12079 / 12079 / 12211 after this session's two crop-selection fixes)
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

## Photographs that contain something other than a remote (done)

`2750` is a remote lying beside its instruction manual. Four crops: the remote
at area 0.538 with 61 buttons, and three crops of the printed page with 50, 92
and 112. The page has no keycaps on it at all — what `detect_buttons` traced is
halftone, body text and a printed diagram of the remote, blown up because
rectification upscales any body to 400px whatever it was cut from.

The measured population, over the 12218-record catalogue:

    crops per photo   1: 10892   2: 486   3: 53   4: 23   5: 12   6: 6   7: 1

Every photograph with 3+ crops is a photograph the detector already knew was
implausible: `max_plausible_bodies` is 2, and past it `detect_bodies_with_mask`
asks `_full_frame_body` to replace the lot. When the frame is not
remote-shaped — a leaflet beside a remote makes a squarish frame — the fallback
declines and **all the bodies are kept anyway**. 95 photographs, 354 records.

Position and count both fail here. `_0` is as often the leaflet as the remote,
and the junk crops have *more* buttons than the real one, so the
sparse-beside-sibling rule shipped earlier this session cannot see them; it was
in fact being harmed by them, since a page traced as 112 keycaps was setting
`max_buttons` for the whole photograph.

What separates them is **buttons per 1000 source pixels**:

    catalogue p10 0.11   p50 0.22   p90 0.44        real remotes
    2750         _0 0.37 | _1 2.2  _2 2.3  _3 7.8   remote, then the leaflet
    worst record 36.09 (703_1, 230 buttons in a blister-card fragment)

Shipped as an absolute ceiling of 3.0 plus a sibling ratio of 3x above a floor
of 1.0 (gotcha in CLAUDE.md; the floor is what keeps it off good crops whose
sibling merely has fewer buttons). One definition in
`pipeline/extract.implausibly_dense`, called from the build *and* from
`/identify` — which had been picking the body with the most buttons while its
comment claimed the largest, so on this photograph the query was answered from
the leaflet. The ceiling alone would not have sufficed on either side: two of
the three leaflet crops sit under it at 2.3 and 2.2 and are caught only by
their siblings.

Deployed: 133 photographs re-extracted, **146 records refused**, catalogue
12218 -> 12079, 12211 index docs, both consumers resynced and in step. Seven
photographs are now left with no crop and should be: six blister-card
fragments and a Panasonic aircon remote whose only detected body was its LCD
panel. Across the catalogue, `n_buttons` p99 fell 92 -> 85 and the maximum
230 -> 156; photographs with 3+ crops fell 95 -> 49. `2750` yields one record
and self-retrieves at 0.9118 against a next-best 0.6070.

**Residual, 4 records.** Density cannot see a printed card that occupies a
*large* share of the frame, because area and button count rise together:

    812_0    140 buttons, area 0.27, label recall 0.04   vs 812_3    52 @ 0.36
    2151_0   105 buttons, area 0.45, label recall 0.01   vs 2151_2   48 @ 0.27
    703_2    102 buttons, area 0.34, label recall 0.02   vs 703_4    13 @ 0.08

The signature is a big crop with roughly double a sibling's buttons and no
labels read at all beside a sibling that reads plenty. Four records is not a
population worth a rule, and the obvious discriminator is label recall, which
is exactly the thing this project has agreed never to require.

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
