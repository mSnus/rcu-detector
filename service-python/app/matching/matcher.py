"""Score fusion and the match entry point (plan 4.5-4.6).

Order of work for one query:

  1. Model-code fast path. Near-100% precise and one indexed lookup, so it
     runs before anything expensive.
  2. Tier 1, inverted index retrieval weighted by IDF.
  3. Tier 2, RANSAC geometric verification of the top candidates.
  4. Fusion into one score, then a confidence band.

The fusion weights are NOT a place to fix a bad match. When a match fails the
cause is nearly always upstream in extraction -- look at the overlay first.
Tuning weights to paper over a bad fingerprint moves the error somewhere less
visible instead of removing it.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.config import CFG
from app.matching.index import TokenIndex
from app.matching.store import FingerprintStore
from app.matching.tokens import (fingerprint_tokens, flip_fingerprint,
                                 norm_label)
from app.matching.verify import VerifyResult, query_is_ambiguous, verify_pair


@dataclass
class Candidate:
    record_id: str
    score: float = 0.0
    tier1: float = 0.0
    geometric: float = 0.0
    inliers: int = 0
    brand_agreement: float = 0.5
    aspect_agreement: float = 0.0
    model_code_bonus: float = 0.0
    flipped: bool = False           # candidate orientation that matched
    flipped_query: bool = False     # query orientation that matched
    brand: str | None = None
    model_code: str | None = None
    verify_reason: str = ""

    def as_dict(self) -> dict:
        return {
            "record_id": self.record_id,
            "score": round(self.score, 4),
            "inliers": self.inliers,
            "brand": self.brand,
            "model_code": self.model_code,
            "terms": {
                "tier1": round(self.tier1, 4),
                "geometric": round(self.geometric, 4),
                "brand_agreement": self.brand_agreement,
                "aspect_agreement": round(self.aspect_agreement, 4),
                "model_code_bonus": self.model_code_bonus,
            },
            "orientation": {"candidate_flipped": self.flipped,
                            "query_flipped": self.flipped_query},
        }


@dataclass
class MatchResult:
    candidates: list[Candidate] = field(default_factory=list)
    confidence: str = "none"
    hint: str | None = None
    fast_path: bool = False
    n_retrieved: int = 0


def _brand_agreement(q_brand: str | None, c_brand: str | None) -> float:
    """1.0 agree, 0.0 conflict, 0.5 either side unknown.

    Weighted, never filtered. OCR misreads brands (AIWA reads as "EMIE") and
    rebadged remotes genuinely carry the wrong name, so a brand conflict must
    be able to lose to strong geometry rather than veto it.
    """
    cfg = CFG.fuse
    if not q_brand or not c_brand:
        return cfg.brand_unknown
    return (cfg.brand_match if q_brand.strip().lower() == c_brand.strip().lower()
            else cfg.brand_conflict)


def _aspect_agreement(q_fp: dict, c_fp: dict) -> float:
    qa = float(q_fp.get("body", {}).get("aspect") or 0.0)
    ca = float(c_fp.get("body", {}).get("aspect") or 0.0)
    if qa <= 0 or ca <= 0:
        return 0.5
    rel = abs(qa - ca) / max(qa, ca)
    return max(0.0, 1.0 - rel / CFG.fuse.aspect_tolerance)


def _levenshtein(a: str, b: str) -> int:
    """Small, dependency-free edit distance; codes are under 12 characters."""
    if a == b:
        return 0
    if not a or not b:
        return max(len(a), len(b))
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1,
                           prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _model_code_bonus(q_code: str | None, c_code: str | None) -> float:
    cfg = CFG.fuse
    q, c = norm_label(q_code), norm_label(c_code)
    if not q or not c:
        return 0.0
    if q == c:
        return cfg.model_code_bonus
    if _levenshtein(q, c) <= cfg.fuzzy_model_max_dist:
        return cfg.fuzzy_model_code_bonus
    return 0.0


def _band(candidates: list[Candidate]) -> tuple[str, str | None]:
    """Confidence band and the UI hint that goes with it.

    These thresholds are the plan's starting numbers and have not been
    calibrated against a real test set. Treat them as provisional.
    """
    cfg = CFG.fuse
    if not candidates:
        return "none", "reshoot"
    top = candidates[0].score
    margin = top - (candidates[1].score if len(candidates) > 1 else 0.0)

    if top > cfg.high_score and margin > cfg.high_margin:
        return "high", None
    if top > cfg.medium_score:
        return "medium", None
    if top > cfg.low_score:
        # The hint is conditional on the query having no code, and that is not
        # known here -- the caller applies it. Returning it unconditionally
        # told a user whose photograph had *just* yielded BN59-01315B to go and
        # photograph the model code.
        return "low", None
    return "none", "reshoot"


class Matcher:
    """Holds the index and the store. Build once at startup, never per query."""

    def __init__(self, index: TokenIndex, store: FingerprintStore):
        self.index = index
        self.store = store

    def match(self, q_fp: dict, top_k: int = 5,
              exclude: set[str] | None = None) -> MatchResult:
        """Identify one query fingerprint.

        `exclude` drops record ids before scoring. It exists for leave-one-out
        evaluation, where a record must not be allowed to match itself.
        """
        exclude = exclude or set()
        ambiguous = query_is_ambiguous(q_fp)

        # --- 1. model-code fast path --------------------------------------
        # Runs first because it is near-100% precise and costs one lookup.
        # It narrows the candidate set; it does not bypass verification, so a
        # misread code still has to survive tier 2.
        fast_ids: list[str] = []
        q_code = norm_label(q_fp.get("model_code"))
        if q_code and hasattr(self.store, "by_model_code"):
            fast_ids = [r for r in self.store.by_model_code(q_code)
                        if r not in exclude]

        # --- 2. tier 1 -----------------------------------------------------
        hits = self.index.retrieve(fingerprint_tokens(q_fp))
        if ambiguous:
            # The index already holds both orientations of every ambiguous
            # RECORD, but the query's own orientation is a separate unknown:
            # retrieving with only one way up would miss a confidently-indexed
            # record that the query happens to be upside down against.
            hits += self.index.retrieve(fingerprint_tokens(
                flip_fingerprint(q_fp)))

        best_hit: dict[str, tuple[float, bool]] = {}
        for h in hits:
            if h.record_id in exclude:
                continue
            cur = best_hit.get(h.record_id)
            if cur is None or h.score > cur[0]:
                best_hit[h.record_id] = (h.score, h.flipped)

        n_retrieved = len(best_hit)
        # Fast-path records are verified even when tier 1 did not surface
        # them: the code is stronger evidence than the token overlap.
        for rid in fast_ids:
            best_hit.setdefault(rid, (0.0, False))

        # --- 3. tier 2 + 4. fusion ----------------------------------------
        cfg = CFG.fuse
        out: list[Candidate] = []
        for rid, (t1, flipped) in best_hit.items():
            c_fp = self.store.get(rid)
            if c_fp is None:
                continue
            v: VerifyResult = verify_pair(q_fp, c_fp,
                                          candidate_flipped=flipped,
                                          query_ambiguous=ambiguous)
            brand_ag = _brand_agreement(q_fp.get("brand"), c_fp.get("brand"))
            aspect_ag = _aspect_agreement(q_fp, c_fp)
            code_bonus = _model_code_bonus(q_fp.get("model_code"),
                                           c_fp.get("model_code"))
            score = (cfg.w_geometric * v.score
                     + cfg.w_tier1 * t1
                     + cfg.w_brand * brand_ag
                     + cfg.w_aspect * aspect_ag
                     + code_bonus)
            out.append(Candidate(
                record_id=rid, score=score, tier1=t1, geometric=v.score,
                inliers=v.inliers, brand_agreement=brand_ag,
                aspect_agreement=aspect_ag, model_code_bonus=code_bonus,
                flipped=flipped, flipped_query=v.flipped_query,
                brand=c_fp.get("brand"), model_code=c_fp.get("model_code"),
                verify_reason=v.reason))

        out.sort(key=lambda c: -c.score)
        out = out[:top_k]
        confidence, hint = _band(out)

        # A query carrying no readable code has the most to gain from the
        # "photograph the code" prompt, whatever band it landed in. One that
        # already has a code has nothing to gain: the answer is uncertain for
        # some other reason, and asking again for what was already supplied
        # reads as the service not having looked.
        if hint is None and confidence != "high" and not q_code:
            hint = "photograph_back"

        return MatchResult(candidates=out, confidence=confidence, hint=hint,
                           fast_path=bool(fast_ids), n_retrieved=n_retrieved)
