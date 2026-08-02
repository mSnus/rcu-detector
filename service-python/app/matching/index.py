"""Inverted token index and tier-1 retrieval (plan 4.2-4.3).

Stored CSR-style in flat numpy arrays, never a Python dict: at catalog scale
a dict of twenty million postings costs several GB, the same data in int32
arrays costs a few hundred MB.

    token_ids   int64[N_unique]     sorted, binary-searchable
    offsets     int64[N_unique + 1]
    postings    int32[N_total]      doc ids
    idf         float32[N_unique]   log(n_docs / df)

**IDF weighting is the whole trick.** A 3x4 grey keypad occurs in most of the
catalog and contributes nearly nothing; an orange pentagon occurring in eleven
records contributes enormously. That behaviour falls out of the statistics, so
there is no table of "important features" to hand-tune and no retraining when
a remote is added -- an INSERT plus a rebuild is the entire update path.

A *doc* is not a record. An orientation-ambiguous record is indexed twice, once
each way up, with both docs pointing at the same record. Orientation errors are
silent and corrupting, so where the extractor could not resolve one the index
carries both possibilities rather than betting on the likelier.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.config import CFG
from app.matching.tokens import fingerprint_tokens, flip_fingerprint


@dataclass
class RetrievalHit:
    record_id: str
    # Cosine-like: matched idf mass over the geometric mean of the query's and
    # the document's total mass. One number, used both to rank here and as the
    # tier-1 term in fusion. An earlier version carried a separate raw score
    # for ranking and a normalised one for fusion; they ordered candidates
    # differently, which is a bug waiting to happen. Bounded 0..1 because the
    # matched mass cannot exceed either total.
    score: float
    matched_mass: float   # unnormalised, for diagnostics
    flipped: bool         # the indexed orientation that matched


class TokenIndex:
    """In-memory inverted index. Load once at startup, never per request."""

    def __init__(self, token_ids: np.ndarray, offsets: np.ndarray,
                 postings: np.ndarray, idf: np.ndarray,
                 doc_norms: np.ndarray, doc_record: np.ndarray,
                 doc_flipped: np.ndarray, record_ids: np.ndarray,
                 token_version: int = 0, fingerprint_version: int = 0):
        self.token_ids = token_ids
        self.offsets = offsets
        self.postings = postings
        self.idf = idf
        self.doc_norms = doc_norms
        self.doc_record = doc_record
        self.doc_flipped = doc_flipped
        self.record_ids = record_ids
        self.token_version = token_version
        self.fingerprint_version = fingerprint_version

    @property
    def n_docs(self) -> int:
        return int(len(self.doc_record))

    @property
    def n_records(self) -> int:
        return int(len(self.record_ids))

    @property
    def n_postings(self) -> int:
        return int(len(self.postings))

    # -- build -------------------------------------------------------------

    @classmethod
    def build(cls, records: list[tuple[str, dict]],
              verbose: bool = False) -> "TokenIndex":
        """Build from (record_id, fingerprint) pairs.

        Takes seconds; rebuild after any catalog change rather than trying to
        patch postings in place.
        """
        rec_ids: list[str] = []
        doc_tokens: list[np.ndarray] = []
        doc_record: list[int] = []
        doc_flipped: list[bool] = []

        for rid, fp in records:
            r = len(rec_ids)
            rec_ids.append(rid)
            variants = [(fp, False)]
            if _orientation_ambiguous(fp):
                variants.append((flip_fingerprint(fp), True))
            for variant, flipped in variants:
                toks = fingerprint_tokens(variant)
                if len(toks) == 0:
                    # A record with no tokens is unretrievable but must still
                    # exist, or record ids shift under the caller.
                    continue
                doc_tokens.append(toks)
                doc_record.append(r)
                doc_flipped.append(flipped)

        n_docs = len(doc_tokens)
        if n_docs == 0:
            return cls(np.zeros(0, np.int64), np.zeros(1, np.int64),
                       np.zeros(0, np.int32), np.zeros(0, np.float32),
                       np.zeros(0, np.float32), np.zeros(0, np.int32),
                       np.zeros(0, np.bool_), np.array(rec_ids, dtype=object),
                       CFG.token_version, CFG.fingerprint_version)

        flat_tok = np.concatenate(doc_tokens)
        flat_doc = np.repeat(np.arange(n_docs, dtype=np.int32),
                             [len(t) for t in doc_tokens])

        # Sort by token, then doc. Postings for one token then form a
        # contiguous, ascending slice, which is what makes the CSR layout and
        # the binary search work.
        order = np.lexsort((flat_doc, flat_tok))
        flat_tok = flat_tok[order]
        flat_doc = flat_doc[order]

        token_ids, starts = np.unique(flat_tok, return_index=True)
        offsets = np.append(starts, len(flat_tok)).astype(np.int64)
        df = np.diff(offsets)
        idf = np.log(n_docs / df.astype(np.float64)).astype(np.float32)
        # A token in every doc has idf 0 and would contribute nothing; a
        # tiny floor keeps it from also zeroing a doc norm.
        idf = np.maximum(idf, 1e-6)

        # Doc norm is the doc's own total idf mass. Dividing by a power of it
        # at query time stops a 54-button record from outscoring a 6-button
        # one on sheer volume, without fully normalising away the fact that a
        # rich record genuinely carries more evidence.
        doc_norms = np.zeros(n_docs, dtype=np.float64)
        np.add.at(doc_norms, flat_doc, idf[np.searchsorted(token_ids, flat_tok)])
        doc_norms = np.maximum(doc_norms, 1e-6).astype(np.float32)

        if verbose:
            print(f"  {n_docs} docs / {len(rec_ids)} records, "
                  f"{len(token_ids)} unique tokens, {len(flat_tok)} postings")

        return cls(token_ids.astype(np.int64), offsets,
                   flat_doc.astype(np.int32), idf, doc_norms,
                   np.asarray(doc_record, dtype=np.int32),
                   np.asarray(doc_flipped, dtype=np.bool_),
                   np.asarray(rec_ids, dtype=object),
                   CFG.token_version, CFG.fingerprint_version)

    # -- persistence -------------------------------------------------------

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            path,
            token_ids=self.token_ids, offsets=self.offsets,
            postings=self.postings, idf=self.idf, doc_norms=self.doc_norms,
            doc_record=self.doc_record, doc_flipped=self.doc_flipped,
            record_ids=np.asarray([str(r) for r in self.record_ids]),
            versions=np.asarray([self.token_version,
                                 self.fingerprint_version], dtype=np.int64),
        )

    @classmethod
    def load(cls, path: str | Path) -> "TokenIndex":
        """Load, refusing a stale index rather than retrieving nonsense.

        Token generation changing under a built index does not raise anywhere
        -- it just returns quietly wrong candidates -- so the version is
        checked here, where the failure is still loud.
        """
        with np.load(path, allow_pickle=False) as z:
            versions = z["versions"] if "versions" in z else np.array([0, 0])
            tok_v = int(versions[0])
            if tok_v != CFG.token_version:
                raise ValueError(
                    f"index at {path} was built with token_version {tok_v}, "
                    f"code is at {CFG.token_version}. Rebuild it: "
                    f"python scripts/build_index.py")
            return cls(z["token_ids"], z["offsets"], z["postings"], z["idf"],
                       z["doc_norms"], z["doc_record"], z["doc_flipped"],
                       z["record_ids"], tok_v, int(versions[1]))

    # -- tier 1 ------------------------------------------------------------

    def retrieve(self, query_tokens: np.ndarray,
                 top_n: int | None = None) -> list[RetrievalHit]:
        """Coarse retrieval. Returns the best records by weighted token mass.

        Scores per doc, then collapses to records keeping each record's best
        orientation, so a record indexed both ways up gets one entry and not
        two adjacent ones crowding the candidate list.
        """
        cfg = CFG.index
        top_n = top_n or cfg.top_n
        if self.n_docs == 0 or len(query_tokens) == 0:
            return []

        q = np.unique(np.asarray(query_tokens, dtype=np.int64))
        pos = np.searchsorted(self.token_ids, q)
        pos = np.clip(pos, 0, len(self.token_ids) - 1)
        present = self.token_ids[pos] == q
        pos = pos[present]
        if len(pos) == 0:
            return []

        weights = self.idf[pos]
        lo, hi = self.offsets[pos], self.offsets[pos + 1]
        df = hi - lo

        # Both filters exist purely for speed (plan 4.3). The df ceiling is
        # meaningless on a catalog too small for a fraction of it to be a
        # sensible count, so it is disabled there rather than throwing away
        # most of the signal during development.
        keep = weights >= cfg.min_idf
        if self.n_docs >= cfg.max_df_min_docs:
            keep &= df <= int(cfg.max_df_frac * self.n_docs)
        pos, weights, lo, hi, df = (pos[keep], weights[keep], lo[keep],
                                    hi[keep], df[keep])
        if len(pos) == 0:
            return []

        # bincount over the concatenated posting slices, rather than np.add.at
        # per token: same result, roughly an order of magnitude faster, and
        # tier 1 has a 10 ms budget.
        idx = np.concatenate([self.postings[a:b] for a, b in zip(lo, hi)])
        w = np.repeat(weights, df)
        mass = np.bincount(idx, weights=w, minlength=self.n_docs)

        # Normalise by doc mass and query mass together. The query term is
        # constant within a query so it does not affect this ranking -- it is
        # there so the score means the same thing across queries, which is
        # what fusion needs downstream.
        q_mass = float(weights.sum()) or 1.0
        e = cfg.norm_exponent
        scores = mass / (np.power(self.doc_norms, e) * q_mass ** (1.0 - e))

        n = min(top_n * 2, self.n_docs)          # *2: docs collapse to records
        cand = np.argpartition(-scores, n - 1)[:n]
        cand = cand[scores[cand] > 0]

        best: dict[int, tuple[float, int]] = {}
        for d in cand:
            r = int(self.doc_record[d])
            s = float(scores[d])
            if r not in best or s > best[r][0]:
                best[r] = (s, int(d))

        hits = [
            RetrievalHit(record_id=str(self.record_ids[r]),
                         score=min(s, 1.0),
                         matched_mass=float(mass[d]),
                         flipped=bool(self.doc_flipped[d]))
            for r, (s, d) in best.items()
        ]
        hits.sort(key=lambda h: -h.score)
        return hits[:top_n]


def _orientation_ambiguous(fp: dict) -> bool:
    """Whether this record must be indexed both ways up.

    Currently true for everything. Session 2 left 11 of 21 extractions
    unresolved, and worse, at least one *resolved* extraction is wrong:
    RM-PJ20_big_light_0 reports confidence 1.00 and is stored upside down. So
    confidence cannot be used to skip the second doc yet. The cost is one
    extra doc per record; the cost of getting it wrong is a record that can
    never be retrieved, with nothing raising an error anywhere.
    """
    if CFG.index.index_both_orientations:
        return True
    conf = float(fp.get("stats", {}).get("orientation_conf", 0.0))
    return conf < CFG.index.orientation_trust_conf
