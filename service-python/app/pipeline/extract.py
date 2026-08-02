"""End-to-end extraction: image in, fingerprints out.

This is the single extraction code path. Both the offline catalog build
(`scripts/extract_one.py`) and the query service call it, because a catalog
built by one implementation and queried by a slightly different one degrades
silently and looks like a matching problem -- the same reason token generation
is shared between build and query rather than written twice.

Nothing here touches the filesystem. Callers decide what to persist.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.config import CFG
from app.pipeline import debug_render as dbg
from app.pipeline.branding import find_brand, find_model_code
from app.pipeline.color import classify_colors
from app.pipeline.detect_body import detect_bodies, _foreground_mask
from app.pipeline.detect_buttons import detect_buttons
from app.pipeline.fingerprint import build_fingerprint
from app.pipeline.labels import assign_labels, suppress_text_detections
from app.pipeline.normalize import (apply_flip, flip_buttons,
                                    orientation_from_text, rectify,
                                    resolve_orientation)
from app.pipeline.ocr import run_ocr


@dataclass
class ExtractedRemote:
    """One detected remote: its fingerprint plus the pixels behind it."""
    index: int
    fingerprint: dict
    crop: np.ndarray                      # rectified canonical crop
    buttons: list[dict] = field(default_factory=list)
    regions: list[dict] = field(default_factory=list)
    suppressed: list[dict] = field(default_factory=list)
    aspect: float = 0.0
    orientation: dict = field(default_factory=dict)
    brand: dict | None = None
    model_code: str | None = None
    # Whole-image panels, computed once per image and shared by reference
    # across the remotes found in it. Rendering them per remote instead would
    # re-run body detection, which is the most expensive step here.
    mask: np.ndarray | None = field(default=None, repr=False)
    body_overlay: np.ndarray | None = field(default=None, repr=False)


def extract_remotes(img: np.ndarray, ensemble: bool = True,
                    use_ocr: bool = True,
                    consensus: bool = False,
                    fast_ocr: bool = False) -> list[ExtractedRemote]:
    """Detect every remote in `img` and fingerprint each one.

    `fast_ocr` selects the query path's OCR economy (one pass, lower upscale;
    see `OcrConfig.query_min_width`). The catalog build must leave it off.
    """
    out: list[ExtractedRemote] = []

    bodies = detect_bodies(img)
    mask = _foreground_mask(img)
    body_overlay = dbg.draw_bodies(img, bodies)

    for idx, body in enumerate(bodies):
        crop, _M, aspect = rectify(img, body["rect"])

        buttons = detect_buttons(crop, ensemble=ensemble)
        classify_colors(crop, buttons)

        orient = resolve_orientation(crop, buttons)
        orient["source"] = "geometry"

        regions: list[dict] = []
        suppressed: list[dict] = []
        brand = None
        model_code = None

        # The invariant every branch below must leave behind: `crop` is the way
        # up we are committing to, and `regions` are in that crop's
        # coordinates. `buttons` are brought into line afterwards, once, by
        # rotation.
        text_orientation = use_ocr and (not fast_ocr
                                        or CFG.ocr.query_both_orientations)

        if text_orientation:
            # OCR both ways up before committing. Text is the strongest
            # orientation signal available and costs one extra pass, which the
            # offline build can afford. The flipped regions are already in
            # flipped-crop coordinates, so whichever orientation wins, its
            # region set is reused as-is -- no second OCR after the decision.
            regions_up = run_ocr(crop, consensus=consensus)
            flipped = apply_flip(crop)
            regions_down = run_ocr(flipped, consensus=consensus)
            orient = orientation_from_text(regions_up, regions_down, orient)

            if orient["flip"]:
                crop, regions = flipped, regions_down
            else:
                regions = regions_up
        else:
            # No text orientation signal, so geometry's verdict stands. Commit
            # to it *before* reading, so the single OCR pass runs on the crop
            # we keep and its regions need no re-mapping.
            if orient["flip"]:
                crop = apply_flip(crop)
            if use_ocr:
                regions = run_ocr(crop, consensus=consensus,
                                  min_width=CFG.ocr.query_min_width)

        # Detections follow the crop by rotation, never by re-detection --
        # `detect_buttons` is not rotation-invariant, so re-running it here
        # would make the fingerprint depend on the flip verdict. See
        # `flip_buttons`.
        if orient["flip"]:
            buttons = flip_buttons(buttons, crop.shape)

        if use_ocr:
            buttons, suppressed = suppress_text_detections(buttons, regions)
            assign_labels(buttons, regions)
            brand = find_brand(regions)
            model_code = find_model_code(regions, buttons, brand)

        fp = build_fingerprint(buttons, aspect, orient, regions, brand,
                               model_code, body)

        out.append(ExtractedRemote(
            index=idx, fingerprint=fp, crop=crop, buttons=buttons,
            regions=regions, suppressed=suppressed, aspect=aspect,
            orientation=orient, brand=brand, model_code=model_code,
            mask=mask, body_overlay=body_overlay))

    return out


def debug_panel(img: np.ndarray, remote: ExtractedRemote,
                use_ocr: bool = True) -> np.ndarray:
    """The side-by-side overlay.

    Looking at this is how essentially every bug in this pipeline has been
    found, so it is part of the interface rather than a debugging afterthought
    -- the service exposes it at GET /debug/{request_id}.
    """
    mask = remote.mask if remote.mask is not None else _foreground_mask(img)
    overlay = (remote.body_overlay if remote.body_overlay is not None
               else dbg.draw_bodies(img, detect_bodies(img)))
    panels = [img, mask, overlay, remote.crop,
              dbg.draw_buttons(remote.crop, remote.buttons)]
    captions = ["original", "fg mask", "body detect", "rectified",
                f"buttons: {len(remote.buttons)}"]
    if use_ocr:
        panels.append(dbg.draw_text(remote.crop, remote.regions,
                                    remote.suppressed))
        captions.append(f"text: {len(remote.regions)}  "
                        f"cut: {len(remote.suppressed)}")
    return dbg.side_by_side(panels, captions)
