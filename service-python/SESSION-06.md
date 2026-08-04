# Session 06 — the calibration corpus was thumbnails

Session 5 left three items. This session took item 3, "re-validate the
confidence bands on a real catalog", and found that the corpus it would have
been validated on is not a real catalog: three quarters of it is Drupal
imagecache thumbnails, 12–19 px wide by 50 px tall.

The bands are still not calibrated. That is the honest result, and it is a
better position than the one before, which was a table of numbers that looked
calibrated.

## What the first calibration said, and why none of it was true

`scripts/calibrate_bands.py` (written this session) ran over
`work/legacy_search.txt`, 94 images, 109 fingerprints. It reported:

| | |
|---|---|
| self-retrieval recall@1 | 76/91 |
| mean separation | **+0.559** |
| precision of `high` | 100% |
| precision of `medium` | 100% |
| precision of `low` | **100%** |
| precision of `none` | 0% (15 queries, all wrong) |

A separation of +0.559 against the +0.077 the 18-image set gives, and 100%
precision in *every* band including `low`, is not a good result. It is the
shape of a measurement with no wrong answers in it at all.

The cause: **72 of the 91 queries were imagecache thumbnails.** 41 of them
returned an identical top score of 0.9250 and 7 an identical 0.3750. A score
that is constant across dozens of different remotes is not a match score.

Two separate defects sat underneath it.

### 1. A thumbnail extracts confident buttons

`CFG.normalize.out_width` rectifies every body to 400 px whatever the source
was. A 16x50 thumbnail is therefore enlarged about 25x, and `detect_buttons`
traces the interpolation artefacts. `2376.jpg`, 16x50, extracted **29 buttons**
at quality 0.66, indexed, and self-matched at 0.925. Nothing in the pipeline
distinguished that from a real extraction, because structurally it is one.

Fixed: `CFG.normalize.min_source_long_side = 600`. The build refuses a smaller
source before extraction and records it as `too small (WxH, ...)`.

Long side, not both sides, and not area. A remote is elongated; the real
catalogue standard here is 303x1090. Measured over the 136 readable images in
`files/`:

| rule | keeps |
|---|---|
| both sides >= 600 | 10 |
| area >= 600*600 | 13 |
| **long side >= 600** | **60** |
| short side >= 200 | 62 |

The literal square rule would have thrown away 52 of the 62 usable images.

### 2. A featureless record indexed as a record

29 of the 109 fingerprints had **zero buttons and zero text regions**. With no
tokens they can never be retrieved — by themselves or by anything else — and 15
of them returned zero candidates when queried. The build reported every one of
them as `1 remote(s) extracted`; `skipped.txt` listed 3 images out of 94.

Fixed: a body with no buttons and no text is refused rather than written, with
the source dimensions in the reason, because the dimensions are the tell.

Both refusals are counted where the reason is still known, per the rule session
5 added for unreadable files. `docker/build-catalog.sh` now prints an exclusion
summary at the end of a run: one process per image meant nothing had ever
totalled them, and at 13k images the per-image lines are long gone.

## After the fix

Same 94-image manifest, rebuilt:

| | before | after |
|---|---|---|
| records written | 109 | **19** |
| records with zero features | 29 | **0** |
| queries returning zero candidates | 15 | **0** |
| self-retrieval recall@1 | 76/91 | **17/17** |
| mean separation | +0.559 | +0.595 |

The 19 is the honest count of remotes in this drop that are photographed at a
usable size. 75 images were refused as too small and 2 as unreadable.

## The bands are still uncalibrated, and this data cannot calibrate them

19 records, 17 queries, **zero wrong answers**. Every band is 100% precise by
construction, and the floor sweep has nothing to reject. The separation is
+0.595 for the same reason it was +0.559: no two of these 19 remotes are
confusable with each other.

A band is a promise about precision, and precision is undefined without a
population of wrong answers. That needs a catalog with confusable neighbours —
the 13k on `rcud`, not a 19-record sample of one. Until then `CFG.fuse.high_score`
and friends remain the implementation plan's opening guesses, and the fact that
they survive this measurement means nothing.

Run there:

```bash
php artisan rcu:legacy-manifest --out=- > work/primary.txt
docker compose --profile build run --rm extract --manifest /data/work/primary.txt --jobs 4
python scripts/calibrate_bands.py --photos ../files --manifest ../work/primary.txt \
    --fp ../work/fp --csv ../work/bands.csv
```

Expect the exclusion summary to be large. 10693 of the 13763 photographs the
manifest finds exist only as imagecache derivatives; how many of those are
`watermark` (large, usable) versus a small preset is not known here, and the
`too small` count is now the thing that reports it.

## The test page

`/try` — photograph a remote, see the answer. Nothing in the application did
this before: the admin visualiser's upload runs `fingerprint`, which is
extraction with no matching at all.

It calls `/api/identify` and `/api/identify/{id}/choose` exactly as any other
client would. Taking a shortcut straight to the service would have made it a
test of something that does not ship — and this project has now found three
bugs that lived on one path and not the other.

Off by default (`RCU_TRY_PAGE`), because it has **no authentication** and
serves catalog crops and match internals to anyone who can reach it. That is
acceptable on a loopback dev box and nowhere else. Deploying it to `rcud` means
putting auth in front of it first, which is not a one-liner: `/admin/rcu`
already declares `auth` middleware and this application has **no login route at
all**, so the admin visualiser is currently unreachable too.

The routes register unconditionally and the controller refuses when the flag is
off. Registering them inside an `if` reads tighter and is worse: routes bind at
boot, so the behaviour then depends on the environment at boot rather than on
the config, and cannot be tested without rebooting the application mid-test.

Verified end to end through nginx -> Laravel -> the service: `high`, 2534 ms,
`ClickPdu_RM-D1110_Philips_TV_1_0` at 1.325 against 0.247 for the runner-up,
catalog metadata resolved, and the feedback write landing in `rcu_queries`.
71 tests pass.

The page states the catalog size on its face. With 21 records almost every real
remote comes back `none`, and without that line it reads as a broken
recogniser rather than an empty catalog.

## Carried forward

1. **Low-contrast keycap detection (plan 9.1)** — unchanged, still the only
   substantial CV item and the only thing that moves `MR-18B_0_1` off 4 buttons.
2. **Calibrate the bands on `rcud`**, per above. The tooling is written and
   measured; only the corpus is missing.
3. **The query path has no size floor.** The build now refuses a thumbnail; the
   service still accepts one and will answer it confidently, for the same reason
   the build used to. Left alone deliberately — it changes an API verdict, and a
   phone photograph is never this small — but it is a real asymmetry.
4. Button drift between query and catalog: unchanged from session 5, measured as
   not costing recall.
5. `work/fp.bak-*` stale catalog copies, unchanged from session 5.
