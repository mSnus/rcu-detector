"""Locate the remote body (or bodies) within an image.

Catalog images are typically studio shots on a light background, often with
more than one remote per image (colour variants of the same model). Query
images from a phone are messier but the same code path handles both; the
background estimate adapts.
"""
from __future__ import annotations

import cv2
import numpy as np

from app.config import CFG


def _background_is_light(img: np.ndarray, border: int) -> bool:
    """Sample the image border to decide whether the background is light."""
    strips = [
        img[:border, :], img[-border:, :],
        img[:, :border], img[:, -border:],
    ]
    vals = [s.reshape(-1, s.shape[-1]).mean() for s in strips if s.size]
    return float(np.mean(vals)) > 127.0


def _foreground_mask_otsu(img: np.ndarray) -> np.ndarray:
    """Greyscale Otsu mask. Separates adjacent remotes more readily, but
    fails when the background is a mid-grey gradient."""
    cfg = CFG.body
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    light_bg = _background_is_light(img, cfg.border_px)
    flag = cv2.THRESH_BINARY_INV if light_bg else cv2.THRESH_BINARY
    _, mask = cv2.threshold(blur, 0, 255, flag | cv2.THRESH_OTSU)
    return _clean(mask, img)


def _clean(mask: np.ndarray, img: np.ndarray) -> np.ndarray:
    cfg = CFG.body
    k = max(9, int(min(img.shape[:2]) * cfg.close_frac) | 1)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (k, k))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    return cv2.morphologyEx(mask, cv2.MORPH_OPEN,
                            cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)))


def _foreground_mask(img: np.ndarray) -> np.ndarray:
    """Binary mask where 255 = candidate foreground (the remote).

    Segments by colour distance from the estimated background rather than by
    global greyscale Otsu. Catalog backgrounds are frequently a light grey
    gradient rather than pure white, and Otsu on greyscale merges a dark
    remote with a mid-grey background into one blob covering the whole frame.
    """
    cfg = CFG.body
    blur = cv2.GaussianBlur(img, (5, 5), 0)
    lab = cv2.cvtColor(blur, cv2.COLOR_BGR2LAB).astype(np.float32)

    b = cfg.border_px
    border = np.concatenate([
        lab[:b, :].reshape(-1, 3), lab[-b:, :].reshape(-1, 3),
        lab[:, :b].reshape(-1, 3), lab[:, -b:].reshape(-1, 3),
    ])
    bg = np.median(border, axis=0)

    dist = np.linalg.norm(lab - bg, axis=2)
    dist = np.clip(dist / max(dist.max(), 1e-6) * 255, 0, 255).astype(np.uint8)
    _, mask = cv2.threshold(dist, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)

    return _clean(mask, img)


def _touches_edges(rect, shape, tol: int = 3) -> int:
    """How many image edges this rotated rect touches."""
    box = cv2.boxPoints(rect)
    h, w = shape[:2]
    n = 0
    if box[:, 0].min() <= tol:
        n += 1
    if box[:, 1].min() <= tol:
        n += 1
    if box[:, 0].max() >= w - tol:
        n += 1
    if box[:, 1].max() >= h - tol:
        n += 1
    return n


