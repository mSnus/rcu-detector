"""Where candidate fingerprints are read from at match time.

Tier 2 needs the full fingerprint of every candidate tier 1 returned, and the
model-code fast path needs a lookup over catalog metadata. In production both
come from MySQL; while the service is being built they come from a directory
of JSON files. Same interface, so nothing above this file knows the difference
-- the same reason the OCR engine sits behind an adapter.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator, Protocol, runtime_checkable

from app.matching.tokens import norm_label


@runtime_checkable
class FingerprintStore(Protocol):
    def get(self, record_id: str) -> dict | None: ...
    def iter_all(self) -> Iterator[tuple[str, dict]]: ...
    def __len__(self) -> int: ...


class JsonDirStore:
    """Fingerprints as `<dir>/<record_id>.json`, loaded eagerly.

    Eager because the catalog is 10k-50k documents of a few KB: holding them
    costs a few hundred MB at the top end and saves a disk seek per candidate
    on every query. The MySQL implementation will want a batched fetch of the
    top-N instead.
    """

    def __init__(self, directory: str | Path):
        self.dir = Path(directory)
        self._fps: dict[str, dict] = {}
        for p in sorted(self.dir.glob("*.json")):
            try:
                self._fps[p.stem] = json.loads(p.read_text())
            except json.JSONDecodeError:
                continue
        self._by_code = _build_code_map(self._fps)

    def get(self, record_id: str) -> dict | None:
        return self._fps.get(record_id)

    def iter_all(self) -> Iterator[tuple[str, dict]]:
        yield from self._fps.items()

    def __len__(self) -> int:
        return len(self._fps)

    def by_model_code(self, code: str) -> list[str]:
        """Records carrying this exact model code (plan 4.5, fast path)."""
        return self._by_code.get(norm_label(code) or "", [])

    def model_codes(self) -> list[str]:
        return list(self._by_code)


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
