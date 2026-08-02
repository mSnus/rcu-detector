"""Detect buttons within a rectified body crop.

Classical CV, phase 1. This is the known weak point of the baseline -- matte
black buttons on matte black bodies have almost no edge contrast. The ensemble
over several adaptive-threshold block sizes recovers a useful amount of that;
a trained detector (plan section 9.1) is the real fix.

High recall matters more than high precision here. A spurious button costs one
bad token; a missed button loses several real ones.
"""
from __future__ import annotations

import cv2
import numpy as np

from app.config import CFG


def _classify_shape(contour, w: int, h: int) -> str:
    area = cv2.contourArea(contour)
    if area <= 0:
        return "unknown"
    rect_fill = area / float(w * h)
    circ_fill = area / (np.pi * (w / 2) * (h / 2) + 1e-6)
    aspect = max(w, h) / max(min(w, h), 1)
    if aspect > 2.6:
        return "bar"
    if circ_fill > 0.86 and rect_fill < 0.88:
        return "ellipse"
    if rect_fill > 0.88:
        return "rect"
    return "rounded_rect"


def _iou(a: dict, b: dict) -> float:
    ax1, ay1 = a["x"] - a["w"] / 2, a["y"] - a["h"] / 2
    ax2, ay2 = a["x"] + a["w"] / 2, a["y"] + a["h"] / 2
    bx1, by1 = b["x"] - b["w"] / 2, b["y"] - b["h"] / 2
    bx2, by2 = b["x"] + b["w"] / 2, b["y"] + b["h"] / 2
    ix = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    iy = max(0.0, min(ay2, by2) - max(ay1, by1))
    inter = ix * iy
    union = a["w"] * a["h"] + b["w"] * b["h"] - inter
    return inter / max(union, 1e-9)


def _contains(outer: dict, inner: dict, margin: float = 0.85) -> bool:
    """True if `inner` sits almost entirely inside `outer`."""
    ox1, oy1 = outer["x"] - outer["w"] / 2, outer["y"] - outer["h"] / 2
    ox2, oy2 = outer["x"] + outer["w"] / 2, outer["y"] + outer["h"] / 2
    ix1, iy1 = inner["x"] - inner["w"] / 2, inner["y"] - inner["h"] / 2
    ix2, iy2 = inner["x"] + inner["w"] / 2, inner["y"] + inner["h"] / 2
    px = max(0.0, min(ox2, ix2) - max(ox1, ix1))
    py = max(0.0, min(oy2, iy2) - max(oy1, iy1))
    inner_area = max(inner["w"] * inner["h"], 1e-9)
    return (px * py) / inner_area > margin


def _drop_nested(buttons: list[dict]) -> list[dict]:
    """Remove detections nested inside a larger one.

    Dual-polarity detection finds both a light button and the dark digit
    printed on it. The button is the feature; the glyph is a label and is
    handled by OCR, not by the detector.
    """
    order = sorted(buttons, key=lambda b: -(b["w"] * b["h"]))

    # A detection containing several others is a recessed panel or a button
    # group, not a button. Drop the container and keep its contents.
    containers = set()
    for i, outer in enumerate(order):
        inner_n = sum(1 for j, b in enumerate(order)
                      if j != i and _contains(outer, b))
        if inner_n >= 3:
            containers.add(i)

    kept: list[dict] = []
    for i, b in enumerate(order):
        if i in containers:
            continue
        if any(_contains(k, b) for k in kept):
            continue
        kept.append(b)
    return kept


def _rescue_ring(contour, area: float, w: int, h: int, aspect: float,
                 area_img: float, shape_contour, fill: float):
    """Re-measure a contour that looks like a button outline.

    A low-contrast button thresholds as a ring rather than a disc. When that
    ring is unbroken `findContours` still reports the enclosed area, but a ring
    with a gap in it is traced as a *stroke*, so `contourArea` returns the area
    of the stroke -- a couple of hundred pixels for a 90px button -- and
    `min_fill` discards it. Measuring the convex hull instead recovers the
    button.

    The guards exist to keep printed lettering out: a rescued contour has to be
    round, hollow and large. An `O` or a `0` can still slip through, which is
    acceptable -- it is one spurious token, and OCR suppression downstream
    removes most of them. Returns the measurements unchanged when the contour
    does not look like a ring.
    """
    cfg = CFG.button
    if aspect > cfg.ring_max_aspect:
        return area, fill, shape_contour

    hull = cv2.convexHull(contour)
    hull_area = cv2.contourArea(hull)
    if hull_area <= 0:
        return area, fill, shape_contour

    hull_fill = hull_area / float(w * h)
    solidity = area / hull_area
    if (hull_fill >= cfg.ring_min_hull_fill
            and solidity < cfg.ring_max_solidity
            and hull_area >= cfg.ring_min_area_mult * cfg.min_area_frac * area_img):
        return hull_area, hull_fill, hull
    return area, fill, shape_contour