def _split_merged(mask: np.ndarray, rect, img_shape) -> list[tuple] | None:
    """Split a body that is really several remotes bridged together.

    Catalog images often place two colour variants side by side, and a
    watermark laid across both connects them into one blob. A vertical
    projection of the mask shows deep valleys in the gaps.

    Returns a list of rects, or None if no confident split was found.
    """
    box = cv2.boxPoints(rect).astype(int)
    x0, y0 = box[:, 0].min(), box[:, 1].min()
    x1, y1 = box[:, 0].max(), box[:, 1].max()
    x0, y0 = max(0, x0), max(0, y0)
    x1 = min(mask.shape[1], x1)
    y1 = min(mask.shape[0], y1)
    if x1 - x0 < 20 or y1 - y0 < 20:
        return None

    sub = mask[y0:y1, x0:x1]
    col = (sub > 0).sum(axis=0).astype(np.float32)
    if col.max() <= 0:
        return None

    # smooth so button gaps and JPEG noise do not register as valleys
    k = max(3, (sub.shape[1] // 40) | 1)
    col = cv2.GaussianBlur(col.reshape(1, -1), (k, 1), 0).ravel()

    height = float(sub.shape[0])
    valley = col < 0.25 * height          # column is mostly background
    if not valley.any():
        return None

    # find contiguous valley runs, ignoring the outer margins
    runs, start = [], None
    for i, v in enumerate(valley):
        if v and start is None:
            start = i
        elif not v and start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, len(valley)))

    inner = [r for r in runs
             if r[0] > 0.10 * sub.shape[1] and r[1] < 0.90 * sub.shape[1]
             and (r[1] - r[0]) > 0.02 * sub.shape[1]]
    if not inner:
        return None

    cuts = [int((a + b) / 2) for a, b in inner]
    bounds = [0] + cuts + [sub.shape[1]]
    out = []
    for a, b in zip(bounds[:-1], bounds[1:]):
        seg = sub[:, a:b]
        cols_on = (seg > 0).sum(axis=0)
        occupied = np.where(cols_on > 0.15 * height)[0]
        if len(occupied) < 10:
            continue
        sx0 = x0 + a + int(occupied.min())
        sx1 = x0 + a + int(occupied.max())
        rows_on = (seg > 0).sum(axis=1)
        occ_r = np.where(rows_on > 0.10 * seg.shape[1])[0]
        if len(occ_r) < 10:
            continue
        sy0, sy1 = y0 + int(occ_r.min()), y0 + int(occ_r.max())
        w, h = sx1 - sx0, sy1 - sy0
        if w < 10 or h < 10:
            continue
        if not (CFG.body.min_elongation <= max(w, h) / min(w, h)
                <= CFG.body.max_elongation):
            continue
        # floats, explicitly: these bounds come from numpy reductions, and
        # cv2.boxPoints rejects a rect whose size is a numpy integer
        out.append((((sx0 + sx1) / 2.0, (sy0 + sy1) / 2.0),
                    (float(w), float(h)), 0.0))

    return out if len(out) >= 2 else None


def _bodies_from_mask(img: np.ndarray, mask: np.ndarray) -> list[dict]:
    """Return candidate remote bodies, ordered left to right.

    Each result: {rect, area_frac, elongation, fill, contour}
    """
    cfg = CFG.body
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)

    img_area = float(img.shape[0] * img.shape[1])
    out: list[dict] = []

    for c in contours:
        area = cv2.contourArea(c)
        if area < cfg.min_area_frac * img_area:
            continue

        rect = cv2.minAreaRect(c)
        (rw, rh) = rect[1]
        if min(rw, rh) < 1:
            continue

        elong = max(rw, rh) / min(rw, rh)
        fill = area / (rw * rh)

        # Only reject as "the frame itself" when the blob touches most edges,
        # covers most of the image AND fills its own bounding rect. Catalog
        # crops are often tight, so a real remote routinely touches top and
        # bottom and can cover 80%+ of the frame -- but its rounded corners
        # keep the fill well under a solid rectangle's.
        if (cfg.reject_edge_touching
                and _touches_edges(rect, img.shape) >= 3
                and area / img_area > cfg.frame_area_frac
                and (fill >= cfg.frame_min_fill
                     or elong < cfg.frame_min_elongation)):
            continue

        # Always probe for a split. Several remotes bridged by a watermark,
        # a shadow, or simple proximity can still produce a blob whose
        # elongation and fill look perfectly reasonable, so gating the probe
        # on those numbers misses exactly the cases it was meant to catch.
        # The probe is cheap and only fires on a genuinely deep valley.
        parts = _split_merged(mask, rect, img.shape)
        if parts:
            if True:
                for p in parts:
                    (pw, ph) = p[1]
                    out.append({
                        "rect": p,
                        "area_frac": (pw * ph) / img_area,
                        "elongation": max(pw, ph) / min(pw, ph),
                        "fill": 1.0,
                        "contour": c,
                        "split": True,
                    })
                continue

        if not (cfg.min_elongation <= elong <= cfg.max_elongation):
            continue
        if fill < 0.55:
            continue

        out.append({
            "rect": rect,
            "area_frac": area / img_area,
            "elongation": elong,
            "fill": fill,
            "contour": c,
            "split": False,
        })

    # left to right, so crop_index is stable across re-runs
    out.sort(key=lambda d: d["rect"][0][0])
    return out


