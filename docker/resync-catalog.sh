#!/usr/bin/env bash
# Bring both catalog consumers back in step with work/fp, then measure what the
# result actually promises. Run on the host, from the compose project root,
# after an extraction finishes.
#
#   ./docker/resync-catalog.sh                      # index, import, verify
#   ./docker/resync-catalog.sh --calibrate          # ... and sweep the bands
#   ./docker/resync-catalog.sh --snapshot           # serve a build in progress
#   DOCKER='sudo -n docker' ./docker/resync-catalog.sh   # rcud: no docker group
#
# There are two consumers of an extraction and they must come from the same
# run: the token index the service holds in memory, and the rcu_fingerprints
# table Laravel joins catalogue metadata onto. When they drift, matching does
# not fail -- it succeeds and returns record_ids that resolve to no row, which
# reads as a database bug and is not. Running the steps by hand in the wrong
# order, or forgetting the second, is exactly how that happens at 07:00 after
# a 30-hour build, which is why this is a script and not a section of a README.
#
# It refuses to run while an extraction is still writing. `fp/` fills
# incrementally, so indexing a live build produces a perfectly valid index of a
# partial catalog, and nothing downstream can tell that from a finished one.
set -euo pipefail

cd "$(dirname "$0")/.."

DOCKER=${DOCKER:-docker}
COMPOSE="$DOCKER compose"
WORK=${WORK:-./work}
CALIBRATE=0
CHECK_DECODE=0
SAMPLE=${SAMPLE:-500}
FORCE=0
SNAPSHOT=0
FP_NAME=fp

while [ $# -gt 0 ]; do
    case "$1" in
        --calibrate) CALIBRATE=1; shift ;;
        --check-decode) CHECK_DECODE=1; shift ;;
        --sample) SAMPLE="$2"; shift 2 ;;
        --snapshot) SNAPSHOT=1; shift ;;
        --force) FORCE=1; shift ;;
        -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

# ---------------------------------------------------------------- 0. is it done

# `docker compose --profile build run` names its container <project>-extract-run-*,
# which `compose ps` does not list as the `extract` service. Ask the daemon for
# the container, not compose for the service.
running=$($DOCKER ps --filter "name=extract-run" --format '{{.Names}}' | head -5)
if [ -n "$running" ]; then
    echo "an extraction is still running:" >&2
    echo "$running" | sed 's/^/  /' >&2
    if [ "$SNAPSHOT" -eq 1 ]; then
        echo "--snapshot: pinning both consumers to what exists right now" >&2
    elif [ "$FORCE" -eq 0 ]; then
        echo >&2
        echo "fp/ is written incrementally, so indexing now yields a valid index" >&2
        echo "of a partial catalog and nothing downstream can tell. Use" >&2
        echo "--snapshot to serve what is extracted so far, wait for the build," >&2
        echo "or --force if you know this build is not writing to $WORK." >&2
        exit 1
    else
        echo "--force given; continuing against a live build" >&2
    fi
fi

[ -d "$WORK/fp" ] || { echo "no fingerprints at $WORK/fp" >&2; exit 1; }

# ------------------------------------------------------------- 0b. the snapshot

# Serving a half-built catalog is a reasonable thing to want, but not by
# pointing the two consumers at a directory a build is still writing into: the
# index would be built at one moment and the catalog table imported at another,
# and the two would disagree by however many records landed in between. That is
# the same drift the count check at the end exists to catch, arriving by a route
# the check cannot see, because both counts move.
#
# So copy first and let everything downstream read the copy. The copy is also
# where a half-written file is caught: a fingerprint being flushed as `cp` reads
# it lands as truncated JSON, and the index build would die on it partway
# through. Parse each one and drop what does not parse -- it is in the next
# snapshot, complete.
if [ "$SNAPSHOT" -eq 1 ]; then
    FP_NAME=fp.snapshot
    say "snapshotting $WORK/fp -> $WORK/$FP_NAME"
    rm -rf "${WORK:?}/$FP_NAME"
    mkdir -p "$WORK/$FP_NAME"

    # One interpreter for the whole directory, not one per file: at 13k
    # fingerprints a per-file `python3 -c` is minutes of process startup on a
    # two-core box that is already extracting.
    python3 - "$WORK/fp" "$WORK/$FP_NAME" <<'PY'
import json, shutil, sys
from pathlib import Path

src, dst = Path(sys.argv[1]), Path(sys.argv[2])
copied = partial = 0
for f in sorted(src.glob("*.json")):
    try:
        json.loads(f.read_text())
    except Exception:
        partial += 1
        continue
    shutil.copy2(f, dst / f.name)
    copied += 1

print(f"{copied} fingerprint(s) copied")
if partial:
    print(f"{partial} still being written, left for the next snapshot")
PY
fi

# ------------------------------------------------------- 1. what the build did

say "what the build produced"
fp_count=$(find "$WORK/$FP_NAME" -name '*.json' | wc -l)
echo "fingerprints: $fp_count"

if [ -s "$WORK/skipped.txt" ]; then
    echo "excluded ($(wc -l < "$WORK/skipped.txt") image(s)):"
    sed 's/.*\t//; s/ (.*//' "$WORK/skipped.txt" | sort | uniq -c | sort -rn | sed 's/^/  /'
