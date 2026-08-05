"""Tier 2: RANSAC geometric verification (plan 4.4).

Tier 1 answers "which records share rare features with this query". It cannot
tell a real match from a record that happens to share a scattering of tokens,
because a bag of tokens has no notion of arrangement. Tier 2 asks the question
that actually decides it: is there a single plausible transform under which
the query's buttons land on the candidate's?

Rules that matter here, all of them learned the hard way elsewhere in this
pipeline:

- **Labels are a bonus, never a requirement.** OCR recall on a black remote
  under tungsten light is poor. Anything that gates a match on label agreement
  takes recall down with it.
- **Penalise unmatched candidate buttons.** A 40-button candidate that
  explains all 12 of a sparse query's buttons is not a good match; coverage is
  measured against the larger of the two.
- **Reject implausible transforms.** Both fingerprints are already rectified,
  so the true transform is close to identity. A homography free to warp will
  otherwise find inliers in noise.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.config import CFG
from app.matching.ransac import affine_ransac
from app.matching.tokens import norm_label, flip_fingerprint


@dataclass
class VerifyResult:
    score: float = 0.0            # 0..1, coverage plus label bonus
    inliers: int = 0
    coverage: float = 0.0
    label_bonus: float = 0.0
    correspondences: int = 0
    flipped_query: bool = False   # the query orientation that won
    reason: str = ""              # why it scored zero, for the audit
    transform: list | None = field(default=None, repr=False)


def _size_compatible(qb: dict, cb: dict) -> bool:
    cfg = CFG.verify
    qa = max(float(qb["w"]) * float(qb["h"]), 1e-9)
    ca = max(float(cb["w"]) * float(cb["h"]), 1e-9)
    ratio = max(qa / ca, ca / qa)
    return ratio <= cfg.max_size_ratio ** 2


def _pair_cost(qb: dict, cb: dict) -> float | None:
    """Cost of pairing two buttons, or None if they cannot be paired.

    Colour disagreement is a cost, not a veto. White balance between a studio
    catalog shot and a phone photo moves colours around freely, and the whole
    reason colours are bucketed coarsely is that the fine distinction does not
    survive; refusing to pair on it would throw away the position evidence
    too.
    """
    cfg = CFG.verify
    dx = float(qb["x"]) - float(cb["x"])
    dy = float(qb["y"]) - float(cb["y"])
    dist = float(np.hypot(dx, dy))
    if dist > cfg.max_pair_dist or not _size_compatible(qb, cb):
        return None
    cost = dist
    if qb.get("color", "grey") != cb.get("color", "grey"):
        cost += cfg.color_mismatch_cost
    return cost


def _correspondences(q_buttons: list[dict],
                     c_buttons: list[dict]) -> tuple[np.ndarray, np.ndarray,
                                                     list[tuple[int, int]]]:
    """One-to-one button pairing, greedily by ascending cost.

    Not simply "nearest candidate for each query button": that lets several
    query buttons claim the same candidate key, which inflates the
    correspondence count with duplicates and gives RANSAC a degenerate set to
    fit. A one-to-one constraint costs a sort over at most a few thousand
    pairs and makes the inlier count mean what it says.
    """
    # The cost matrix, in numpy rather than a Python double loop. This is the
    # hot spot of the whole query: _pair_cost was called 320,058 times for one
    # 8262-record match -- 156 candidates, each verified both ways up, each a
    # few thousand button pairs -- and accounted for 1.0 s of the 1.3 s the
    # match took. `_pair_cost` and `_size_compatible` are kept as the readable
    # statement of the rule and as the reference the equality check compares
    # against; they are no longer on the hot path.
    #
    # The ordering must match the old `pairs.sort()` exactly, ties included, or
    # a different one-to-one assignment comes out of the greedy loop below and
    # inlier counts move for reasons no one can trace. sort() on (cost, i, j)
    # breaks ties by i then j, which is np.lexsort with cost as the last key.
    cfg = CFG.verify
    if not q_buttons or not c_buttons:
        pairs = []
    else:
        q = np.array([[float(b["x"]), float(b["y"]), float(b["w"]), float(b["h"])]
                      for b in q_buttons], dtype=np.float64)
        c = np.array([[float(b["x"]), float(b["y"]), float(b["w"]), float(b["h"])]
                      for b in c_buttons], dtype=np.float64)

        dist = np.hypot(q[:, None, 0] - c[None, :, 0],
                        q[:, None, 1] - c[None, :, 1])

        qa = np.maximum(q[:, 2] * q[:, 3], 1e-9)
        ca = np.maximum(c[:, 2] * c[:, 3], 1e-9)
        ratio = np.maximum(qa[:, None] / ca[None, :], ca[None, :] / qa[:, None])

        ok = (dist <= cfg.max_pair_dist) & (ratio <= cfg.max_size_ratio ** 2)

        q_col = np.array([b.get("color", "grey") for b in q_buttons], dtype=object)
        c_col = np.array([b.get("color", "grey") for b in c_buttons], dtype=object)
        cost = dist + np.where(q_col[:, None] != c_col[None, :],
                               cfg.color_mismatch_cost, 0.0)

        ii, jj = np.nonzero(ok)
        cc = cost[ii, jj]
        order = np.lexsort((jj, ii, cc))
        pairs = list(zip(cc[order], ii[order], jj[order]))

    used_q: set[int] = set()
    used_c: set[int] = set()
    src, dst, idx = [], [], []
    for _, i, j in pairs:
        if i in used_q or j in used_c:
            continue
        used_q.add(i)
        used_c.add(j)
        src.append([float(q_buttons[i]["x"]), float(q_buttons[i]["y"])])
        dst.append([float(c_buttons[j]["x"]), float(c_buttons[j]["y"])])
        idx.append((i, j))
    return (np.asarray(src, dtype=np.float32),
            np.asarray(dst, dtype=np.float32), idx)


def _transform_plausible(M: np.ndarray) -> tuple[bool, str]:
    """Reject transforms a rectified pair could not legitimately produce.

    Both sides went through the same rectification, so the honest transform
    between them is close to identity: modest scale and little shear. Without
    this, RANSAC will happily fold the plane to explain a random scattering of
    points and report a healthy inlier count for two unrelated remotes.
    """
    cfg = CFG.verify
    if M is None or not np.all(np.isfinite(M)):
        return False, "no transform"

    a = M[:2, :2]
    det = float(np.linalg.det(a))
    if abs(det) < 1e-9:
        return False, "degenerate transform"
    if abs(np.log(abs(det))) / 2.0 > cfg.max_scale_dev:
        return False, f"implausible scale ({np.sqrt(abs(det)):.2f}x)"

    # shear: how far the transformed axes are from perpendicular
    c0, c1 = a[:, 0], a[:, 1]
    n0, n1 = np.linalg.norm(c0), np.linalg.norm(c1)
    if n0 < 1e-9 or n1 < 1e-9:
        return False, "degenerate transform"
    shear = abs(float(np.dot(c0, c1)) / (n0 * n1))
    if shear > cfg.max_shear:
        return False, f"implausible shear ({shear:.2f})"
    return True, ""


def _label_agreement(q_buttons: list[dict], c_buttons: list[dict],
                     idx: list[tuple[int, int]], mask: np.ndarray) -> float:
    """0..max_label_bonus. Agreement among inlier pairs that both carry text.

    Measured only over pairs where BOTH sides read a label. Counting a missing
    label as disagreement would quietly turn this into the label requirement
    the plan forbids, since the query side is usually missing most of them.
    """
    agree = comparable = 0
    for k, (i, j) in enumerate(idx):
        if not mask[k]:
            continue
        ql, cl = norm_label(q_buttons[i].get("label")), \
            norm_label(c_buttons[j].get("label"))
        if not ql or not cl:
            continue
        comparable += 1
        if ql == cl:
            agree += 1
    if comparable == 0:
        return 0.0
    cfg = CFG.verify
    evidence = min(comparable / max(cfg.label_full_evidence, 1), 1.0)
    return cfg.max_label_bonus * (agree / comparable) * evidence


def verify(q_fp: dict, c_fp: dict) -> VerifyResult:
    """Geometrically verify one query against one candidate, as given."""
    cfg = CFG.verify
    qb, cb = q_fp.get("buttons", []), c_fp.get("buttons", [])
    if not qb or not cb:
        return VerifyResult(reason="no buttons")

    src, dst, idx = _correspondences(qb, cb)
    if len(src) < cfg.min_correspondences:
        return VerifyResult(correspondences=len(src),
                            reason=f"only {len(src)} correspondences")

    M, mask = affine_ransac(src, dst, cfg.ransac_reproj,
                            max_iters=cfg.ransac_iters, seed=cfg.ransac_seed)
    if M is None:
        return VerifyResult(correspondences=len(src), reason="no transform")

    ok, why = _transform_plausible(M)
    if not ok:
        return VerifyResult(correspondences=len(src), reason=why)

    inliers = int(mask.sum())

    # Against the LARGER side, so a dense candidate cannot win by explaining
    # a sparse query completely.
    denom = max(len(qb), len(cb)) * cfg.asymmetry_weight
    coverage = inliers / denom if denom else 0.0
    bonus = _label_agreement(qb, cb, idx, mask)

    return VerifyResult(score=min(coverage + bonus, 1.0), inliers=inliers,
                        coverage=round(coverage, 4),
                        label_bonus=round(bonus, 4),
                        correspondences=len(src),
                        transform=M.tolist())


def verify_pair(q_fp: dict, c_fp: dict, candidate_flipped: bool = False,
                query_ambiguous: bool = False) -> VerifyResult:
    """Verify across the orientations still in play.

    Tier 1 reports which indexed orientation of the candidate matched, so that
    one is used. The query's own orientation may still be unresolved -- it is
    for half this sample -- and where it is, both ways up are tried and the
    better kept. Guessing instead would be silent and corrupting, which is the
    one failure mode this pipeline treats as unacceptable.
    """
    cand = flip_fingerprint(c_fp) if candidate_flipped else c_fp

    best = verify(q_fp, cand)
    if query_ambiguous:
        alt = verify(flip_fingerprint(q_fp), cand)
        if alt.score > best.score:
            alt.flipped_query = True
            best = alt
    return best


def query_is_ambiguous(fp: dict) -> bool:
    """Whether the query must be tried both ways up. Normally: always.

    This used to apply the index side's confidence threshold to the query, on
    the reasoning that it is the same question. It is not. A catalog record's
    orientation is resolved from text, read at full upscale both ways up; a
    query's is resolved by `fast_ocr`, which drops the text signal and leaves
    geometry -- and geometry is confidently wrong often enough to matter.
    `RM-PJ20_big_light` reads flipped at confidence 1.00 against a record
    stored upright at confidence 1.00, so under the old rule neither side was
    ever tried the other way up and the record was simply never retrieved.

    So the two confidences are different quantities and only one of them is
    worth trusting. Trying both ways at query time costs one retrieval and one
    verify per candidate, and no memory at all -- which is the whole reason
    the index side is allowed to be selective and this side is not.
    """
    if CFG.index.index_both_orientations or not CFG.index.trust_query_orientation:
        return True
    conf = float(fp.get("stats", {}).get("orientation_conf", 0.0))
    return conf < CFG.index.orientation_trust_conf
