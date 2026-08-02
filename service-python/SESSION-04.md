# Session 04 — the flip re-detection bug, and two corrections to session 3

Session 3's list put "text/button arbitration" first, on the strength of
`MR-18B_0_1` going 21 buttons -> 4. That attribution was wrong, and so was the
generalisation drawn from it. Both are corrected below with measurements. The
real defect behind the 21 -> 4 was found and fixed.

## Run it

Unchanged from session 3.

```bash
for f in ../photos/*.jpg; do
    python scripts/extract_one.py "$f" --out ../work      # + --ocr-width 800
done                                                       # on the 5 big ones
python scripts/build_index.py --fp ../work/fp --out ../work/index/tokens.npz
python scripts/match_eval.py  --fp ../work/fp --index ../work/index/tokens.npz
```

## What landed

**`detect_buttons` is not invariant under a 180 degree rotation, and the
pipeline was re-running it on the flipped crop.** `normalize.flip_buttons`
now rotates the existing detections instead. `extract.py` calls it once,
after the orientation branch, and the `flip_applied` bookkeeping the two
branches needed is gone with it.

### The cause, isolated

`apply_flip` is an exact `cv2.rotate(..., ROTATE_180)` — verified, the flipped
crop is bit-identical to `np.rot90(crop, 2)`. So detection on it should be
invariant. It is not:

| MR-18B_0 body 1, light polarity | upright | flipped |
|---|---|---|
| block=19 | 26 | 25 |
| block=31 | **12** | **27** |
| block=47 | 9 | 8 |
| final after votes | **6** | **25** |

Holding the CLAHE output fixed and rotating *that*, `adaptiveThreshold` agrees
on **100.00% of pixels** at every block size and polarity, and the per-pass
counts come back to 26/12/9 exactly. So the thresholding is exactly invariant
and **CLAHE is the whole of the asymmetry**: its `tileGridSize=(8, 8)` grid is
anchored to the crop's top-left corner, and 1326 is not a multiple of 8, so
rotating the pixels moves every tile boundary. The equalised image differs on
64.8% of pixels, by up to 18 grey levels.

18 grey levels is enough because the vote threshold sits on a cliff there:
one pass moves 12 -> 27, which changes which detections get corroborated by a
second pass, which moves the final count 6 -> 25.

The consequence was that a record's button set depended on a flip verdict
taken *after* detection. The same remote indexed differently upright and
flipped, silently — the corruption `resolve_orientation`'s docstring warns
about, arriving one layer below it.

Rotating the detections is exact and free, and it drops a redundant
`detect_buttons` + `classify_colors` per flipped record.

## Correction 1: suppression did not cause MR-18B_0_1's 21 -> 4

Session 3: *"`suppress_text_detections` took `MR-18B_0_1` from 21 buttons to
4."* It did not. That record's debug panel reads `text: 22  cut: 2`.
Suppression removed **2** detections. 4 kept + 2 cut = 6, which is exactly
what `detect_buttons` returns on the upright crop.

What actually happened is the session-3 OCR fix, working correctly. The
angle-classifier change flipped this record's verdict from flipped (wrong) to
upright (right). Pre-fix it was detected on the flipped crop — 25 detections,
21 surviving suppression. Post-fix it is detected upright — 6 detections, 4
surviving. The 25 was never a better extraction, only a luckier CLAHE
alignment, and it was attached to an upside-down crop.

Catalog-wide, suppression removes 70 of 426 detections (16%).

## Correction 2: no evidence that legends are deleting keycaps

The session-3 item was *"a legend printed on a keycap must attach to the
button, not delete it."* Nothing in the sample supports it.

`SMART_TV_T96_0` is the worst case after the BAD label strip — `cut: 10` of 22
— and its whole number keypad is missing from the output, which looks exactly
like the described failure. It is not. Every suppressed detection there is
*text-sized*:

