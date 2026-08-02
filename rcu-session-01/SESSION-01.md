# Session 01 — Extraction pipeline baseline

Corresponds to plan sections 1, 3.3–3.6, 3.9 and 6.5 (visualiser). No OCR, no
matching yet — those are session 2 and 3.

## Run it

```bash
pip install -r requirements.txt
python scripts/extract_one.py --dir /path/to/images --out ../work
```

Outputs per detected remote: `norm/` rectified crop, `debug/` five-panel
overlay, `fp/` fingerprint JSON.

## Results on the six sample images

| Image | Bodies | Buttons | Chromatic | Quality | Assessment |
|---|---|---|---|---|---|
| RC-51A (200×500) | 1 | 8 | 0 | 0.75 | Under-detected; ~20 expected. Low-res source. |
| AKAI black (300×990) | 1 | 57 | 6 | 0.70 | Over-detected; label text counted as buttons. |
| AKAI LCD (303×1090) | 1 | 23 | 2 | 0.84 | Reasonable; misses low-contrast dark buttons. |
| AKAI pair (442×719) | **2** | 43 / 12 | 3 / 2 | 0.88 / 0.73 | Split works. Second crop under-detected. |
| Blue/orange (300×973) | 1 | 25 | 12 | 0.86 | Good. Orange cluster partly found. |
| InFocus (325×822) | 1 | 23 | 2 | 0.86 | Close to ground truth (~22). |

Nothing here is production accuracy. It is a working baseline with a visible
feedback loop, which is the point of session 1.

## Bugs found and fixed this session

Each was found by looking at an overlay, not by reasoning about the code.

1. **Single-polarity thresholding.** Only dark-on-light buttons were detected.
   The RC-51A has light buttons on a dark body and yielded 2 detections.
   → Dual-polarity passes, with votes accumulating within a polarity only.

2. **Absolute threshold block sizes.** Block 21 found 39 buttons on one crop
   while block 31 found zero, and the two-vote requirement then wiped out
   everything. → Block sizes are now fractions of crop width, and a pass that
   yields almost nothing is excluded from the vote instead of vetoing it.

3. **Panel swallowing its own buttons.** Dual-polarity detection finds the
   recessed panel as one large region containing the buttons; nested
   suppression then deleted the buttons and kept the panel. → A detection
   containing 3+ others is treated as a container and dropped.

4. **Edge-touching rejection too aggressive.** Tightly cropped catalog images
   have remotes touching top and bottom; one of the paired remotes was being
   discarded as "the image frame". → Requires 3+ edges *and* high area
   coverage.

5. **Greyscale Otsu failing on gradient backgrounds.** The RC-51A sits on a
   grey gradient; Otsu merged remote and background into a whole-frame blob.
   → Added Lab colour-distance-from-border segmentation.

6. **Lab segmentation bridging adjacent remotes.** The watermark laid across
   both remotes in the paired image connects them in Lab space, undoing fix 4.
   → Both strategies now run and the more plausible result wins, judged on
   largest single body rather than total coverage.

7. **Orientation heuristic was simply wrong.** It assumed keypads sit low. The
   RC-51A has digits at the top and the InFocus has them at the bottom, so
   density carries no signal. → Replaced with silhouette taper plus
   large-button position, and it now returns `ambiguous` rather than guessing.
   Ambiguous crops should be indexed in both orientations.

## Known-bad, for session 2

- **Text counted as buttons.** The 57-button result is mostly printed labels.
  Fix belongs with OCR: text regions get identified and excluded from the
  button set rather than filtered geometrically.
- **Low-contrast dark buttons still missed.** This is the expected ceiling of
  classical CV and the reason plan section 9.1 exists.
- **Orientation still flips some upright crops.** Taper is weak on
  parallel-sided remotes. OCR text baseline will settle most of these.
- **Colour bucketing is untuned against real phone photos.** Every threshold in
  `ColorConfig` was set against studio images and will need recalibration —
  do not trust it until it has seen tungsten light.

## Next session

1. OCR integration (`pipeline/ocr.py`) with the engine behind an interface
2. Text-region exclusion from the button set — should fix the over-detection
3. Brand matching and the model-code regex with the `BUTTON_VOCAB` blocklist
4. `scripts/audit_catalog.py` so you can point this at the real catalog

## Tuning

Every threshold is in `app/config.py`. Change a value, re-run, look at the
overlay in `work/debug/`. Do not scatter constants into the pipeline modules.