fi
if [ -s "$WORK/missing.txt" ]; then
    echo "not on disk: $(wc -l < "$WORK/missing.txt") manifest line(s)"
fi

# The manifest is the catalogue's own list. A build that stopped early leaves no
# other trace: fp/ is as plausible at 40% as at 100%.
if [ -f "$WORK/primary.txt" ]; then
    lines=$(grep -c . "$WORK/primary.txt" || true)
    last=$(find "$WORK/$FP_NAME" -name '*.json' -printf '%f\n' | sort | tail -1)
    echo "manifest: $lines line(s); alphabetically last fingerprint: ${last:-none}"
    echo "  (the manifest is sorted, so a last stem well before its end means"
    echo "   the build did not finish -- check $WORK/build.log)"
fi

if [ "$fp_count" -eq 0 ]; then
    echo "nothing to index" >&2
    exit 1
fi

# ------------------------------------------------------ 2. optional decode check

if [ "$CHECK_DECODE" -eq 1 ]; then
    say "decode invariant"
    # Asserts imread and imdecode agree and that decoding is stable across
    # repeats. A truncated JPEG decodes into a partly uninitialised buffer and
    # gives a different image each call, which broke the build and the service
    # differently -- see the gotcha in CLAUDE.md.
    $COMPOSE --profile build run --rm --entrypoint python extract \
        scripts/check_decode.py --dir /data/files
fi

# --------------------------------------------------------------- 3. the index

say "building the token index"
# `extract` is the only container with work/ writable; rcu-service mounts it
# read-only precisely so that the index cannot be rewritten under a running
# service by anything but this step.
$COMPOSE --profile build run --rm --entrypoint python extract \
    scripts/build_index.py --fp "/data/work/$FP_NAME" --out /data/work/index/tokens.npz

# --------------------------------------------------------------- 4. the table

say "importing the catalog table"
# --fp so the table is loaded from the same set the index was built from.
# Without it the import reads RCU_FP_DIR, the live directory, and a snapshot
# run would import records the index does not contain -- the exact drift this
# script ends by asserting against.
$COMPOSE exec -T laravel php artisan rcu:import-catalog --legacy --prune --reindex \
    --fp "/data/work/$FP_NAME"

# ---------------------------------------------------------------- 5. do they agree

say "verifying the two consumers agree"
# HOME=/tmp because psysh insists on a writable home and the image gives the
# web user /var/www, which is not. Without it tinker exits 1 on a config path
# and the count comes back empty, which this step would then report as a
# failure to read the database.
rows=$($COMPOSE exec -T -e HOME=/tmp laravel php artisan tinker --execute \
    'echo DB::table("rcu_fingerprints")->count();' 2>/dev/null | tr -cd '0-9')
echo "fingerprint files: $fp_count"
echo "rcu_fingerprints rows: ${rows:-unknown}"

if [ -z "$rows" ]; then
    echo "could not read the row count; check the import output above" >&2
    exit 1
fi
if [ "$rows" != "$fp_count" ]; then
    echo >&2
    echo "MISMATCH: the index and the table are not from the same extraction." >&2
    echo "Matching will still 'work' and return record_ids that resolve to no" >&2
    echo "row. Do not calibrate or deploy against this state." >&2
    exit 1
fi
echo "in step."

# ---------------------------------------------------------------- 6. calibrate

if [ "$CALIBRATE" -eq 1 ]; then
    say "calibrating the confidence bands"
    # Every query is a real upload to the running service, so this is the query
    # path and not the offline metric -- which is the point, and also why it is
    # sampled: 13763 uploads is several hours. --sample 0 runs the lot.
    : "${RCU_INTERNAL_TOKEN:?set RCU_INTERNAL_TOKEN (the service rejects an unauthenticated query)}"

    # The image carries the code, so a checkout that has the script proves
    # nothing about the container that runs it. This failed exactly that way
    # the first time: calibrate_bands.py landed in session 6, the extract image
    # was older, and `run` reported a missing file with no hint as to why.
    if ! $COMPOSE --profile build run --rm --entrypoint test extract \
            -f scripts/calibrate_bands.py 2>/dev/null; then
        echo "the extract image has no scripts/calibrate_bands.py -- it predates" >&2
        echo "the script. Rebuild it first:  $COMPOSE build extract" >&2
        exit 1
    fi
    manifest=()
    [ -f "$WORK/primary.txt" ] && manifest=(--manifest /data/work/primary.txt)

    $COMPOSE --profile build run --rm \
        -e "RCU_INTERNAL_TOKEN=$RCU_INTERNAL_TOKEN" \
        --entrypoint python extract \
        scripts/calibrate_bands.py \
            --photos /data/files "${manifest[@]}" \
            --fp "/data/work/$FP_NAME" \
            --url http://rcu-service:8600 \
            --token "$RCU_INTERNAL_TOKEN" \
            --sample "$SAMPLE" \
            --csv /data/work/bands.csv \
        2>&1 | tee "$WORK/bands.log"

    echo
    echo "per-query rows in $WORK/bands.csv, full output in $WORK/bands.log"
    echo "Before believing the table: look at the distribution of top scores in"
    echo "the CSV. A score repeated across dozens of different remotes is not a"
    echo "score, and that is what the last calibration turned out to be."
fi

say "done"
