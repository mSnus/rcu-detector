"""Token generation from a fingerprint (plan 4.1).

The same code path runs at catalog-build time and at query time. If these ever
diverge, retrieval degrades silently and the index looks fine -- so everything
here is a pure function of the fingerprint document and CFG.token.

Two families:

  A. Grid tokens -- button position quantised into the rectified body's
     coordinate grid, with colour, size and label. Primary signal, and valid
     only because rectification already put every record in canonical
     coordinates.
  B. Triplet invariants -- ratios and angles among neighbouring buttons.
     These survive an affine error in body detection (a hand over one edge,
     a photo taken at an angle) that shifts every grid coordinate at once.

Hashes must be stable across processes and across restarts, so this uses
blake2b and never Python's `hash()`, which is salted per interpreter.
"""
from __future__ import annotations

import hashlib
import itertools
import re
from typing import Iterable

import numpy as np

from app.config import CFG

_NON_ALNUM = re.compile(r"[^A-Z0-9]+")
_MASK63 = (1 << 63) - 1


def hash64(parts: Iterable) -> int:
    """Stable 63-bit hash of a tuple of primitives.

    Kept to 63 bits so the value is always a non-negative int64: the index
    stores token ids in an int64 array and sorts them, and a sign flip there
    would scramble the binary search.
    """
    raw = "\x1f".join(str(p) for p in parts).encode("utf-8")
    return int.from_bytes(hashlib.blake2b(raw, digest_size=8).digest(),
                          "big") & _MASK63


def norm_label(text: str | None) -> str | None:
    """Canonical form of a button legend for token purposes.

    Case and punctuation vary between engines and between a studio shot and a
    phone photo of the same key ("VOL+", "vol +", "VOL -"). Strip to letters
    and digits. Deliberately no synonym table: an aggressive mapping would
    make distinct keys collide, and IDF already discounts the legends that
    appear on everything.
    """
    if not text:
        return None
    t = _NON_ALNUM.sub("", text.upper())
    return t or None


def _size_bucket(w: float) -> int:
    edges = CFG.token.size_edges
    for i, e in enumerate(edges):
        if w < e:
            return i
    return len(edges)


def _cells(v: float, g: int) -> list[int]:
    """Grid cells a normalised coordinate belongs to.

    Returns the containing cell, plus its neighbour when the value sits close
    to a boundary. Without this a button a hair over a cell edge produces a
    different token in the query than in the catalog and contributes nothing
    at all -- the failure is total rather than graceful.
    """
    edge = CFG.token.grid_edge_frac
    pos = min(max(v, 0.0), 0.999999) * g
    c = int(pos)
    frac = pos - c
    out = [c]
    if frac < edge and c > 0:
        out.append(c - 1)
    elif frac > 1.0 - edge and c < g - 1:
        out.append(c + 1)
    return out


def grid_tokens(fp: dict) -> list[int]:
    """Family A. Position-quantised button tokens."""
    cfg = CFG.token
    toks: list[int] = []

    for b in fp.get("buttons", []):
        cxs = _cells(float(b["x"]), cfg.grid_x)
        cys = _cells(float(b["y"]), cfg.grid_y)
        color = b.get("color", "grey")
        size = _size_bucket(float(b.get("w", 0.0)))
        label = norm_label(b.get("label"))

        for cx, cy in itertools.product(cxs, cys):
            toks.append(hash64(("G", cx, cy, color, size)))
            # Degraded variants. Each drops one attribute, so a single bad
            # colour reading or a size on a bucket boundary costs recall
            # instead of erasing the button. They are common tokens and IDF
            # weights them down on its own.
            if cfg.emit_size_agnostic:
                toks.append(hash64(("Gc", cx, cy, color)))
            if cfg.emit_color_agnostic:
                toks.append(hash64(("Gs", cx, cy, size)))
            if label:
                toks.append(hash64(("L", cx, cy, label)))

        if label and cfg.emit_label_text:
            toks.append(hash64(("LT", label)))

    return toks


def triplet_tokens(fp: dict) -> list[int]:
    """Family B. Affine-invariant triplets over neighbouring buttons.

    Encodes only a distance ratio and an angle, so it is unchanged by
    translation, rotation, scale and (approximately) shear -- exactly the
    errors a shaky body detection introduces.
    """
    cfg = CFG.token
    buttons = fp.get("buttons", [])
    n = len(buttons)
    if n < 3 or n > cfg.triplet_max_buttons:
        return []

    pts = np.array([[float(b["x"]), float(b["y"])] for b in buttons],
                   dtype=np.float64)
    cols = [b.get("color", "grey") for b in buttons]

    # cKDTree would be the plan's choice, but scipy is a heavy dependency for
    # a neighbour query over at most 80 points; the brute-force distance
    # matrix is smaller and faster at this size.
    d = np.linalg.norm(pts[:, None, :] - pts[None, :, :], axis=-1)
    k = min(cfg.triplet_k, n - 1)
    order = np.argsort(d, axis=1)

    toks: list[int] = []
    for i in range(n):
        neigh = [j for j in order[i][1:k + 1] if j != i]
        for a, b in itertools.combinations(neigh, 2):
            va, vb = pts[a] - pts[i], pts[b] - pts[i]
            na, nb = float(np.linalg.norm(va)), float(np.linalg.norm(vb))
            if na < 1e-9 or nb < 1e-9:
                continue
            ratio = round(min(na, nb) / max(na, nb), cfg.triplet_ratio_places)
            cosang = float(np.dot(va, vb) / (na * nb))
            ang = np.degrees(np.arccos(min(max(cosang, -1.0), 1.0)))
            angb = int(round(ang / cfg.triplet_angle_bucket))
            # The two outer colours are sorted so the token does not depend on
            # which of the pair was visited first.
            toks.append(hash64(("T", ratio, angb, cols[i],
                                *sorted([cols[a], cols[b]]))))
    return toks


def fingerprint_tokens(fp: dict) -> np.ndarray:
    """All tokens for one fingerprint, deduplicated.

    Duplicates are dropped rather than counted: a term-frequency weighting
    would reward a remote for having six identical grey keys in one grid cell,
    which is a property of the layout being dense, not of the match being good.
    """
    toks = grid_tokens(fp)
    if CFG.token.triplets:
        toks.extend(triplet_tokens(fp))
    if not toks:
        return np.zeros(0, dtype=np.int64)
    return np.unique(np.asarray(toks, dtype=np.int64))


def flip_fingerprint(fp: dict) -> dict:
    """The same fingerprint rotated 180 degrees.

    Used to index an orientation-ambiguous record both ways up, and to try a
    query both ways up. Rotation, not mirroring: a remote photographed upside
    down is rotated in the plane, so x AND y both invert. Mirroring it would
    match a remote that does not exist.
    """
    out = dict(fp)
    out["buttons"] = [
        {**b,
         "x": round(1.0 - float(b["x"]), 4),
         "y": round(1.0 - float(b["y"]), 4),
         "label_pos": _flip_pos(b.get("label_pos"))}
        for b in fp.get("buttons", [])
    ]
    out["text_regions"] = [
        {**r,
         "x": round(1.0 - float(r["x"]), 4),
         "y": round(1.0 - float(r["y"]), 4)}
        for r in fp.get("text_regions", [])
    ]
    stats = dict(fp.get("stats", {}))
    stats["orientation_flipped"] = not stats.get("orientation_flipped", False)
    out["stats"] = stats
    return out


_POS_FLIP = {"above": "below", "below": "above",
             "left": "right", "right": "left", "on": "on"}


def _flip_pos(pos: str | None) -> str | None:
    return _POS_FLIP.get(pos) if pos else None
