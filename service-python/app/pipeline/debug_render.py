"""Render annotated overlays.

This is the tuning loop. Every threshold in config.py is adjusted by changing
a value, re-running, and looking at these images. Build this before you build
the matcher.
"""
from __future__ import annotations

import cv2
import numpy as np

BGR = {
    "red": (0, 0, 220), "orange": (0, 140, 255), "yellow": (0, 220, 220),
    "green": (0, 200, 0), "blue": (255, 120, 0), "purple": (200, 0, 200),
    "grey": (150, 150, 150), "black": (60, 60, 60), "white": (240, 240, 240),
}


def draw_bodies(img: np.ndarray, bodies: list[dict]) -> np.ndarray:
    out = img.copy()
    for i, b in enumerate(bodies):
        box = cv2.boxPoints(b["rect"]).astype(int)
        cv2.drawContours(out, [box], 0, (0, 255, 0), 3)
        cx, cy = int(b["rect"][0][0]), int(b["rect"][0][1])
        cv2.putText(out, f"#{i} ar={b['elongation']:.2f}", (cx - 70, cy),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
    return out


def draw_buttons(crop: np.ndarray, buttons: list[dict],
                 show_index: bool = True) -> np.ndarray:
    out = crop.copy()
    H, W = out.shape[:2]
    for i, b in enumerate(buttons):
        x, y, w, h = b["px"]
        col = BGR.get(b.get("color", "grey"), (150, 150, 150))
        thick = 2 if b.get("color", "grey") in (
            "red", "orange", "yellow", "green", "blue", "purple") else 1
        cv2.rectangle(out, (x, y), (x + w, y + h), col, thick)
        if show_index:
            cv2.putText(out, str(i), (x + 1, y + 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.32, (255, 255, 255), 1,
                        cv2.LINE_AA)
    return out


def draw_text(crop: np.ndarray, regions: list[dict],
              suppressed: list[dict] | None = None) -> np.ndarray:
    """Text regions, plus the button detections that OCR removed.

    Suppression is the one session-2 step that can silently destroy good
    detections, so what it removed is drawn in red rather than merely counted.
    """
    out = crop.copy()
    H, W = out.shape[:2]

    for b in suppressed or []:
        x, y, w, h = b["px"]
        cv2.rectangle(out, (x, y), (x + w, y + h), (0, 0, 255), 1)
        cv2.line(out, (x, y), (x + w, y + h), (0, 0, 255), 1)

    for r in regions:
        x1 = int((r["x"] - r["w"] / 2) * W)
        y1 = int((r["y"] - r["h"] / 2) * H)
        x2 = int((r["x"] + r["w"] / 2) * W)
        y2 = int((r["y"] + r["h"] / 2) * H)
        col = (0, 255, 255) if r.get("role") == "label" else (255, 0, 255)
        cv2.rectangle(out, (x1, y1), (x2, y2), col, 1)
        cv2.putText(out, r["text"][:14], (x1, max(9, y1 - 2)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.3, col, 1, cv2.LINE_AA)
    return out


def side_by_side(images: list[np.ndarray], labels: list[str],
                 height: int = 760) -> np.ndarray:
    """Stack panels horizontally at a common height, with captions."""
    panels = []
    for img, label in zip(images, labels):
        if img.ndim == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        scale = height / img.shape[0]
        r = cv2.resize(img, (max(1, int(img.shape[1] * scale)), height))
        bar = np.full((34, r.shape[1], 3), 32, dtype=np.uint8)
        cv2.putText(bar, label, (8, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                    (255, 255, 255), 1, cv2.LINE_AA)
        panels.append(np.vstack([bar, r]))

    gap = np.full((panels[0].shape[0], 12, 3), 32, dtype=np.uint8)
    stitched = panels[0]
    for p in panels[1:]:
        stitched = np.hstack([stitched, gap, p])
    return stitched
