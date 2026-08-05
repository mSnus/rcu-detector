"""Assert MODEL_RE reads the codes it must and refuses the ones it must not.

A model code is the highest-precision signal the matcher has -- an exact
agreement is worth CFG.fuse.model_code_bonus, more than any other single term
-- which cuts both ways: a *wrong* code sends a match confidently to the wrong
remote. So the pattern is pinned by examples rather than by reading it.

Every accepted case below is a real code from this catalogue. Every rejected
one has been observed being misread as a code, or would be under a plausible
loosening of the pattern.

    python scripts/check_model_codes.py

Exits non-zero on any disagreement.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.pipeline.branding import BUTTON_VOCAB, MODEL_RE  # noqa: E402

# text -> the code that must come out of it
ACCEPT = {
    # Samsung: a second digit group after the separator. Truncated to "BN59"
    # before this was handled, which names a family of hundreds.
    "BN59-01315B": "BN59-01315B",
    "AA59-00543A": "AA59-00543A",
    "BN59-01268D": "BN59-01268D",
    "AA59-00622A": "AA59-00622A",
    # Two letter groups before the digits. Truncated to "D1110" when the
    # pattern had one group -- a different remote as far as the index cares.
    "RM-D1110": "RM-D1110",
    "RM-PJ20R": "RM-PJ20R",
    # Single group, with and without a separator.
    "RSF-3106RT": "RSF-3106RT",
    "MR-18B": "MR-18B",
    "TM1060": "TM1060",
    "RM 530F": "RM 530F",
}

# text that must NOT yield a code. The blocklist is applied by find_model_code
# rather than by the pattern, so entries here are checked against both.
REJECT = [
    "HDMI2", "VGA1", "AV2", "USB1",   # button legends
    "1 2 3",                          # a keypad row
    "0",                              # a single key
]


def main() -> int:
    bad: list[str] = []

    for text, want in ACCEPT.items():
        got = [m.group(0) for m in MODEL_RE.finditer(text)]
        if want not in got:
            bad.append(f"{text!r}: expected {want!r}, got {got}")

    for text in REJECT:
        for m in MODEL_RE.finditer(text):
            tok = m.group(0)
            bare = tok.replace("-", "").replace(" ", "")
            # A pattern hit is acceptable when the blocklist catches it; that
            # is the division of labour the two are meant to have.
            if bare not in BUTTON_VOCAB:
                bad.append(f"{text!r}: yielded {tok!r}, which no filter stops")

    # The one case the pattern must clip rather than swallow: with a space
    # allowed in the trailing group, a keypad row becomes a model code.
    row = [m.group(0) for m in MODEL_RE.finditer("VOL 12 34")]
    if any(t.count(" ") > 1 or "34" in t for t in row):
        bad.append(f"'VOL 12 34': ran past the first group -> {row}")

    print(f"{len(ACCEPT)} accepted, {len(REJECT)} rejected case(s) checked")
    if bad:
        print(f"\n!! {len(bad)} disagreement(s):")
        for line in bad:
            print(f"     {line}")
        print("\nFAIL -- see MODEL_RE in app/pipeline/branding.py")
        return 1

    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
