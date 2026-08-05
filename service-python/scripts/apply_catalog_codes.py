"""Take each record's model code from the catalogue title, not from its photograph.

The catalogue knows the code. The extractor has to read it off whatever image
survives -- often a 289x1057 imagecache derivative -- and gets it slightly
wrong: on `BN59-01315B-orig_0` the title says `BN59-01315B` and OCR of that
record's own photograph says `BN59-013158`. An exact agreement is worth
`CFG.fuse.model_code_bonus` (0.40) and a fuzzy one half that, so the last
character is worth 0.20 on the score of a correct answer.

This is also the only way to fix a code without re-extracting: a fingerprint
stores the *derived* code and discards the OCR region it came from, so a
changed pattern cannot be re-applied to stored fingerprints. Measured on the
live catalogue, taking codes from titles reaches 4218 records against the 1218
the extractor read.

    php artisan rcu:export-titles --out=- > work/titles.tsv
    python scripts/apply_catalog_codes.py --fp ../work/fp --titles ../work/titles.tsv

Then rebuild the index and re-import, exactly as after an extraction: the token
index and the catalog table both carry the code.

**Only the head of the title is read.** The catalogue's convention is

    BN59-01315B оригинальный пульт для телевизора Samsung UE65RU7172 и др.
    ^^^^^^^^^^^ the remote                        ^^^^^^^^^^ the television

so everything from the word "пульт" onward names the *device the remote is
for*, and taking a code from there would confidently file a remote under a
television's model number. 2840 titles carry a code only after that word, and
every one of them would be wrong.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import CFG  # noqa: E402
from app.pipeline.branding import BUTTON_VOCAB, MODEL_RE  # noqa: E402

# Where the remote's own code stops and the target device's begins. Case-folded
# because titles use both "пульт" and "Пульт", and the Latin lookalike is there
# because a scraped catalogue contains both.
HEAD_STOP = re.compile(r"пульт|пyльт", re.IGNORECASE)


def code_from_title(title: str) -> str | None:
    """The remote's model code, or None.

    Returns the *first* code in the head, because a head listing several --
    "6710V00046D , 6710V00046C , 6710V00046K пульт для телевизора LG" -- is
    listing interchangeable codes for one remote, and the first is the one the
    catalogue leads with.

    The test is looser than `MODEL_RE` on purpose, and this is the one place in
    the project where a second definition of "looks like a model code" is
    correct rather than drift. MODEL_RE's job is to pick a code out of noisy
    OCR over a remote covered in button legends, so it is shaped for precision
    against text like VGA1 and LIVE20OM. Here the input is a catalogue title
    whose *first token is the code* -- there is nothing to discriminate against
    -- and MODEL_RE's guards reject real codes wholesale: `6710V00046D` leads
    with digits, `G1350PESA` has a four-letter suffix, `996590009952` has no
    letters at all. Applying it here loses those records for no gain.
    """
    stop = HEAD_STOP.search(title)
    head = title[:stop.start()] if stop else ""
    if not head.strip():
        return None

    for raw in re.split(r"[\s,;/]+", head.upper()):
        tok = raw.strip(".,;:()[]\"'").replace("_", "-")
        if not tok or len(tok) < 5:
            continue
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9-]*[A-Z0-9]", tok):
            continue
        if sum(c.isdigit() for c in tok) < 2:
            continue
        if tok.replace("-", "") in BUTTON_VOCAB:
            continue
        return tok
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fp", type=Path, required=True)
    ap.add_argument("--titles", type=Path, required=True,
                    help="record_id<TAB>title, from rcu:export-titles")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.fp.is_dir():
        sys.exit(f"no fingerprint directory at {args.fp}")

    titles: dict[str, str] = {}
    for line in args.titles.read_text().splitlines():
        if "\t" not in line:
            continue
        rid, title = line.split("\t", 1)
        titles[rid.strip()] = title.strip()

    if not titles:
        sys.exit(f"no titles in {args.titles}")

    written = 0
    agreed = 0
    disagreed: list[str] = []
    replaced_ocr = 0
    no_code = 0
    absent = 0
    unchanged = 0

    for rid, title in sorted(titles.items()):
        path = args.fp / f"{rid}.json"
        if not path.is_file():
            absent += 1
            continue

        code = code_from_title(title)
        if code is None:
            no_code += 1
            continue

        fp = json.loads(path.read_text())
        old = fp.get("model_code")

        if old == code and fp.get("model_code_source") == "catalog":
            unchanged += 1
            continue
        if old:
            # Both sources produced something. Agreement is the healthy case;
            # a disagreement is worth seeing, because it is either OCR noise
            # (expected, and the reason for this script) or a record whose
            # photograph and metadata are about different remotes (not).
            if old.upper() == code.upper():
                agreed += 1
            else:
                replaced_ocr += 1
                if len(disagreed) < 15:
                    disagreed.append(f"{rid}: OCR {old!r} -> catalog {code!r}")

        fp["model_code"] = code
        # Provenance, so nothing downstream has to guess whether a code was
        # read off the remote or taken from the catalogue. The two are not
        # equally trustworthy about what is *printed* on the remote, which is
        # what a query is compared against.
        fp["model_code_source"] = "catalog"
        if old and old != code:
            fp["model_code_ocr"] = old

        if not args.dry_run:
            path.write_text(json.dumps(fp))
        written += 1

    verb = "would set" if args.dry_run else "set"
    print(f"{verb} {written} model code(s) from {len(titles)} title(s)")
    print(f"  {agreed} already agreed with OCR, {replaced_ocr} replaced a "
          f"different OCR reading")
    print(f"  {unchanged} already applied, {no_code} title(s) carry no code "
          f"before the description, {absent} have no fingerprint")

    if disagreed:
        print("\nreplaced (first 15) -- a wholly different code on both sides "
              "is a record worth looking at, not OCR noise:")
        for line in disagreed:
            print(f"  {line}")

    if not args.dry_run and written:
        print("\nNow rebuild the index and re-import, as after any extraction:")
        print("  scripts/build_index.py --fp <fp> --out <index>/tokens.npz")
        print("  php artisan rcu:import-catalog --legacy --prune --reindex")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
