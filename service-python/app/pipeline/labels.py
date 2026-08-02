"""Arbitrate between text and buttons, and attach labels to buttons.

Two jobs, both consuming OCR output:

1. **Suppression.** Session 1's detector counted printed labels as buttons --
   the AKAI black remote returned 57 detections, most of them lettering. A
   geometric filter cannot fix this: SOURCE and a small rectangular button
   have the same fill ratio and aspect. Knowing where the text is, can.

2. **Assignment.** Each remaining text region is attached to its nearest
   button as `label` plus `label_pos`, which the matcher uses as a bonus
   scoring term. Regions too far from any button are group captions and stay
   in `text_regions`, where brand/model extraction picks them up.

The asymmetry that matters: a glyph printed on a keycap must not delete the
keycap, but a word printed on the body must not become one. The
discriminator is size -- a real button is substantially larger than the text
on it.
"""
from __future__ import annotations

from app.config import CFG


def _box(d: dict) -> tuple[float, float, float, float]:
    return (d["x"] - d["w"] / 2, d["y"] - d["h"] / 2,
            d["x"] + d["w"] / 2, d["y"] + d["h"] / 2)


def _intersection(a: dict, b: dict) -> float:
    ax1, ay1, ax2, ay2 = _box(a)
    bx1, by1, bx2, by2 = _box(b)
    ix = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    iy = max(0.0, min(ay2, by2) - max(ay1, by1))
    return ix * iy


def _covered_fraction(button: dict, regions: list[dict]) -> float:
    """Fraction of the button's area covered by text.

    Regions are summed rather than unioned: text boxes from one engine rarely
    overlap each other, and the result is clamped to 1.
    """
    area = max(button["w"] * button["h"], 1e-9)
    total = sum(_intersection(button, r) for r in regions)
    return min(total / area, 1.0)


def suppress_text_detections(buttons: list[dict],
                             regions: list[dict]) -> tuple[list[dict],
                                                           list[dict]]:
    """Split detections into real buttons and misdetected text.

    Returns (buttons, text_detections). The second list is kept for the debug
    overlay -- when suppression goes wrong, seeing what it removed is the only
    way to tell.
    """
    cfg = CFG.label
    kept, dropped = [], []

    for b in buttons:
        b_area = max(b["w"] * b["h"], 1e-9)
        touching = [r for r in regions if _intersection(b, r) > 0]
        covered = _covered_fraction(b, touching)

        if covered < cfg.text_overlap_frac:
            kept.append(b)
            continue

        # A caption strip spanning much of the body is never a button, however
        # large the detection sitting under it.
        if any(r["w"] >= cfg.caption_width_frac for r in touching):
            b["suppressed_by"] = "caption"
            dropped.append(b)
            continue

        # The detection is bigger than the text on it -> a keycap with a glyph
        # printed on it, which is exactly what we want to keep.
        biggest = max((r["w"] * r["h"] for r in touching), default=0.0)
        if biggest > 0 and b_area / biggest >= cfg.keep_area_ratio:
            kept.append(b)
            continue

        b["suppressed_by"] = "text"
        dropped.append(b)

    return kept, dropped


def _distance(region: dict, button: dict) -> float:
    dx = region["x"] - button["x"]
    dy = region["y"] - button["y"]
    return (dx * dx + dy * dy) ** 0.5


def _position(region: dict, button: dict) -> str:
    bx1, by1, bx2, by2 = _box(button)
    if bx1 <= region["x"] <= bx2 and by1 <= region["y"] <= by2:
        return "on"
    dx = region["x"] - button["x"]
    dy = region["y"] - button["y"]
    if abs(dy) >= abs(dx):
        return "above" if dy < 0 else "below"
    return "left" if dx < 0 else "right"


def assign_labels(buttons: list[dict], regions: list[dict]) -> list[dict]:
    """Attach each text region to its nearest button, in place.

    A button keeps its closest label only. Regions that find no button within
    `max_assign_dist` are tagged `role="caption"` and left for brand and
    model-code extraction.

    Returns the regions, annotated. Buttons gain `label`, `label_pos` and
    `label_conf`; buttons with no nearby text simply do not, and no caller may
    require that they do.
    """
    cfg = CFG.label
    best_for_button: dict[int, tuple[float, dict]] = {}

    for r in regions:
        if not buttons:
            r["role"] = "caption"
            continue
        idx = min(range(len(buttons)), key=lambda i: _distance(r, buttons[i]))
        dist = _distance(r, buttons[idx])
        if dist > cfg.max_assign_dist or r["w"] >= cfg.caption_width_frac:
            r["role"] = "caption"
            continue
        r["role"] = "label"
        r["button"] = idx
        prev = best_for_button.get(idx)
        if prev is None or dist < prev[0]:
            best_for_button[idx] = (dist, r)

    for idx, (_, r) in best_for_button.items():
        b = buttons[idx]
        b["label"] = r["text"]
        b["label_pos"] = _position(r, b)
        b["label_conf"] = r["conf"]

    return regions


def label_recall(buttons: list[dict]) -> float:
    """Fraction of buttons that ended up with a label."""
    if not buttons:
        return 0.0
    return sum(1 for b in buttons if b.get("label")) / len(buttons)
