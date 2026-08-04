#!/usr/bin/env bash
# Build the catalog: extract the product photos, then build the token index.
#
# One image per process. Looping extract_remotes inside a single process gets
# OOM-killed; peak RSS is ~700 MB on a 5 MP image at the default OCR upscale.
#
#   docker compose exec laravel php artisan rcu:legacy-manifest --out=/data/work/primary.txt
#   docker compose --profile build run --rm extract --manifest /data/work/primary.txt --jobs 4
#
# Without --manifest every file in $PHOTOS is extracted. That is right for a
# directory of remotes and wrong for the legacy `files/` directory, a third of
# which is replacement-model promos and instruction sheets hung off the same
# products at delta >= 1. Only the database knows which is which, and this
# container has no database -- hence the manifest.
set -euo pipefail

PHOTOS=${PHOTOS:-/data/files}
OUT=${OUT:-/data/work}
MANIFEST=${MANIFEST:-}
JOBS=1
# Empty means "whatever app/config.py says". Only set this to extract a drop of
# deliberately small images you have looked at first.
MIN_LONG_SIDE=${MIN_LONG_SIDE:-}

while [ $# -gt 0 ]; do
    case "$1" in
        --jobs) JOBS="$2"; shift 2 ;;
        --photos) PHOTOS="$2"; shift 2 ;;
        --out) OUT="$2"; shift 2 ;;
        --manifest) MANIFEST="$2"; shift 2 ;;
        --min-long-side) MIN_LONG_SIDE="$2"; shift 2 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

cd /srv/service-python
mkdir -p "$OUT"

if [ -n "$MANIFEST" ]; then
    [ -f "$MANIFEST" ] || { echo "manifest not found: $MANIFEST" >&2; exit 2; }

    # One path per line, relative to $PHOTOS, as rcu:legacy-manifest writes
    # it -- not a bare basename: most originals are gone from files/ and the
    # manifest points at the imagecache derivative instead. A path that is
    # not on disk is dropped here *and counted*: a build that quietly extracts
    # fewer records than the catalogue lists is how a catalog loses rows.
    absent=0
    images=()
    : > "$OUT/missing.txt"   # truncated per run, or two builds' gaps merge

    while IFS= read -r name; do
        [ -z "$name" ] && continue
        if [ -f "$PHOTOS/$name" ]; then
            images+=("$PHOTOS/$name")
        else
            absent=$((absent + 1))
            echo "not on disk: $name" >> "$OUT/missing.txt"
        fi
    done < "$MANIFEST"

    echo "manifest $MANIFEST: ${#images[@]} present, $absent absent (see $OUT/missing.txt)"
else
    mapfile -t images < <(find "$PHOTOS" -maxdepth 1 -type f \
        \( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' -o -iname '*.webp' \) | sort)
fi

echo "${#images[@]} image(s) from $PHOTOS -> $OUT  (jobs=$JOBS)"
[ "${#images[@]}" -eq 0 ] && { echo "nothing to extract"; exit 1; }

# Each worker is a fresh process, so memory is bounded per image. Raise --jobs
# only as far as (available RAM / ~700 MB) allows.
extra=()
[ -n "$MIN_LONG_SIDE" ] && extra+=(--min-long-side "$MIN_LONG_SIDE")

printf '%s\0' "${images[@]}" \
  | xargs -0 -P "$JOBS" -I{} python scripts/extract_one.py "{}" --out "$OUT" \
        "${extra[@]}" \
  || echo "one or more images failed; see $OUT/skipped.txt"

# Each worker appends its own reason, and one process per image means nothing
# ever prints a total. Say here what the build refused and why: at 13k images
# the per-image lines are long gone, and "extracted N" alone cannot distinguish
# a clean drop from one that was three quarters thumbnails.
if [ -s "$OUT/skipped.txt" ]; then
    echo
    echo "excluded ($(wc -l < "$OUT/skipped.txt") image(s), see $OUT/skipped.txt):"
    sed 's/.*\t//; s/ (.*//' "$OUT/skipped.txt" | sort | uniq -c | sort -rn \
      | sed 's/^/  /'
fi

python scripts/build_index.py --fp "$OUT/fp" --out "$OUT/index/tokens.npz"

echo
echo "done. Reload the running service and refresh the catalog table:"
echo "  (the manifest and the import must come from the same catalogue read)"
echo "  docker compose exec laravel php artisan rcu:import-catalog --legacy --prune --reindex"
