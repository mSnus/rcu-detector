"""Where candidate fingerprints are read from at match time.

Tier 2 needs the full fingerprint of every candidate tier 1 returned, and the
model-code fast path needs a lookup over catalog metadata. In production both
come from MySQL; while the service is being built they come from a directory
of JSON files. Same interface, so nothing above this file knows the difference
-- the same reason the OCR engine sits behind an adapter.
"""
from __future__ import annotations

import json
from collections import OrderedDict
from pathlib import Path
from typing import Iterator, Protocol, runtime_checkable

from app.matching.tokens import norm_label


@runtime_checkable
class FingerprintStore(Protocol):
    def get(self, record_id: str) -> dict | None: ...
    def iter_all(self) -> Iterator[tuple[str, dict]]: ...
    def __len__(self) -> int: ...


# How many fingerprints to keep in memory. A query reads about 156 of them --
# the candidates tier 1 returns -- so a few thousand covers the working set of
# consecutive queries without holding the catalogue.
CACHE_SIZE = 2048


class JsonDirStore:
    """Fingerprints as `<dir>/<record_id>.json`, read on demand.

    This used to load every fingerprint at construction. That was a deliberate
    trade -- "a few hundred MB at the top end, and it saves a disk seek per
    candidate" -- and the measurement went the other way once the catalogue was
    real:

        read + parse the ~156 candidates a query touches      22 ms
        hold all 12311 of them                            228 MB, 9 s startup

    22 ms against a 3-second query is not worth 228 MB on a 3.9 GB box. The
    cost was not theoretical: the container limit had to be raised twice as the
    catalogue grew, each time discovered by the process being killed
    mid-request, and the box finally hit a *global* OOM during calibration --
    the kernel killed the service because the calibration run had loaded its
    own copy of the same fingerprints.

    The 9 s also mattered on its own. `load_index` rebuilds the store, so every
    reindex took the service out for long enough that the next query got a 503,
    which is how a user's upload came to be filed as "nothing matched".

    The code map is the one thing that genuinely needs every record. It is read
    from a sidecar written at index-build time when there is one, and rebuilt
    by streaming the directory when there is not -- streaming, so the
    fingerprints are parsed and dropped rather than retained.
    """

    def __init__(self, directory: str | Path,
                 codes_path: str | Path | None = None):
        self.dir = Path(directory)
        # Names only. A directory listing of 12k entries costs milliseconds;
        # opening 12k files costs seconds.
        self._ids = frozenset(p.stem for p in self.dir.glob("*.json"))
        self._cache: OrderedDict[str, dict] = OrderedDict()
        self._by_code = self._load_code_map(codes_path)

    def _load_code_map(self, codes_path) -> dict[str, list[str]]:
        if codes_path:
            p = Path(codes_path)
            if p.is_file():
                try:
                    raw = json.loads(p.read_text())
                    # Trust it only if it describes this directory. A sidecar
                    # left behind by an older catalogue would send the fast
                    # path at record ids that no longer exist.
                    return {k: [r for r in v if r in self._ids]
                            for k, v in raw.items()}
                except (json.JSONDecodeError, AttributeError):
                    pass
        out: dict[str, list[str]] = {}
        for rid, fp in self.iter_all():
            code = norm_label(fp.get("model_code"))
            if code:
                out.setdefault(code, []).append(rid)
        return out

    def get(self, record_id: str) -> dict | None:
        if record_id in self._cache:
            self._cache.move_to_end(record_id)
            return self._cache[record_id]
        if record_id not in self._ids:
            return None
        try:
            fp = json.loads((self.dir / f"{record_id}.json").read_text())
        except (OSError, json.JSONDecodeError):
            return None
        self._cache[record_id] = fp
        while len(self._cache) > CACHE_SIZE:
            self._cache.popitem(last=False)
        return fp

    def iter_all(self) -> Iterator[tuple[str, dict]]:
        """Every fingerprint, streamed.

        Deliberately not cached: the callers are the index build and the
        offline evaluation, which walk the catalogue once and would otherwise
        fill the cache with records no query will ask for.
        """
        for rid in sorted(self._ids):
            try:
                yield rid, json.loads((self.dir / f"{rid}.json").read_text())
            except (OSError, json.JSONDecodeError):
                continue

    def __len__(self) -> int:
        return len(self._ids)

    def by_model_code(self, code: str) -> list[str]:
        """Records carrying this exact model code (plan 4.5, fast path)."""
        return self._by_code.get(norm_label(code) or "", [])

    def model_codes(self) -> list[str]:
        return list(self._by_code)

    @staticmethod
    def write_code_map(fp_dir: str | Path, codes_path: str | Path) -> int:
        """Write the sidecar the constructor prefers. Called by build_index."""
        store = JsonDirStore(fp_dir, codes_path=None)
        Path(codes_path).parent.mkdir(parents=True, exist_ok=True)
        Path(codes_path).write_text(json.dumps(store._by_code))
        return len(store._by_code)


def _build_code_map(fps: dict[str, dict]) -> dict[str, list[str]]:
    """Model code -> record ids.

    Keyed on the normalised code, so RM-530F, RM 530F and rm530f are one key.
    The separator carries no information and OCR loses it constantly.
    """
    out: dict[str, list[str]] = {}
    for rid, fp in fps.items():
        code = norm_label(fp.get("model_code"))
        if code:
            out.setdefault(code, []).append(rid)
    return out
