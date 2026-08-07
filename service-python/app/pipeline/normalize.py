"""Warp a detected body to a canonical upright rectangle.

Every downstream coordinate is relative to this rectangle, which is what makes
fingerprints comparable between a studio catalog shot and a phone photo taken
at an angle.

Orientation is resolved here. Getting it wrong silently corrupts the
fingerprint, so when the evidence is weak we say so and let the caller index
both orientations.
"""
from __future__ import annotations

import cv2
import numpy as np

from app.config import CFG


def order_corners(pts: np.ndarray) -> np.ndarray:
    """Order 4 points as top-left, top-right, bottom-right, bottom-left."""
    pts = np.asarray(pts, dtype=np.float32)
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()
    return np.array([
        pts[np.argmin(s)],   # tl
        pts[np.argmin(d)],   # tr
        pts[np.argmax(s)],   # br
        pts[np.argmax(d)],   # bl
    ], dtype=np.float32)


def rectify(img: np.ndarray, rect) -> tuple[np.ndarray, np.ndarray, float]:
    """Warp the rotated rect to an upright crop.

    Returns (crop, homography, aspect_ratio). Orientation along the long axis
    is not yet resolved: see resolve_orientation.
    """
    cfg = CFG.normalize
    (rw, rh) = rect[1]
    long_side, short_side = max(rw, rh), min(rw, rh)
    aspect = long_side / max(short_side, 1e-6)
    aspect = min(aspect, cfg.max_aspect)

    box = cv2.boxPoints(rect).astype(np.float32)
    box = order_corners(box)

    # if the rect is landscape, roll the corners so the long axis becomes
    # vertical -- remotes are always treated as portrait
    w_top = np.linalg.norm(box[1] - box[0])
    h_left = np.linalg.norm(box[3] - box[0])
    if w_top > h_left:
        box = np.roll(box, -1, axis=0)

    W = cfg.out_width
    H = int(round(W * aspect))

    pad = cfg.pad_frac
    dst = np.array([
        [-pad * W, -pad * H],
        [W + pad * W, -pad * H],
        [W + pad * W, H + pad * H],
        [-pad * W, H + pad * H],
    ], dtype=np.float32)

    M = cv2.getPerspectiveTransform(box, dst)
    crop = cv2.warpPerspective(img, M, (W, H), flags=cv2.INTER_CUBIC,
                               borderMode=cv2.BORDER_REPLICATE)
    return crop, M, aspect


