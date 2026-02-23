# idempotent helper for downloading a PBF, fetching a foot.lua profile, and
# running osrm-extract/partition/customize inside a container.  Can be run
# from anywhere; paths are resolved relative to the location of this script.
#!/usr/bin/env bash
set -euo pipefail

# idempotent helper for downloading a PBF, fetching a foot.lua profile, and
# running osrm-extract/partition/customize inside a container.  Intended to be
# run from the repository root; writes into transport-backend/cache/osrm.
#
# Usage:
#   ./transport-backend/scripts/build_osrm.sh [options]
#
# Options:
#   --pbf-url URL        Geofabrik or other PBF URL (can be file:// path).
#                        Defaults to the UK extract on Geofabrik.
#   --sample             use a tiny example extract (Monaco) instead of the
#                        default huge UK file; useful for quick local testing.
#   --threads N          Number of threads passed to osrm-extract (default 1).
#   --start              After building, start an osrm-routed container.
#   --runtime NAME       docker or podman; autodetected if omitted.
#   --platform PLATFORM  pass this string to "docker|podman run --platform".
#   --force              rebuild even if a timestamp file exists.
#   -h|--help            Show this message.

# defaults
PBF_URL="https://download.geofabrik.de/europe/united-kingdom/england-latest.osm.pbf"
THREADS=1
START=false
FORCE=false
RUNTIME=""
PLATFORM=""

while (("$#")); do
    case "$1" in
        --pbf-url) PBF_URL="$2"; shift 2;;
        --sample)
            # Monaco is the smallest full-country extract that Geofabrik
            # publishes.  It’s only a couple of megabytes, so the OSRM
            # preprocessing is very fast – great for development machines or
            # CI jobs where you don’t want to wait for a UK‑wide build.
            PBF_URL="https://download.geofabrik.de/europe/monaco-latest.osm.pbf"
            shift;;
        --threads) THREADS="$2"; shift 2;;
        --start) START=true; shift;;
        --force) FORCE=true; shift;;
        --runtime) RUNTIME="$2"; shift 2;;
        --platform) PLATFORM="--platform $2"; shift 2;;
        -h|--help)
            grep '^#' "$0" | cut -c2- || true
            exit 0;;
        *) echo "Unknown option: $1" >&2; exit 1;;
    esac
done

# choose runtime if not supplied
if [ -z "$RUNTIME" ]; then
    if command -v podman >/dev/null 2>&1; then
        RUNTIME=podman
    elif command -v docker >/dev/null 2>&1; then
        RUNTIME=docker
    else
        echo "Neither podman nor docker are installed" >&2
        exit 1
    fi
fi

# resolve base directory relative to this script so callers need not cd
# around; we assume the script lives in transport-backend/scripts
HERE=$(cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(cd "$HERE/.." && pwd)
CACHE_DIR="$REPO_ROOT/cache/osrm"
mkdir -p "$CACHE_DIR"

# derive filename and base prefix
PBF_NAME=$(basename "$PBF_URL")
# support file://
if [[ "$PBF_NAME" == file://* ]]; then
    PBF_NAME=$(basename "${PBF_URL#file://}")
fi
PREFIX=${PBF_NAME%.osm.pbf}
TS_FILE="$CACHE_DIR/$PREFIX.osrm.timestamp"

# download profile if missing
if [ ! -f "$CACHE_DIR/foot.lua" ]; then
    echo "fetching foot.lua profile" >&2
    curl -fsSL https://raw.githubusercontent.com/Project-OSRM/osrm-backend/master/profiles/foot.lua \
        -o "$CACHE_DIR/foot.lua"
fi

# download PBF if necessary (or forced)
if [ "$FORCE" = true ] || [ ! -f "$CACHE_DIR/$PBF_NAME" ]; then
    echo "downloading PBF from $PBF_URL" >&2
    # attempt with curl/wget/aria2c
    if command -v curl >/dev/null 2>&1; then
        curl -fL --retry 3 -o "$CACHE_DIR/$PBF_NAME" "$PBF_URL" || true
    fi
    if [ ! -s "$CACHE_DIR/$PBF_NAME" ] && command -v wget >/dev/null 2>&1; then
        wget -O "$CACHE_DIR/$PBF_NAME" "$PBF_URL" || true
    fi
    if [ ! -s "$CACHE_DIR/$PBF_NAME" ] && command -v aria2c >/dev/null 2>&1; then
        aria2c -x4 -o "$CACHE_DIR/$PBF_NAME" "$PBF_URL" || true
    fi
    if [ ! -s "$CACHE_DIR/$PBF_NAME" ]; then
        echo "failed to download PBF" >&2
        exit 2
    fi
fi

# determine whether we need to build
if [ "$FORCE" != true ] && [ -f "$TS_FILE" ]; then
    echo "dataset already appears built (timestamp present); use --force to rebuild" >&2
else
    echo "building dataset ($PREFIX) with $THREADS thread(s)" >&2
    # run each step via container
    $RUNTIME run --rm $PLATFORM -v "$CACHE_DIR":/data:Z osrm/osrm-backend \
        osrm-extract -p /data/foot.lua -t $THREADS /data/$PBF_NAME
    $RUNTIME run --rm $PLATFORM -v "$CACHE_DIR":/data:Z osrm/osrm-backend \
        osrm-partition /data/$PREFIX.osrm
    $RUNTIME run --rm $PLATFORM -v "$CACHE_DIR":/data:Z osrm/osrm-backend \
        osrm-customize /data/$PREFIX.osrm
    date +%s > "$TS_FILE"
    echo "build complete" >&2
    # ensure data files are readable by whatever user the container runs as
    # (some builds create 600-permission files which rootless podman can't
    # read, leading to mysterious "Missing/Broken File" errors).
    chmod -R a+r "$CACHE_DIR"
fi

if [ "$START" = true ]; then
    echo "starting osrm-routed container" >&2

    # restore a previously-hidden timestamp if one remains from an earlier
    # failed start; this keeps the checks below from complaining and ensures
    # we always run the router against the correct dataset.
    if [ -f "$TS_FILE.hidden" ]; then
        mv "$TS_FILE.hidden" "$TS_FILE"
    fi

    # OSRM will scan everything under /data and occasionally attempts to
    # interpret files ending in ".osrm.*" as archive streams.  Our timestamp
    # file (`$PREFIX.osrm.timestamp`) is just a plain text marker and causes
    # the container to panic with an "Inappropriate ioctl for device" error on
    # macOS/Podman.  We temporarily move it out of the way, then restore it
    # unconditionally using a trap so that even if the router command exits
    # non-zero the timestamp returns.
    HIDDEN_TS=""
    if [ -f "$TS_FILE" ]; then
        HIDDEN_TS="$TS_FILE.hidden"
        mv "$TS_FILE" "$HIDDEN_TS"
    fi

    restore_ts() {
        if [ -n "$HIDDEN_TS" ] && [ -f "$HIDDEN_TS" ]; then
            mv "$HIDDEN_TS" "$TS_FILE"
        fi
    }
    trap restore_ts EXIT

    # run the router; if it fails we still hit the EXIT trap above
    $RUNTIME run --rm $PLATFORM -p 5001:5000 -v "$CACHE_DIR":/data:Z osrm/osrm-backend \
        osrm-routed /data/$PREFIX.osrm
    # trap will restore timestamp automatically
fi