| suppressed | detection area / text area | covered |
|---|---|---|
| MEDIA | 0.77 | 1.00 |
| WEB | 0.69 | 1.00 |
| 8 | 1.05 | 0.92 |
| VOL- / VOL+ | 0.64 / 0.52 | 1.00 |
| RETURN | 0.72 | 1.00 |
| OK / OK | 0.32 / 0.30 | 1.00 |
| MENU | 0.89 | 0.91 |
| SET | 0.69 | 1.00 |

`keep_area_ratio` is 2.2 and every one of these is at or below 1.05 — the
detection is no bigger than the word printed on it. These are not keycaps with
legends on them; they *are* the legends. Suppression is behaving exactly as
designed.

The keypad is missing because `detect_buttons` never found the keycaps.
Matte-black keys on a matte-black body have almost no edge contrast, the
printed glyph is the only thing with any, and suppression then correctly
removes the glyph. Raising `keep_area_ratio` would not recover a single
keycap; it would only re-admit text as buttons — the AKAI 57-detection failure
from session 1.

So the lever is upstream in detection, exactly as CLAUDE.md's "the cause is
nearly always upstream in extraction" says. Plan 9.1's trained detector is the
real fix.

## Results on the 21 records

| | session 3 | session 4 |
|---|---|---|
| total buttons | 355 | 356 |
| orientation unresolved | 2/21 | 2/21 |
| brand | 11/21 | 11/21 |
| model code | 7/21 | 7/21 |
| recall@1 (of 8 answerable) | 8/8 | 8/8 |
| true/false separation | +0.077 | +0.077 |
| records flagged by audit | 12/21 | 12/21 |

Re-extracted at session-3 OCR settings (default width, `--ocr-width 800` on
the same five) so the comparison is clean. The session-3 fingerprints are kept
at `work/fp.bak-preflip/`, alongside session 2's `work/fp.bak-precls/`.

The service was left running from a previous session and had to be stopped
before the rebuild would fit in memory — see CLAUDE.md "Memory". **It is
currently stopped.**

**The fix is prophylactic on this sample.** Only one record — `RM-L859-1_0`,
already marked BAD — actually flips after the session-3 OCR change, and it
went 1 -> 2 buttons. Everything else is untouched, because nothing else
flips. The bug was real, it was silent, and it would have surfaced on a
catalog where flipped records are common; it simply has almost nothing to
bite on here.

### Verified end to end

Each remote queried upright and rotated 180 degrees against the service:

| query | upright | rotated 180 |
|---|---|---|
| Sony_RM-PJ20_big | high, 26 btns, 1.400, inl=26 | high, 26 btns, 1.379, inl=25 |
| SMART_TV_T96 | high, 11 btns, 0.907, inl=11 | high, 12 btns, 0.768, inl=8 |
| HONEYWELL_HE5500 | high, 6 btns, 0.998, inl=6 | high, 6 btns, 0.923, inl=6 |

All six identify the right record at high confidence. The residual score
differences come from body detection and rectification on a rotated *source
photo*, which is a different input, not from the button rotation.

## Correction 3: brand recall is at its ceiling, not blocked on OCR

Sessions 2, 3 and 4 all carried "brand recall — visual wordmark templates
(plan 3.8), still 11/21" as the top remaining CV item, on the reading that
stylised wordmarks defeat OCR. Looking at the ten brandless crops, that is not
what is happening.

**Eight of the ten have no manufacturer wordmark printed on them at all.** The
largest text on each is a model marking or a service logo:

| record | largest printed text | manufacturer wordmark? |
|---|---|---|
| ClickPdu_RM-D1110 | `RM-D1110` | none |
| Prestigio_KF-7777A | (none) | none |
| Huayu_Motorola_Cisco_15 | `mxv3 TB` | none |
| YDX-107 | (none) | none |
| DVD_80 | stylised `DVD`, `PULTOV.NET` watermark | none |
| MR-18B_0_0 / _0_1 | `MR-18B`, `ivi`, `NETFLIX` | none |
| SMART_TV_T96 | `MEDIA` `TV` `WEB` `APP` | none |

