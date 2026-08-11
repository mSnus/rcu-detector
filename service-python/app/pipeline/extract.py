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
from app.pipeline.detect_body import detect_bodies, detect_bodies_with_mask
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
    # False when `ocr_only_best` skipped this body's text. Load-bearing, not
    # informational: see select_query_body.
    text_read: bool = True
    # Buttons found by detection, BEFORE suppress_text_detections removed the
    # ones that turned out to be printed words. This is what the query
    # selection ranks on, and it exists because it is the only count available
    # both before and after the text is read -- see select_query_body.
    n_detected: int = 0


def select_query_body(remotes: list[ExtractedRemote],
                      src_shape) -> ExtractedRemote | None:
    """The one body a query should be answered from.

    Most buttons wins -- but only among bodies whose buttons their own pixels
    could resolve. Photograph a remote lying on its instruction manual and the
    page extracts more keycaps than the remote does (112 against 61 on
    catalogue photo `2750`), so ranking on the raw count hands the query to the
    leaflet.

    Never returns nothing when given something: if every body is implausible
    the best of them still answers, because "no remote found" is a worse lie
    than a low-confidence match.

    One definition, called by `/identify` and by `extract_remotes` itself when
    it is deferring OCR to the winner -- those two must not be able to disagree
    about which body won, or the query would be answered from a body whose text
    was never read.
    """
    if not remotes:
        return None

    # If the reading was rationed, choose only among the bodies that were
    # read. Re-deciding freely here would sometimes pick a different body from
    # the one `extract_remotes` chose, and that body would have no text at all.
    #
    # The two selections differ because `suppress_text_detections` deletes
    # buttons that turned out to be printed words, so a body's button count
    # *falls* once it is read. Ranking read bodies (suppressed, lower) against
    # unread ones (unsuppressed, higher) is not comparing like with like:
    # `MR-18B_0` lost its winner that way and answered from a body whose text
    # was never read -- no labels, no brand, no model code, silently.
    read = [r for r in remotes if r.text_read]
    if read and len(read) < len(remotes):
        remotes = read

    # Ranked on `n_detected`, never on the fingerprint's button count. The
    # fingerprint's count is post-suppression and therefore only exists after
    # the text has been read, so ranking on it would make this function answer
    # differently depending on whether the reading had happened yet -- which is
    # exactly the decision it is being asked to make. `n_detected` is the same
    # number before and after, so the two agree by construction.
    counts = {r.index: (r.n_detected or len(r.buttons)) for r in remotes}
    dense = implausibly_dense(remotes, src_shape, counts=counts)
    plausible = [r for r in remotes if r.index not in dense] or remotes
    return max(plausible, key=lambda r: counts[r.index])


