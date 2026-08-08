#!/usr/bin/env python3
"""Build the inverted token index from a directory of fingerprints.

  python scripts/build_index.py --fp ../work/fp --out ../work/index/tokens.npz

Rebuild after any catalog change; it takes seconds and there is no incremental
path on purpose. Adding a remote is an INSERT plus this, never a retraining run
-- which is the reason the whole system is retrieval and not classification.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import CFG
from app.matching.index import TokenIndex, _orientation_ambiguous
from app.matching.store import JsonDirStore


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fp", default="../work/fp",
                    help="directory of fingerprint JSON documents")
    ap.add_argument("--out", default="../work/index/tokens.npz")
    args = ap.parse_args()

    store = JsonDirStore(args.fp)
    if not len(store):
        print(f"no fingerprints in {args.fp}")
        return 1

    records = list(store.iter_all())
    stale = [r for r, fp in records
             if fp.get("v") != CFG.fingerprint_version]
    if stale:
        print(f"warning: {len(stale)} fingerprint(s) at an older version "
              f"than {CFG.fingerprint_version}, e.g. {stale[0]}")

    t0 = time.time()
    index = TokenIndex.build(records, verbose=True)
    index.save(args.out)

    # The model-code map, beside the index. It is the one thing the service
    # needs from every fingerprint, and writing it here means the store can
    # start without opening 12k files -- see JsonDirStore.
    codes_path = Path(args.out).with_name("codes.json")
    n_codes = JsonDirStore.write_code_map(args.fp, codes_path)
    print(f"  {n_codes} model code(s) -> {codes_path}")
    dt = time.time() - t0

    ambiguous = sum(1 for _, fp in records if _orientation_ambiguous(fp))
    size_mb = Path(args.out).stat().st_size / 1e6

    print(f"\nindexed {index.n_records} records as {index.n_docs} docs "
          f"in {dt:.2f}s")
    print(f"  {ambiguous} record(s) orientation-ambiguous, indexed both ways up")
    print(f"  {index.n_postings} postings, {len(index.token_ids)} unique tokens")
    print(f"  {args.out}  ({size_mb:.2f} MB)")

    # Extrapolate, because the whole design rests on this staying in memory.
    if index.n_records:
        per = index.n_postings / index.n_records
        print(f"  ~{per:.0f} postings/record -> {per * 50000 / 1e6:.1f} M "
              f"postings at 50k records "
              f"(~{per * 50000 * 4 / 1e6:.0f} MB as int32)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
