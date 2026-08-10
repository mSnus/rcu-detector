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
            # The area floor applies to the pieces too. It was checked on the
            # blob before splitting and never on what came out, so a split
            # could emit bodies far below a threshold the same function had
            # just enforced -- Sherwood TX-757 produced pieces of 0.32%, 1.21%
            # and 0.94% against a 2% floor, each of which then extracted 120
            # to 150 phantom buttons.
            #
            # It also decided which mask won. Plausibility ranks by body count
            # first, so the fragmenting Lab mask (4 bodies) beat the Otsu one
            # (2 bodies) that had actually found both remotes correctly.
            kept = [p for p in parts
                    if (p[1][0] * p[1][1]) / img_area >= cfg.min_area_frac]
            if kept:
                for p in kept:
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

    if CFG.body.full_frame_fallback:
        # Nothing found at all, or one fragment small enough that the mask
        # cannot have been separating body from background. See
        # `min_plausible_area_frac` -- the second case is the common one and
        # used to sail through, because a fragment is still a body.
        # The bodies *together* must explain a plausible share of the frame.
        # Stated over the total rather than over a single body because the same
        # failure fragments into several pieces just as readily: the Elenberg
        # DVDP-2417's mask kept only its keycaps, and the pieces came back as a
        # strip down the left keypad column (2.5% of frame) plus a sliver at
        # the bottom. Neither is a remote; together they are 8% of a frame that
        # is entirely remote.
        #
        # Deliberately the same floor as the single-body case, and deliberately
        # not higher. Total coverage across the 1701 multi-body images is
        # bimodal -- p25 0.12, median 0.32, p75 0.67 -- and it is tempting to
        # cut at the median. But the 876 images below 0.35 average quality
        # 0.732 against 0.803 above it, which is not the signature of a
        # population that is broken; only the deep tail is. Two remotes side by
        # side fill the frame, and fusing them into one fingerprint is a bug
        # this project has already had (RM-L859-1).
        # Count first. A mask that fragmented along the rows of a keypad gives
        # strips that are individually the right size and shape and together
        # cover the remote, so neither the area floor nor the span test
        # objects -- only the count does. See `max_plausible_bodies`.
        implausible = len(bodies) > CFG.body.max_plausible_bodies

        # A pair is accepted only on strong evidence, because two remotes side
        # by side and one remote split down the middle look alike by count.
        # Both halves of a genuine pair are substantial objects and the two
        # together fill the frame; a split does neither.
        if not implausible and len(bodies) == 2:
            each = min(b["area_frac"] for b in bodies)
            pts = np.concatenate([cv2.boxPoints(b["rect"]) for b in bodies])
            span = ((pts[:, 0].max() - pts[:, 0].min())
                    * (pts[:, 1].max() - pts[:, 1].min())) / float(
                        img.shape[0] * img.shape[1])
            implausible = (each < CFG.body.pair_min_each_area_frac
                           or span < CFG.body.min_bodies_span_frac)

        if not implausible:
            implausible = (sum(b["area_frac"] for b in bodies)
                           < CFG.body.min_plausible_area_frac)

        # Several bodies that do not span the frame are pieces of one remote,
        # not several remotes. See `min_bodies_span_frac`.
        if not implausible and len(bodies) > 1:
            pts = np.concatenate([cv2.boxPoints(b["rect"]) for b in bodies])
            span = ((pts[:, 0].max() - pts[:, 0].min())
                    * (pts[:, 1].max() - pts[:, 1].min()))
            implausible = span / float(img.shape[0] * img.shape[1]) < CFG.body.min_bodies_span_frac
        if not bodies or implausible:
            # A frame too squat to be one remote is usually several of them
            # side by side, and taking it whole fuses them into a single
            # fingerprint: `STV-22LED5-org` stored one 104-button "remote"
            # that was a black and a white SHIVAKI photographed together, and
            # `RM-L810` the same. `frame_min_elongation` already encodes the
            # discriminator -- a genuine tight crop of one remote is far more
            # elongated than a montage of two -- but it only guarded the
            # contour path, and this fallback walked round it.
            #
            # Cut before accepting whole, never instead of it: on 323 of the
            # 485 squat frames measured there is no valley to cut on, and
            # those keep exactly today's behaviour. 162 split, 149 in two.
            pieces = []
            if _frame_elongation(img) < CFG.body.frame_min_elongation:
                for cand_mask in (otsu_mask, lab_mask):
                    cut = _split_frame(img, cand_mask)
                    # Same order as `plausibility`: more bodies first, then
                    # more of the frame explained. It is what separates a real
                    # pair from a sliver shaved off one remote -- on
                    # `22LE3110-1` one mask cuts 0.36 + 0.46 of the frame and
                    # the other 0.39 + a 0.21 splinter at aspect 8.6.
                    if (len(cut), sum(b["area_frac"] for b in cut)) > (
                            len(pieces), sum(b["area_frac"] for b in pieces)):
                        pieces, mask = cut, cand_mask
            if pieces:
                bodies = pieces
            else:
                whole = _full_frame_body(img)
                # _full_frame_body applies its own guard: it returns nothing
                # unless the frame is itself remote-shaped. When it declines,
                # keep what we had -- a poor body beats no body, and this is
                # exactly the check that stops a square thumbnail or a banner
                # becoming one.
                if whole:
                    bodies = whole
    return bodies, mask


def _frame_elongation(img: np.ndarray) -> float:
    h, w = img.shape[:2]
    return max(w, h) / min(w, h) if min(w, h) else 0.0


def _split_frame(img: np.ndarray, mask: np.ndarray) -> list[dict]:
    """Cut the whole frame into remote-shaped pieces, or return nothing.

    The last resort before `_full_frame_body`, for the case that fallback
    cannot express: several remotes photographed side by side against a
    background their silhouettes do not segment from. What survives in the
    mask is only the keycaps, so every contour is far below `min_area_frac`
    and the split probe in `_bodies_from_mask` never runs -- but the keycaps
    still fall into columns with a clean gap between them, which is exactly
    what `_split_merged` reads.

    Each piece must clear `pair_min_each_area_frac`, the same bar a genuine
    pair of bodies has to clear, so a splinter shaved off one remote is not
    mistaken for a second one.
    """
    h, w = img.shape[:2]
    rect = ((w / 2.0, h / 2.0), (float(w), float(h)), 0.0)
    parts = _split_merged(mask, rect, img.shape)
    if not parts:
        return []

    img_area = float(h * w)
    out = []
    for p in parts:
        (pw, ph) = p[1]
        area_frac = (pw * ph) / img_area
        if area_frac < CFG.body.pair_min_each_area_frac:
            continue
        out.append({
            "rect": p,
            "area_frac": area_frac,
            "elongation": max(pw, ph) / min(pw, ph),
            "fill": 1.0,
            "contour": None,
            "split": True,
            "frame_split": True,   # surfaced by the audit as a lower-trust body
        })
    # Two or it is not a montage. One surviving piece means the cut found a
    # remote and a splinter, which is what the full-frame body already says
    # better.
    return out if len(out) >= 2 else []


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
