"""Vectorised affine RANSAC.

**Why this is not cv2.findHomography / cv2.estimateAffine2D.** Every
RANSAC-backed estimator in the installed OpenCV (4.10.0) raises SIGILL and
kills the process outright when handed 50 or more point pairs. The cutoff is
exact and deterministic -- 49 pairs is fine, 50 is a dead process -- and it is
not data-dependent, not seed-dependent, and not fixable with
`OPENCV_CPU_DISABLE`; the non-RANSAC least-squares path is unaffected. It is
OpenCV's shared RANSAC machinery hitting an instruction this CPU does not
have. See the note in CLAUDE.md.

That is fatal here rather than inconvenient: this catalog is full of remotes
with 50+ buttons (DVD_80 has 54, YDX-107 has 49), so the crash lands on
exactly the dense keypads that produce the most correspondences. Capping the
correspondence count below 50 to dodge it would be the same absolute-count
gate that has already broken this pipeline twice.

Affine rather than perspective is also the honest model. Both fingerprints
have already been rectified into canonical coordinates, so the true transform
between a query and its catalog record is near identity: the extra projective
freedom bought nothing and was being rejected as implausible anyway.
"""
from __future__ import annotations

import numpy as np


def fit_affine_lstsq(src: np.ndarray, dst: np.ndarray) -> np.ndarray | None:
    """Least-squares 2x3 affine mapping src onto dst."""
    if len(src) < 3:
        return None
    A = np.column_stack([src[:, 0], src[:, 1], np.ones(len(src))])
    try:
        sol, *_ = np.linalg.lstsq(A, dst, rcond=None)
    except np.linalg.LinAlgError:
        return None
    M = sol.T                                  # (2, 3)
    return M if np.all(np.isfinite(M)) else None


def affine_ransac(src: np.ndarray, dst: np.ndarray, thresh: float,
                  max_iters: int = 400,
                  seed: int = 12345) -> tuple[np.ndarray | None, np.ndarray]:
    """Robustly fit `dst ~= M @ [src, 1]`.

    Returns the 2x3 transform and a boolean inlier mask.

    The seed is fixed, so the same pair of fingerprints always scores the same
    number. The tuning loop here is "change a threshold, re-run, compare the
    numbers"; a verifier whose score wobbles between runs would make small
    real changes indistinguishable from sampling noise.
    """
    src = np.asarray(src, dtype=np.float64)
    dst = np.asarray(dst, dtype=np.float64)
    n = len(src)
    empty = np.zeros(n, dtype=bool)
    if n < 3:
        return None, empty

    if n == 3:
        M = fit_affine_lstsq(src, dst)
        return (M, np.ones(n, dtype=bool)) if M is not None else (None, empty)

    rng = np.random.default_rng(seed)
    iters = int(min(max_iters, max(32, n * 8)))

    # All minimal samples at once: 3 distinct point indices per iteration.
    # Sorting each row and rejecting rows with a repeat is cheaper than
    # sampling without replacement per iteration in a Python loop.
    samples = rng.integers(0, n, size=(iters, 3))
    ok = ((samples[:, 0] != samples[:, 1]) & (samples[:, 1] != samples[:, 2])
          & (samples[:, 0] != samples[:, 2]))
    samples = samples[ok]
    if len(samples) == 0:
        M = fit_affine_lstsq(src, dst)
        return (M, np.ones(n, dtype=bool)) if M is not None else (None, empty)

    s = src[samples]                                  # (k, 3, 2)
    d = dst[samples]
    A = np.concatenate([s, np.ones((len(s), 3, 1))], axis=2)   # (k, 3, 3)

    # Drop near-degenerate (collinear) samples before solving. A keypad is a
    # grid, so collinear triples are common rather than exceptional, and
    # np.linalg.solve on a singular batch raises for the whole batch.
    dets = np.linalg.det(A)
    good = np.abs(dets) > 1e-8
    A, d = A[good], d[good]
    if len(A) == 0:
        M = fit_affine_lstsq(src, dst)
        return (M, np.ones(n, dtype=bool)) if M is not None else (None, empty)

    try:
        params = np.linalg.solve(A, d)                # (k, 3, 2)
    except np.linalg.LinAlgError:
        M = fit_affine_lstsq(src, dst)
        return (M, np.ones(n, dtype=bool)) if M is not None else (None, empty)

    src_h = np.column_stack([src, np.ones(n)])        # (n, 3)
    proj = np.einsum("ij,kjm->kim", src_h, params)    # (k, n, 2)
    err = np.linalg.norm(proj - dst[None, :, :], axis=2)
    counts = (err <= thresh).sum(axis=1)

    best = int(np.argmax(counts))
    mask = err[best] <= thresh
    if mask.sum() < 3:
        return None, empty

    # Refit on the consensus set. The minimal sample fixes which points are
    # inliers; the transform itself should use all of them.
    M = fit_affine_lstsq(src[mask], dst[mask])
    if M is None:
        return None, empty

    proj_all = src_h @ M.T
    mask = np.linalg.norm(proj_all - dst, axis=1) <= thresh
    if mask.sum() < 3:
        return None, empty
    return M, mask