def extract_remotes(img: np.ndarray, ensemble: bool = True,
                    use_ocr: bool = True,
                    consensus: bool = False,
                    fast_ocr: bool = False,
                    ocr_only_best: bool = False) -> list[ExtractedRemote]:
    """Detect every remote in `img` and fingerprint each one.

    `fast_ocr` selects the query path's OCR economy (one pass, lower upscale;
    see `OcrConfig.query_min_width`). The catalog build must leave it off.

    `ocr_only_best` reads text on the winning body alone. A query answers from
    one body and discards the rest, but stages 4-10 ran on every one of them --
    on `2750`, four bodies at ~2.5 s of OCR each, of which three were thrown
    away. Selection does not use text (see `select_query_body`: buttons and
    area only), so deferring the read cannot change which body wins, and the
    winner is read exactly as before.

    The bodies that lose come back with buttons, geometry and a fingerprint,
    but no text -- no labels, no brand, no model code. That is only sound
    because the caller discards them; the catalog build must leave this off,
    and does, since every crop it keeps becomes a record.
    """
    out: list[ExtractedRemote] = []

    # The mask comes back from body detection rather than being computed
    # again. It was computed twice on every extraction -- once inside
    # detect_bodies and once here -- and the second one is read by exactly one
    # thing, the "fg mask" panel in debug_panel. That was 913 ms of the 4300 ms
    # a 12.6 MP phone photograph takes, measured, usually for an image that is
    # then discarded; and making it lazy instead would only move the cost onto
    # whoever asks for the overlay, which on this deployment is every query.
    bodies, mask = detect_bodies_with_mask(img)
    body_overlay = dbg.draw_bodies(img, bodies)

    # --- pass 1: geometry only ------------------------------------------
    # Everything that decides *which* body wins, and nothing that costs OCR.
    geom = []
    for idx, body in enumerate(bodies):
        crop, _M, aspect = rectify(img, body["rect"])

        buttons = detect_buttons(crop, ensemble=ensemble)
        classify_colors(crop, buttons)

        orient = resolve_orientation(crop, buttons)
        orient["source"] = "geometry"

        # Assuming upright means assuming it, not reading it and then turning
        # the crop over anyway. Geometry is confidently wrong often enough to
        # matter -- `RM-PJ20_big_light` reads flipped at confidence 1.00 -- so
        # leaving the verdict in place would flip a correctly-oriented photo
        # and read it upside down, which is the one failure this whole switch
        # is meant to stop paying for.
        if fast_ocr and CFG.ocr.assume_query_upright:
            orient = dict(orient, flip=False, ambiguous=False,
                          confidence=1.0, source="assumed")

        # A text-free fingerprint, built now because the selection reads
        # `stats.n_buttons` and `body.area_frac` off it. Replaced below for
        # whichever bodies go on to be read.
        geom.append((idx, body, crop, buttons, aspect, orient,
                     build_fingerprint(buttons, aspect, orient, [], None,
                                       None, body)))

    # Which bodies are worth reading. Everything, unless the caller has said
    # it will use only one -- in which case `select_query_body` decides, on
    # the same buttons-and-area rule the caller would have applied itself.
    to_read = {idx for idx, *_ in geom}
    if ocr_only_best and use_ocr and len(geom) > 1:
        provisional = [ExtractedRemote(index=i, fingerprint=fp, crop=c,
                                       buttons=b, aspect=a, orientation=o,
                                       n_detected=len(b))
                       for i, _body, c, b, a, o, fp in geom]
        winner = select_query_body(provisional, img.shape[:2])
        if winner is not None:
            to_read = {winner.index}

    # --- pass 2: read the text, on the bodies that earned it -------------
    for idx, body, crop, buttons, aspect, orient, fp in geom:
        read_this = use_ocr and idx in to_read
        n_detected = len(buttons)          # before suppression, see the dataclass

        regions: list[dict] = []
        suppressed: list[dict] = []
        brand = None
        model_code = None

        # The invariant every branch below must leave behind: `crop` is the way
        # up we are committing to, and `regions` are in that crop's
        # coordinates. `buttons` are brought into line afterwards, once, by
        # rotation.
        # The query normally trusts geometry and OCRs once. It stops trusting
        # it when geometry is not confident: a wrong flip means the single pass
        # reads an upside-down crop, and brand and model code are lost for
        # good. See `query_text_orientation_below_conf`.
        # `assume_query_upright` short-circuits all of it: a deployment that
        # has decided upside-down photographs do not happen must not pay for
        # the second pass on a low-confidence geometry verdict either, which is
        # the branch that actually fires. It applies to the query path only --
        # `fast_ocr` is False on the build, so the catalogue is unaffected and
        # keeps resolving orientation from text at full upscale.
        text_orientation = read_this and (
            not fast_ocr
            or (not CFG.ocr.assume_query_upright
                and (CFG.ocr.query_both_orientations
                     or orient.get("confidence", 1.0)
                     < CFG.ocr.query_text_orientation_below_conf)))

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
            if read_this:
                regions = run_ocr(crop, consensus=consensus,
                                  min_width=CFG.ocr.query_min_width)

        # Detections follow the crop by rotation, never by re-detection --
        # `detect_buttons` is not rotation-invariant, so re-running it here
        # would make the fingerprint depend on the flip verdict. See
        # `flip_buttons`.
        if orient["flip"]:
            buttons = flip_buttons(buttons, crop.shape)

        if read_this:
            buttons, suppressed = suppress_text_detections(buttons, regions)
            assign_labels(buttons, regions)
            brand = find_brand(regions)
            model_code = find_model_code(regions, buttons, brand)

        # Rebuilt whenever anything above moved: the text, the suppression it
        # drives, or the flip. A body that was neither read nor flipped keeps
        # the text-free fingerprint pass 1 already built for it.
        if read_this or orient["flip"]:
            fp = build_fingerprint(buttons, aspect, orient, regions, brand,
                                   model_code, body)

        out.append(ExtractedRemote(
            index=idx, fingerprint=fp, crop=crop, buttons=buttons,
            regions=regions, suppressed=suppressed, aspect=aspect,
            orientation=orient, brand=brand, model_code=model_code,
            mask=mask, body_overlay=body_overlay,
            text_read=read_this or not use_ocr, n_detected=n_detected))

    return out


