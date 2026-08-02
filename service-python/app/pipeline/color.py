"""Assign each button a coarse colour name.

Coarse on purpose. "red" survives white-balance shifts between a studio shot
and a phone photo under tungsten light; an RGB triple does not. The rare
colours (red power, orange cluster, the RGBY row) are the high-IDF tokens that
do most of the discriminative work at match time.
"""
from __future__ import annotations

import cv2
import numpy as np

from app.config import CFG

CHROMATIC = {"red", "orange", "yellow", "green", "blue", "purple"}


def _bucket(h: float, s: float, v: float) -> str:
    cfg = CFG.color
    if v < cfg.value_black_max:
        return "black"
    if s < cfg.sat_grey_max:
        return "white" if v > cfg.value_white_min else "grey"
    for name, ranges in cfg.hue_ranges.items():
        if any(lo <= h <= hi for lo, hi in ranges):
            return name
    return "grey"


def classify_colors(crop: np.ndarray, buttons: list[dict]) -> list[dict]:
    """Attach 'color' and 'color_conf' to each button, in place."""
    cfg = CFG.color
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    H, W = hsv.shape[:2]

    for b in buttons:
        x, y, w, h = b["px"]
        # sample the inner region only: rims catch specular highlights
        mx, my = int(w * (1 - cfg.inner_frac) / 2), int(h * (1 - cfg.inner_frac) / 2)
        x0, y0 = max(0, x + mx), max(0, y + my)
        x1, y1 = min(W, x + w - mx), min(H, y + h - my)
        if x1 <= x0 or y1 <= y0:
            x0, y0, x1, y1 = x, y, min(W, x + w), min(H, y + h)

        patch = hsv[y0:y1, x0:x1].reshape(-1, 3)
        if patch.size == 0:
            b["color"] = "grey"
            b["color_conf"] = 0.0
            continue

        hue_med = float(np.median(patch[:, 0]))
        sat_med = float(np.median(patch[:, 1]))
        val_med = float(np.median(patch[:, 2]))
        name = _bucket(hue_med, sat_med, val_med)

        # confidence: how consistently the patch agrees with the assignment
        votes = [_bucket(*p) for p in patch[::max(1, len(patch) // 60)]]
        agree = sum(1 for x_ in votes if x_ == name) / max(len(votes), 1)

        b["color"] = name
        b["color_conf"] = round(float(agree), 3)
        b["hsv"] = [round(hue_med, 1), round(sat_med, 1), round(val_med, 1)]

    return buttons


def color_summary(buttons: list[dict]) -> dict:
    """Counts per bucket. Chromatic counts are the useful part."""
    out: dict[str, int] = {}
    for b in buttons:
        out[b.get("color", "grey")] = out.get(b.get("color", "grey"), 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))