The remaining two are the known-BAD extractions (`Huayu_RM-530F_JVC_TV_7_0`,
a thumb and the edge of a remote; `RM-L859-1_0`, a packaging label strip).

These are generic OEM remotes. Prestigio and ClickPdu are the sellers, not
anything printed on the plastic. So **11/21 is 11 of 11 achievable** — every
record that carries a wordmark is already read correctly, including the
stylised lowercase `aiwa` that session 2 recorded as reading "EMIE". It reads
as Aiwa now, at 0.905, via the verified list path.

A visual template bank would therefore gain exactly zero on this sample, and
could not be tuned or validated on it either. It may still be worth building
for a real catalog full of Samsung and Philips logotypes — but that is a bet
placed blind, and it is not the reason brand recall reads 11/21 today.

## Also fixed: the query-side false brand

`find_brand`'s unverified fallback now applies the two guards it was missing.
Its own docstring and plan 3.8 both say "isolated text >= 3x the median text
height", but the code only ever tested the height. Added:

- **isolation** (`wordmark_isolation_radius`, new in `BrandConfig`) — a
  wordmark sits in clear space at an end of the body; `APP` had `MEDIA`, `TV`
  and `WEB` ranged alongside it
- **the vocabulary blocklist** — the same guard `find_model_code` already
  applies for VGA1 / HDMI2. A known button legend is never a brand.

`SMART_TV_T96` now returns `brand=None` on both paths. No catalog record used
the unverified path, so nothing regressed: brand 11/21, model code 7/21,
recall@1 8/8, separation +0.077, all unchanged after a full rebuild.

## Known-bad, for session 5

- **Separation is still governed by `MR-18B_0_1`.** The `MR-18B_0_0` <->
  `MR-18B_0_1` true pair scores 0.510 with **inl=0** — zero RANSAC inliers,
  because one side has 4 buttons. It is the only true pair below 0.51, so it
  alone sets separation against the DVD_80/YDX-107 false pair at 0.433. The
  cause is detection on a packaging photo, not arbitration.
- **The service plateaus at ~370 MB RSS and the box cannot always hold it.**
  Idle 95 MB, ~315 MB after the first query, plateauing ~360-370 MB — a
  one-time OCR model allocation, not a leak. But with ~560 MB available it was
  OOM-killed twice during this session on 3.6-5.3 MP queries. Not a code
  defect; a note for whoever sizes the box.
- Unchanged from session 3: stylised wordmarks defeat OCR (brand still 11/21),
  adjacent remotes bridged by packaging still merge, colour bucketing still
  untuned against phone photos, confidence bands still uncalibrated on 21
  records.

## Next session

1. **Laravel: uploads, catalog DB, admin UI, calling 127.0.0.1:8600.** Now the
   top item, because both CV items ahead of it turned out not to be defects.
2. Low-contrast keycap detection (plan 9.1) — the real content of what
   "text/button arbitration" was standing in for, and the only thing that
   would move `MR-18B_0_1` off 4 buttons and lift separation off +0.077.
3. Re-validate the confidence bands once the catalog is real.

Two items are **off the list**, each disproved by measurement rather than
deferred:

- *Text/button arbitration* — Correction 2. Suppression cuts 2 detections from
  `MR-18B_0_1`, not 17, and everything it cuts elsewhere is text-sized.
- *Visual wordmark templates* — Correction 3. 11/21 is 11 of 11 achievable on
  this sample; eight of the ten misses have no wordmark to match.

Both had been carried forward for two sessions on a plausible-sounding
attribution that no one had checked against the overlays. That is the pattern
CLAUDE.md's "look at the overlay before changing code" is aimed at, and it
cost two sessions of misdirected priority.