def _density(n: int, remote: ExtractedRemote, src_shape) -> float:
    px = (remote.fingerprint["body"]["area_frac"]
          * float(src_shape[0] * src_shape[1]))
    return n / (px / 1000.0) if px > 0 else 0.0


def button_density(remote: ExtractedRemote, src_shape) -> float:
    """Buttons per 1000 pixels of the body *as photographed*.

    Not per pixel of the crop: the crop is `CFG.normalize.out_width` wide
    whatever it was cut from, which is exactly why a photographed instruction
    leaflet extracts a hundred confident keycaps out of halftone. See
    `CFG.body.max_button_density`.
    """
    px = (remote.fingerprint["body"]["area_frac"]
          * float(src_shape[0] * src_shape[1]))
    return len(remote.buttons) / (px / 1000.0) if px > 0 else 0.0


def implausibly_dense(remotes: list[ExtractedRemote], src_shape,
                      counts: dict[int, int] | None = None) -> set[int]:
    """Indices of bodies holding more buttons than their pixels can resolve.

    Takes the whole photograph, not one body, because half the verdict is
    relative: see `CFG.body.max_button_density` for the ceiling and
    `sibling_max_density_ratio` for what settles the band beneath it.

    One definition, called from the build (`scripts/extract_one.py`) and from
    the query path (`app/main.py`). The two take different routes through
    extraction and a rule enforced at one end only is not enforced -- and on
    `2750` the ceiling alone is not enough on either side: two of the three
    leaflet crops sit under it and are caught only by their siblings.
    """
    cfg = CFG.body
    dens = {r.index: (button_density(r, src_shape) if counts is None else
                      _density(counts[r.index], r, src_shape))
            for r in remotes}
    # Over the bodies that have buttons at all: one with none has density 0 and
    # would otherwise become the reference every other body is three times
    # denser than, turning one blank crop into a verdict on the photograph.
    floor = min((d for d in dens.values() if d > 0), default=0.0)

    out = set()
    for r in remotes:
        d = dens[r.index]
        if d > cfg.max_button_density:
            out.add(r.index)
        elif (len(remotes) > 1
                and d > cfg.sibling_density_floor
                and floor > 0
                and d > cfg.sibling_max_density_ratio * floor):
            out.add(r.index)
    return out


def debug_panel(img: np.ndarray, remote: ExtractedRemote,
                use_ocr: bool = True) -> np.ndarray:
    """The side-by-side overlay.

    Looking at this is how essentially every bug in this pipeline has been
    found, so it is part of the interface rather than a debugging afterthought
    -- the service exposes it at GET /debug/{request_id}.
    """
    # Both fall back to recomputing for a caller that built the ExtractedRemote
    # itself, and both then come from the same run so the two panels cannot
    # disagree about which segmentation was used.
    if remote.mask is None or remote.body_overlay is None:
        bodies, fresh = detect_bodies_with_mask(img)
    mask = remote.mask if remote.mask is not None else fresh
    overlay = (remote.body_overlay if remote.body_overlay is not None
               else dbg.draw_bodies(img, bodies))
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