def _ink_density(gray: np.ndarray) -> float:
    """Fraction of non-background pixels -- a proxy for button density."""
    _, th = cv2.threshold(gray, 0, 255,
                          cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    return float(th.mean() / 255.0)


def _silhouette_widths(crop: np.ndarray) -> np.ndarray:
    """Foreground width per row, normalised to the crop width."""
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    border = np.concatenate([gray[:4].ravel(), gray[-4:].ravel(),
                             gray[:, :4].ravel(), gray[:, -4:].ravel()])
    bg = float(np.median(border))
    diff = cv2.absdiff(gray, np.full_like(gray, int(bg)))
    _, fg = cv2.threshold(diff, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    return (fg > 0).sum(axis=1).astype(np.float32) / max(fg.shape[1], 1)


def resolve_orientation(crop: np.ndarray,
                        buttons: list[dict] | None = None) -> dict:
    """Decide whether the crop is right way up.

    Deliberately conservative. Getting this wrong silently corrupts every
    fingerprint, and a wrong confident answer is far worse than an admitted
    unknown -- an ambiguous result tells the caller to index both orientations,
    which costs storage and nothing else.

    Signals:
      1. taper -- most remotes narrow toward the IR emitter at the top
      2. large-button position -- oversized buttons (transport controls,
         volume rockers) tend to sit below the midpoint

    Note what is NOT used: button density. Keypads sit at the top on some
    layouts (AKAI RC-51A) and the bottom on others (InFocus INL148), so
    density carries no consistent signal.
    """
    widths = _silhouette_widths(crop)
    n = len(widths)
    taper_vote, taper_margin = 0.0, 0.0
    if n >= 12:
        top = float(np.median(widths[int(0.02 * n):int(0.22 * n)]))
        bot = float(np.median(widths[int(0.78 * n):int(0.98 * n)]))
        if top > 0 and bot > 0:
            taper_margin = abs(bot - top) / max(bot + top, 1e-6)
            taper_vote = 1.0 if bot >= top else -1.0   # +1 = already upright

    size_vote, size_margin = 0.0, 0.0
    if buttons and len(buttons) >= 6:
        areas = np.array([b["w"] * b["h"] for b in buttons])
        ys = np.array([b["y"] for b in buttons])
        big = ys[areas >= np.percentile(areas, 80)]
        if len(big):
            size_vote = 1.0 if big.mean() > 0.5 else -1.0
            size_margin = min(abs(float(big.mean()) - 0.5) * 2, 1.0)

    score = 0.70 * taper_vote * taper_margin + 0.30 * size_vote * size_margin
    confidence = float(min(abs(score) * 4.0, 1.0))

    # below this, we do not trust the decision: assume upright (correct for
    # nearly all catalog imagery) and flag the crop for dual indexing
    ambiguous = confidence < 0.25
    return {
        # Sure, not merely not-unsure. See `flip_min_confidence`: leaving a
        # crop as photographed is the safe error, turning it over is not.
        "flip": bool(score < 0
                     and confidence >= CFG.normalize.flip_min_confidence),
        "ambiguous": ambiguous,
        "confidence": confidence,
        "signals": {
            "taper_vote": taper_vote,
            "taper_margin": round(taper_margin, 4),
            "size_vote": size_vote,
            "size_margin": round(size_margin, 4),
        },
    }


def apply_flip(crop: np.ndarray) -> np.ndarray:
    """Rotate 180 degrees (not a mirror -- mirroring would be wrong)."""
    return cv2.rotate(crop, cv2.ROTATE_180)


def flip_buttons(buttons: list[dict], shape: tuple[int, ...]) -> list[dict]:
    """Rotate detections 180 degrees to follow `apply_flip` on their crop.

    Detection must **not** be re-run on the flipped crop. `detect_buttons` is
    not invariant under rotation, and the reason is CLAHE: its tile grid is
    anchored to the crop's top-left corner, so rotating the pixels moves every
    tile boundary and perturbs the equalised image by up to ~18 grey levels.
    `adaptiveThreshold` itself is exactly invariant -- held to one CLAHE
    output, the two orientations agree on 100% of pixels -- so CLAHE is the
    whole of the asymmetry.

    That perturbation is not small in its effect. On MR-18B_0 body 1 it swings
    one ensemble pass from 12 detections to 27, and the final count from 6 to
    25, because the vote threshold sits on a cliff there. Re-detecting would
    therefore make a record's button set depend on a flip decision taken
    *after* detection: the same remote indexes differently upright and
    flipped, silently, which is the corruption `resolve_orientation` warns
    about one layer up.

    Rotating the detections instead is exact, and free. Colours, shapes, sizes
    and votes are all rotation-invariant, so only the centres and the pixel
    boxes move.
    """
    H, W = shape[:2]
    out = []
    for b in buttons:
        nb = dict(b)
        nb["x"] = 1.0 - b["x"]
        nb["y"] = 1.0 - b["y"]
        if "px" in b:
            x, y, w, h = b["px"]
            nb["px"] = (W - x - w, H - y - h, w, h)
        out.append(nb)
    # same ordering `detect_buttons` leaves behind, so downstream indexing by
    # position is unaffected by the flip
    out.sort(key=lambda b: (round(b["y"], 2), b["x"]))
    return out


def _text_score(regions: list[dict]) -> float:
    """How much this crop reads like the right way up.

    Recognised words dominate: printed legends read as POWER and MENU one way
    up and as noise the other, which is a far stronger signal than the
    silhouette taper. Raw confidence contributes a little, to break ties on
    crops whose only text is digits.
    """
    from app.pipeline.branding import vocab_hits

    hits = vocab_hits(regions)
    conf = sum(r["conf"] ** 2 for r in regions if len(r["text"]) >= 2)
    return 3.0 * hits + conf


def orientation_from_text(regions_upright: list[dict],
                          regions_flipped: list[dict],
                          geometric: dict) -> dict:
    """Settle orientation using OCR, falling back to the geometric answer.

    Session 1 left this open: taper is weak on parallel-sided remotes and got
    the AIWA PAS-Y600 upside down, which then made OCR read its wordmark as
    "emie". Text resolves it -- but only when the two orientations actually
    disagree by a margin. A crop with no readable text either way keeps the
    geometric verdict, including its `ambiguous` flag, rather than inventing
    an answer.
    """
    up = _text_score(regions_upright)
    down = _text_score(regions_flipped)
    total = up + down

    # Nothing readable either way: text has no opinion. Leave geometry alone.
    if total < 1.0:
        return dict(geometric, source="geometry")

    margin = abs(up - down) / total
    if margin < 0.25:
        return dict(geometric, source="geometry")

    return {
        "flip": bool(down > up),
        "ambiguous": False,
        "confidence": round(float(min(0.5 + margin, 1.0)), 3),
        "source": "text",
        "signals": dict(geometric.get("signals", {}),
                        text_up=round(up, 2), text_flipped=round(down, 2)),
    }
