#!/usr/bin/env python3
"""Run the extraction pipeline over one or more images.

Writes, per detected remote:
  <out>/norm/<stem>_<idx>.jpg     rectified canonical crop
  <out>/debug/<stem>_<idx>.jpg    annotated side-by-side overlay
  <out>/fp/<stem>_<idx>.json      fingerprint document

Usage:
  python scripts/extract_one.py IMG [IMG ...] --out ../work
  python scripts/extract_one.py --dir /var/lib/rcu/catalog_raw --out ../work
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import CFG
from app.pipeline.color import color_summary
from app.pipeline.extract import debug_panel, extract_remotes
from app.pipeline.imageio import read_image
from app.pipeline.ocr import available_engines


def process_image(path: Path, out_dir: Path, ensemble: bool = True,
                  verbose: bool = True, use_ocr: bool = True,
                  consensus: bool = False,
                  stem: str | None = None) -> list[dict] | None:
    """Extract, then write the crop, the overlay and the fingerprint.

    The extraction itself lives in app/pipeline/extract.py and is shared with
    the query service; everything here is I/O and reporting.

    `stem` overrides the record identifier, which otherwise comes from the
    filename. Pass the catalogue's own primary key for a database-driven
    build: source filenames are not unique -- the legacy catalogue holds two
    different remotes both called `IRC_new.jpg`, deduplicated only in their
    stored path -- and identical stems collide silently in the flat `fp/`
    directory, one record overwriting the other.

    Returns None when the file could not be decoded at all, and a list --
    possibly empty -- when it could. The caller counts those separately: an
    unreadable file and a readable one holding no recognisable remote are
    different problems, and lumping them together hides both.

    Entries carry `dropped`: None for a record that was written, otherwise the
    reason it was refused. A refused entry is still returned rather than
    silently omitted, because a record that vanishes without a reason is
    indistinguishable from a catalogue gap downstream.
    """
    # Never cv2.imread: the service decodes uploads through the same helper,
    # and a truncated JPEG read any other way fills its missing region with
    # uninitialised memory -- different between the two paths, and different
    # between two runs of this script. See app/pipeline/imageio.py.
    stem_base = stem or path.stem

    img = read_image(path)
    if img is None:
        print(f"  !! unreadable: {path}")
        return None

    h, w = img.shape[:2]
    if max(h, w) < CFG.normalize.min_source_long_side:
        # Refused before extraction, not after: there is nothing to look at in
        # an overlay of an upscaled thumbnail, and running it only produces a
        # confident fingerprint of interpolation noise. Counted by the caller.
        reason = f"too small ({w}x{h}, long side under {CFG.normalize.min_source_long_side})"
        print(f"  !! {path.name}: {reason}")
        # Not an empty list: that means "readable, no remote in it", which is a
        # different problem with a different fix. The reason travels with the
        # result so the caller records this one as itself.
        return [{"stem": stem_base, "fingerprint": None, "dropped": reason}]

    for sub in ("norm", "debug", "fp"):
        (out_dir / sub).mkdir(parents=True, exist_ok=True)

    remotes = extract_remotes(img, ensemble=ensemble, use_ocr=use_ocr,
                              consensus=consensus)
    if verbose:
        print(f"\n{path.name}  {img.shape[1]}x{img.shape[0]}  "
              f"-> {len(remotes)} bod{'y' if len(remotes)==1 else 'ies'}")

    # A sparse crop beside a dense sibling is scenery, not a second remote.
    # Computed over the whole photograph before the loop, because the test is
    # relative and a single crop has nothing to be relative to -- a one-crop
    # photo is never touched however few buttons it has. The most-buttoned crop
    # cannot fail its own ratio test, so this can never empty a photograph.
    max_buttons = max((len(r.buttons) for r in remotes), default=0)
    bcfg = CFG.body

    results = []
    for r in remotes:
        fp, orient = r.fingerprint, r.orientation
        stem = f"{stem_base}_{r.index}"

        cv2.imwrite(str(out_dir / "norm" / f"{stem}.jpg"), r.crop,
                    [cv2.IMWRITE_JPEG_QUALITY, 92])
        cv2.imwrite(str(out_dir / "debug" / f"{stem}.jpg"),
                    debug_panel(img, r, use_ocr=use_ocr),
                    [cv2.IMWRITE_JPEG_QUALITY, 88])

        # A body with no buttons and no text has no tokens, so it can never be
        # retrieved -- by itself or by anything else. Writing it anyway costs a
        # catalog row, an index doc and a fingerprint file, and inflates every
        # count downstream with records that are structurally unmatchable.
        # Session 6 found 29 of 109 like this: the manifest had resolved to
        # 16x50 imagecache thumbnails, and nothing said so, because the build
        # reported them as remotes extracted. The dimensions go in the reason
        # for exactly that case -- it is the tell.
        if not r.buttons and not r.regions:
            reason = f"no features ({img.shape[1]}x{img.shape[0]} source)"
            if verbose:
                print(f"  [{r.index}] dropped: {reason}")
            results.append({"stem": stem, "fingerprint": None,
                            "dropped": reason})
            continue

        # Counted, not silently skipped: a catalogue that loses records without
        # saying so is how the 56 unkeyable legacy fingerprints went unnoticed.
        # The sibling count goes in the reason because it is what makes the
        # verdict reviewable -- 3 buttons is unremarkable on its own and
        # damning beside 19.
        if (len(remotes) > 1
                and len(r.buttons) < bcfg.sibling_min_button_ratio * max_buttons
                and len(r.buttons) < bcfg.sibling_min_buttons):
            reason = (f"sparse beside sibling ({len(r.buttons)} buttons "
                      f"vs {max_buttons} on the same photo)")
            if verbose:
                print(f"  [{r.index}] dropped: {reason}")
            results.append({"stem": stem, "fingerprint": None,
                            "dropped": reason})
            continue

        (out_dir / "fp" / f"{stem}.json").write_text(json.dumps(fp, indent=2))

        if verbose:
            print(f"  [{r.index}] aspect={r.aspect:.2f}  "
                  f"buttons={len(r.buttons)}  "
                  f"chromatic={fp['stats']['n_chromatic']}  "
                  f"flip={orient['flip']}{'?' if orient['ambiguous'] else ''} (conf {orient['confidence']:.2f} {orient.get('source','geometry')})  "
                  f"quality={fp['extract_quality']:.2f}")
            print(f"       colours: {color_summary(r.buttons)}")
            if use_ocr:
                print(f"       text={len(r.regions)} "
                      f"(labels {fp['stats']['n_labelled']}/{len(r.buttons)}, "
                      f"captions {fp['stats']['n_captions']}) "
                      f"cut={len(r.suppressed)}  "
                      f"brand={r.brand['name'] if r.brand else '-'}"
                      f"{'?' if r.brand and r.brand['source'] != 'list' else ''}  "
                      f"model={r.model_code or '-'}")

        results.append({"stem": stem, "fingerprint": fp, "dropped": None})

    return results


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("images", nargs="*", type=Path)
    ap.add_argument("--dir", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--no-ensemble", action="store_true")
    ap.add_argument("--no-ocr", action="store_true",
                    help="skip OCR entirely (fast, for geometry tuning)")
    ap.add_argument("--ocr-consensus", action="store_true",
                    help="run every installed OCR engine and keep agreed "
                         "regions (offline catalog build)")
    ap.add_argument("--ocr-width", type=int, default=None,
                    help="override OCR upscale width (default "
                         f"{CFG.ocr.min_width}). The upscale is the biggest "
                         "lever on label recall, but it is also the peak "
                         "memory user: a 4000px image at the default OOMs on "
                         "a box with under ~1 GB free. Lower it to get a "
                         "large image through, and record that you did.")
    ap.add_argument("--stem", default=None,
                    help="record identifier for the output files, replacing "
                         "the filename stem. Use the catalogue's primary key "
                         "for a DB-driven build; source filenames are not "
                         "unique and collide silently in fp/. Single image "
                         "only.")
    ap.add_argument("--min-long-side", type=int, default=None,
                    help="refuse source images whose long side is under this "
                         f"(default {CFG.normalize.min_source_long_side}). The crop "
                         "is upscaled to a fixed width whatever the source, so "
                         "a thumbnail yields confident buttons traced from "
                         "interpolation. Lower it only for a drop you have "
                         "looked at.")
    ap.add_argument("--no-watermark-filter", action="store_true",
                    help="keep source-watermark text. Use when the images are "
                         "clean; the filter is only needed for scraped ones.")
    ap.add_argument("--watermark-terms", default=None,
                    help="comma-separated stamps to strip, overriding the "
                         f"default {','.join(CFG.ocr.watermark_terms) or '(none)'}. "
                         "Empty string disables the filter.")
    args = ap.parse_args()

    if args.min_long_side is not None:
        CFG.normalize.min_source_long_side = args.min_long_side
    print(f"source floor: long side >= {CFG.normalize.min_source_long_side}px")

    if args.no_watermark_filter:
        CFG.ocr.watermark_filter = False
    if args.watermark_terms is not None:
        terms = tuple(t.strip().upper() for t in args.watermark_terms.split(",")
                      if t.strip())
        CFG.ocr.watermark_terms = terms
        CFG.ocr.watermark_filter = bool(terms)

    # Stated every run: a build that silently kept or stripped a watermark is
    # a catalog that cannot be compared with the one before it.
    print(f"watermark filter: "
          f"{'on ' + ','.join(CFG.ocr.watermark_terms) if CFG.ocr.watermark_filter else 'off'}")

    if args.ocr_width:
        CFG.ocr.min_width = args.ocr_width
        print(f"OCR upscale width overridden to {args.ocr_width} "
              f"(label recall will be lower than the catalog default)")

    paths = list(args.images)
    if args.dir:
        for ext in ("*.jpg", "*.jpeg", "*.png", "*.webp"):
            paths.extend(sorted(args.dir.glob(ext)))
    if not paths:
        ap.error("no images given")

    if not args.no_ocr:
        engines = available_engines()
        if not engines:
            print("!! no OCR engine installed -- continuing without OCR. "
                  "Run install.sh.")
            args.no_ocr = True
        else:
            using = engines if args.ocr_consensus else engines[:1]
            print(f"OCR: {', '.join(using)}"
                  f"{'  (consensus)' if args.ocr_consensus else ''}")

    total = 0
    skipped: list[tuple[Path, str]] = []

    for p in paths:
        # One malformed image must not take the batch down with it. A scraped
        # catalog of 10k-50k images reliably contains some, and a run that
        # dies two thirds of the way through costs hours.
        try:
            found = process_image(p, args.out,
                                  ensemble=not args.no_ensemble,
                                  use_ocr=not args.no_ocr,
                                  consensus=args.ocr_consensus,
                                  stem=args.stem)
        except Exception as exc:                       # noqa: BLE001
            skipped.append((p, f"{type(exc).__name__}: {exc}"))
            print(f"  !! {p.name}: {type(exc).__name__}: {exc}")
            continue

        if found is None:
            skipped.append((p, "unreadable"))
        elif not found:
            skipped.append((p, "no remote found"))
        else:
            kept = [f for f in found if not f["dropped"]]
            for f in found:
                if f["dropped"]:
                    skipped.append((p, f["dropped"]))
            total += len(kept)

    print(f"\n{len(paths)} image(s) -> {total} remote(s) extracted"
          f"{f', {len(skipped)} skipped' if skipped else ''}")

    if skipped:
        # Written out because at catalog scale the console scrollback is gone
        # long before anyone reads it, and a silent skip is a record missing
        # from the catalog that nothing downstream will ever report.
        args.out.mkdir(parents=True, exist_ok=True)
        report = args.out / "skipped.txt"
        with report.open("a") as fh:
            for p, why in skipped:
                fh.write(f"{p}\t{why}\n")

        by_reason: dict[str, int] = {}
        for _, why in skipped:
            # Group on the reason, not its detail: "no features" carries the
            # source dimensions, which would otherwise split the summary into
            # one line per image size.
            key = why.split(":")[0].split(" (")[0]
            by_reason[key] = by_reason.get(key, 0) + 1
        print("  skipped: " + ", ".join(f"{n} {k}" for k, n in sorted(by_reason.items())))
        print(f"  -> {report}")


if __name__ == "__main__":
    main()
