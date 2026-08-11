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

    src_h = np.column_stack([src, np.ones(n)])        # (n, 3)

    # Evaluate the samples in blocks, keeping the best consensus seen so far,
    # and stop when the standard adaptive bound says more sampling cannot
    # plausibly improve on it.
    #
    # Blocks rather than one iteration at a time because the whole point of
    # this implementation is that it is vectorised: a Python loop per sample
    # would cost more than the sampling it saves. And a *prefix* of the same
    # seeded draw rather than a different draw, so the early-exit run and the
    # full run agree exactly whenever the exit does not trigger -- the fixed
    # seed exists so scores never wobble, and this must not undo it.
    from app.config import CFG                        # local: keeps this
    cfg = CFG.verify                                  # module importable alone
    block = max(1, cfg.ransac_block) if cfg.ransac_adaptive else len(samples)
    conf = min(max(cfg.ransac_confidence, 0.0), 1.0 - 1e-12)

    best_count = -1
    best_err = None
    for start in range(0, len(samples), block):
        chunk = samples[start:start + block]
        s = src[chunk]                                # (k, 3, 2)
        d = dst[chunk]
        A = np.concatenate([s, np.ones((len(s), 3, 1))], axis=2)

        # Drop near-degenerate (collinear) samples before solving. A keypad is
        # a grid, so collinear triples are common rather than exceptional, and
        # np.linalg.solve on a singular batch raises for the whole batch.
        good = np.abs(np.linalg.det(A)) > 1e-8
        A, d = A[good], d[good]
        if len(A) == 0:
            continue

        try:
            params = np.linalg.solve(A, d)            # (k, 3, 2)
        except np.linalg.LinAlgError:
            continue

        proj = np.einsum("ij,kjm->kim", src_h, params)     # (k, n, 2)
        err = np.linalg.norm(proj - dst[None, :, :], axis=2)
        counts = (err <= thresh).sum(axis=1)

        i = int(np.argmax(counts))
        if counts[i] > best_count:
            best_count = int(counts[i])
            best_err = err[i]

        if not cfg.ransac_adaptive or best_count < 3:
            continue
        # Standard bound: with an inlier ratio w, the chance that a minimal
        # sample of 3 is all-inlier is w^3, so the iterations needed for
        # `conf` certainty of having drawn one is log(1-conf)/log(1-w^3).
        w = best_count / n
        if w >= 1.0:
            break
        denom = np.log1p(-w ** 3)
        if denom >= 0:
            continue
        if (start + block) >= np.log1p(-conf) / denom:
            break

    if best_err is None:
        M = fit_affine_lstsq(src, dst)
        return (M, np.ones(n, dtype=bool)) if M is not None else (None, empty)

    mask = best_err <= thresh
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
