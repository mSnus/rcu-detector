"""Assert the catalogue-title rule takes the remote's code and not the television's.

Every case here is a real title shape from the live catalogue. The dangerous
one is the fourth: a title that names only the *device* the remote is for.
2840 of 7431 titles carry a code only after the word "пульт", and taking it
would file a remote under a television's model number -- confidently, because
an exact model-code agreement outscores every other term in the fusion.

    python scripts/check_catalog_codes.py

Exits non-zero on any disagreement.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.apply_catalog_codes import code_from_title  # noqa: E402

CASES = [
    # the code leads, the television follows
    ("BN59-01315B оригинальный пульт для телевизора Samsung UE65RU7172 и др.", "BN59-01315B"),
    ("AA59-00622A оригинальный пульт для монитора Samsung T24B300L", "AA59-00622A"),
    ("EUR7722X30 пульт для домашнего кинотеатра Panasonic SC-HT870 и др.", "EUR7722X30"),
    ("RC-1035 оригинальный пульт для аудиосистемы Denon S-81", "RC-1035"),
    # interchangeable codes for one remote: the catalogue leads with the first
    ("6710V00046D , 6710V00046C , 6710V00046K пульт для телевизора LG", "6710V00046D"),
    ("996590009952, YKF354-001, 398GRFBD1NEPHT оригинальный пульт", "996590009952"),
    # shapes MODEL_RE rejects and this must not: digits first, long suffix
    ("G1350PESA пульт для телевизора", "G1350PESA"),
    # NOTHING may be taken from these
    ("Пульт для телевизора Samsung UE65RU7172", None),   # only the TV is named
    ("заменяющий IRC-9601D", None),                       # no description marker
    ("Пульт HDMI2 для телевизора", None),                 # a button legend
    ("", None),
]


def main() -> int:
    bad = []
    for title, want in CASES:
        got = code_from_title(title)
        if got != want:
            bad.append(f"{title[:56]!r}: expected {want!r}, got {got!r}")

    print(f"{len(CASES)} title(s) checked")
    if bad:
        print(f"\n!! {len(bad)} disagreement(s):")
        for line in bad:
            print(f"     {line}")
        print("\nFAIL -- see code_from_title in scripts/apply_catalog_codes.py")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
