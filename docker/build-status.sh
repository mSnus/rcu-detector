#!/usr/bin/env bash
# Is the catalogue build still running, and if it stopped, did it finish?
#
#   ./docker/build-status.sh
#   DOCKER='sudo -n docker' ./docker/build-status.sh     # rcud: no docker group
#
# The two questions are separate and the second is the one that matters. A
# build that is killed leaves exactly what a build that finished leaves: no
# container, a log ending in something plausible, and a work/fp full of
# fingerprints. The first full run stopped 20 hours in with a worker killed by
# the OOM killer, printed "one or more images failed", indexed the two thirds
# it had and said `done` -- and nothing distinguished that from success until
# somebody counted.
#
# So this counts. Every manifest image must have produced either a fingerprint
# or a line in skipped.txt; anything else was never attempted, and that number
# is the answer.
set -euo pipefail

cd "$(dirname "$0")/.."

DOCKER=${DOCKER:-docker}
WORK=${WORK:-./work}
MANIFEST=${MANIFEST:-}

[ -d "$WORK/fp" ] || { echo "no fingerprints at $WORK/fp" >&2; exit 1; }

# Whichever manifest the running build was given, else the primary one.
if [ -z "$MANIFEST" ]; then
    if [ -f "$WORK/remainder.txt" ] && [ -f "$WORK/primary.txt" ] \
       && [ "$WORK/remainder.txt" -nt "$WORK/primary.txt" ]; then
        MANIFEST="$WORK/remainder.txt"
    else
        MANIFEST="$WORK/primary.txt"
    fi
fi

running=$($DOCKER ps --filter "name=extract-run" --format '{{.Names}} {{.Status}}' | head -1)

echo "== extraction"
if [ -n "$running" ]; then
    echo "RUNNING   $running"
else
    echo "not running"
fi

echo
echo "== what the log last said"
# A detached `compose run -d` writes its per-image output to the container's
# log, not to build.log -- build.log then holds only the container id. Ask the
# container while it exists, and fall back to the file once it is gone.
if [ -n "$running" ]; then
    cid=$($DOCKER ps -q --filter "name=extract-run" | head -1)
    $DOCKER logs --tail 3 "$cid" 2>&1 | sed 's/^/  /'
    echo "  (follow it with: $DOCKER logs -f $cid)"
elif [ -f "$WORK/build.log" ]; then
    if grep -q "^ABORTED" "$WORK/build.log"; then
        echo "!! ABORTED -- a worker was killed; everything after it was skipped"
    fi
    tail -3 "$WORK/build.log" | sed 's/^/  /'
    echo "  (log last written $(date -r "$WORK/build.log" '+%Y-%m-%d %H:%M'))"
else
    echo "  no build.log"
fi

echo
echo "== accounting against $(basename "$MANIFEST")"
python3 - "$WORK" "$MANIFEST" <<'PY'
import glob, os, sys

work, manifest = sys.argv[1], sys.argv[2]

done = {os.path.basename(f).rsplit("_", 1)[0] for f in glob.glob(f"{work}/fp/*.json")}
skipped = set()
skip_file = f"{work}/skipped.txt"
if os.path.exists(skip_file):
    for line in open(skip_file):
        parts = line.split()
        if parts:
            skipped.add(os.path.splitext(os.path.basename(parts[0]))[0])

wanted, missing = [], []
for line in open(manifest):
    line = line.strip()
    if not line:
        continue
    stem = os.path.splitext(os.path.basename(line))[0]
    wanted.append(stem)
    if stem not in done and stem not in skipped:
        missing.append(stem)

n = len(wanted)
did = n - len(missing)
pct = 100.0 * did / n if n else 0.0
print(f"  {n} image(s) asked for")
print(f"  {did} accounted for ({pct:.1f}%) -- {len(done & set(wanted))} extracted, "
      f"{len(skipped & set(wanted))} excluded")
print(f"  {len(missing)} never attempted")
if missing:
    print(f"    e.g. {', '.join(sorted(missing)[:3])}")
PY

echo
if [ -n "$running" ]; then
    echo "Still going. Re-run this to see the 'never attempted' number fall."
else
    echo "FINISHED when 'never attempted' is 0 (or only the images you know are"
    echo "unreadable). If it is not 0 and nothing is running, the build stopped"
    echo "early -- write the remainder to a manifest and start it again:"
    echo
    echo "  build-catalog --manifest /data/work/remainder.txt --jobs 1 --batch 200"
fi