def _pass(gray: np.ndarray, block: int, invert: bool = True) -> list[dict]:
    """One detection pass.

    invert=True finds regions darker than their surroundings, invert=False
    finds lighter ones. Both are needed: a dark body with light buttons (the
    AKAI RC-51A) and a light body with dark buttons both occur, sometimes on
    the same remote.
    """
    cfg = CFG.button
    H, W = gray.shape
    area_img = float(H * W)

    th = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV if invert else cv2.THRESH_BINARY,
        block, cfg.adaptive_c if invert else -cfg.adaptive_c)
    th = cv2.morphologyEx(th, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))

    contours, _ = cv2.findContours(th, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
    found: list[dict] = []
    for c in contours:
        area = cv2.contourArea(c)
        x, y, w, h = cv2.boundingRect(c)
        if w < 3 or h < 3:
            continue
        aspect = max(w, h) / max(min(w, h), 1)
        fill = area / float(w * h)
        shape_contour = c

        if cfg.ring_rescue and fill < cfg.min_fill:
            area, fill, shape_contour = _rescue_ring(c, area, w, h, aspect,
                                                     area_img, shape_contour,
                                                     fill)

        if not (cfg.min_area_frac * area_img < area < cfg.max_area_frac * area_img):
            continue
        if fill < cfg.min_fill:
            continue
        if aspect > cfg.max_aspect:
            continue
        found.append({
            "x": (x + w / 2) / W,
            "y": (y + h / 2) / H,
            "w": w / W,
            "h": h / H,
            "shape": _classify_shape(shape_contour, w, h),
            "px": (x, y, w, h),
            "votes": 1,
        })
    return found


def detect_buttons(crop: np.ndarray, ensemble: bool = True) -> list[dict]:
    """Detect buttons. With ensemble=True, requires agreement across passes."""
    cfg = CFG.button
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    # mild contrast boost helps the black-on-black case
    gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)

    W = gray.shape[1]
    fracs = cfg.block_fracs if ensemble else cfg.block_fracs[1:2]
    blocks = sorted({max(7, int(W * f) | 1) for f in fracs})

    # Run every pass first, then discard the ones that clearly failed. A pass
    # that returns nothing must not be allowed to veto a pass that worked.
    passes: list[tuple[str, list[dict]]] = []
    for invert in (True, False):
        for block in blocks:
            passes.append(("dark" if invert else "light",
                           _pass(gray, block, invert=invert)))

    raw = [(p, f) for p, f in passes if len(f) >= cfg.min_pass_yield]

    # `min_pass_yield` is an absolute count, so a remote with genuinely few
    # buttons -- a five-button air-conditioner remote, say -- can have every
    # pass fall below it and return nothing at all. Nothing is always the wrong
    # answer: keep the best pass and let the vote logic below handle it.
    if not raw:
        best = max(passes, key=lambda pf: len(pf[1]), default=None)
        if best and best[1]:
            raw = [best]

    n_good = {"dark": sum(1 for p, _ in raw if p == "dark"),
              "light": sum(1 for p, _ in raw if p == "light")}

    merged: list[dict] = []
    for polarity, found in raw:
        for cand in found:
            cand["polarity"] = polarity
            hit = None
            for m in merged:
                if _iou(cand, m) > cfg.dedupe_iou:
                    hit = m
                    break
            if hit is None:
                merged.append(cand)
            else:
                # votes accumulate within a polarity only: a button seen once
                # as dark and once as light is one detection, not a
                # corroborated one
                if hit["polarity"] == cand["polarity"]:
                    hit["votes"] += 1
                if cand["w"] * cand["h"] > hit["w"] * hit["h"]:
                    keep = hit["votes"]
                    hit.update({k: cand[k] for k in
                                ("x", "y", "w", "h", "shape", "px",
                                 "polarity")})
                    hit["votes"] = keep

    # require corroboration only where corroboration was actually possible
    out = [b for b in merged
           if b["votes"] >= (cfg.min_votes
                             if n_good.get(b["polarity"], 0) >= cfg.min_votes
                             else 1)]
    out = _drop_nested(out)
    out.sort(key=lambda b: (round(b["y"], 2), b["x"]))
    return out