def detect_bodies(img: np.ndarray) -> list[dict]:
    """Bodies only. See `detect_bodies_with_mask` for the mask as well."""
    return detect_bodies_with_mask(img)[0]


def detect_bodies_with_mask(img: np.ndarray) -> tuple[list[dict], np.ndarray]:
    """Detect remote bodies using two segmentation strategies.

    Neither strategy wins everywhere. Lab colour-distance handles gradient and
    tinted backgrounds that defeat greyscale Otsu; greyscale Otsu keeps
    adjacent remotes separate where a watermark laid across both bridges them
    in Lab space. Running both costs a few milliseconds and we keep whichever
    yields the more plausible result.
    """
    cfg_frame_max = CFG.body.single_body_max_area_frac
    lab_mask = _foreground_mask(img)
    otsu_mask = _foreground_mask_otsu(img)

    lab_bodies = _bodies_from_mask(img, lab_mask)
    otsu_bodies = _bodies_from_mask(img, otsu_mask)

    def plausibility(bodies: list[dict]) -> tuple:
        if not bodies:
            return (0, 0.0)
        # A single body covering most of the frame means the segmentation
        # swallowed the background, not that it found a large remote. Judge on
        # the largest single body, never on the total: two genuine remotes
        # legitimately sum to a high coverage.
        if max(b["area_frac"] for b in bodies) > cfg_frame_max:
            return (0, 0.0)
        return (len(bodies), sum(b["area_frac"] for b in bodies))

    otsu_won = plausibility(otsu_bodies) > plausibility(lab_bodies)
    bodies = otsu_bodies if otsu_won else lab_bodies
    # The mask that was actually used, not always the Lab one. The debug panel
    # is the project's main diagnostic and it showed the Lab mask regardless of
    # which strategy won, so on an image where Otsu won it displayed a
    # segmentation the pipeline had rejected.
    mask = otsu_mask if otsu_won else lab_mask

    if not bodies and CFG.body.full_frame_fallback:
        bodies = _full_frame_body(img)
    return bodies, mask


def _full_frame_body(img: np.ndarray) -> list[dict]:
    """Treat the whole frame as the body, for images with no visible background.

    Both segmentation strategies estimate the background from the image border.
    When the product is cropped flush to the edges there is no background to
    sample, so the estimate lands on the remote itself and the mask comes out
    inverted -- foreground becomes whatever contrasts with the remote, which is
    typically just its printed wordmark.

    The only sane reading of such an image is that it is *all* remote. Applied
    only when the frame is itself remote-shaped, so a square thumbnail or a
    banner does not silently become a body.
    """
    cfg = CFG.body
    h, w = img.shape[:2]
    if min(w, h) < 1:
        return []
    elong = max(w, h) / min(w, h)
    if not (cfg.min_elongation <= elong <= cfg.max_elongation):
        return []

    inset = cfg.full_frame_inset
    rect = ((w / 2.0, h / 2.0),
            (float(w * (1 - 2 * inset)), float(h * (1 - 2 * inset))), 0.0)
    return [{
        "rect": rect,
        "area_frac": (1 - 2 * inset) ** 2,
        "elongation": elong,
        "fill": 1.0,
        "contour": None,
        "split": False,
        "full_frame": True,      # surfaced by the audit as a lower-trust body
    }]
