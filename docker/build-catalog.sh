#!/usr/bin/env bash
# Build the catalog: extract every photo, then build the token index.
#
# One image per process. Looping extract_remotes inside a single process gets
# OOM-killed; peak RSS is ~700 MB on a 5 MP image at the default OCR upscale.
#
#   docker compose --profile build run --rm extract
#   docker compose --profile build run --rm extract --jobs 4
set -euo pipefail

PHOTOS=${PHOTOS:-/data/files}
OUT=${OUT:-/data/work}
JOBS=1

while [ $# -gt 0 ]; do
    case "$1" in
        --jobs) JOBS="$2"; shift 2 ;;
        --photos) PHOTOS="$2"; shift 2 ;;
        --out) OUT="$2"; shift 2 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

cd /srv/service-python
mkdir -p "$OUT"

mapfile -t images < <(find "$PHOTOS" -maxdepth 1 -type f \
    \( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' -o -iname '*.webp' \) | sort)

echo "${#images[@]} image(s) from $PHOTOS -> $OUT  (jobs=$JOBS)"
[ "${#images[@]}" -eq 0 ] && { echo "nothing to extract"; exit 1; }

# Each worker is a fresh process, so memory is bounded per image. Raise --jobs
# only as far as (available RAM / ~700 MB) allows.
printf '%s\0' "${images[@]}" \
  | xargs -0 -P "$JOBS" -I{} python scripts/extract_one.py "{}" --out "$OUT" \
  || echo "one or more images failed; see $OUT/skipped.txt"

python scripts/build_index.py --fp "$OUT/fp" --out "$OUT/index/tokens.npz"

echo
echo "done. Reload the running service and refresh the catalog table:"
echo "  docker compose exec laravel php artisan rcu:import-catalog --legacy --prune --reindex"
