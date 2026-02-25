#!/usr/bin/env bash
set -euo pipefail

# Simple helper to download a PBF and run OSRM (MLD) using the official Docker image
# Defaults: downloads the provided URL and runs osrm on host port 5012

PBF_URL_DEFAULT="https://download.geofabrik.de/europe/united-kingdom/england/lancashire-latest.osm.pbf"
# Default data dir: directory next to this script (transport-backend/osrm/data)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR_DEFAULT="$SCRIPT_DIR/data"
PORT_DEFAULT=5012

PBF_URL="${1:-$PBF_URL_DEFAULT}"
DATA_DIR="${2:-$DATA_DIR_DEFAULT}"
PORT="${3:-$PORT_DEFAULT}"

echo "Using PBF URL: $PBF_URL"
echo "Data directory: $DATA_DIR"
echo "Host port: $PORT -> container port 5012"

# Detect a usable container runtime (prefer Docker if its daemon is responsive,
# otherwise try Podman). This avoids "docker: Cannot connect to the Docker
# daemon" errors on macOS when Docker Desktop isn't running.
RUNTIME=""
if command -v docker >/dev/null 2>&1; then
  if docker info >/dev/null 2>&1; then
    RUNTIME="docker"
  else
    echo "docker found but daemon not reachable (it may not be running)."
  fi
fi
if [ -z "$RUNTIME" ] && command -v podman >/dev/null 2>&1; then
  if podman info >/dev/null 2>&1; then
    RUNTIME="podman"
  else
    echo "podman found but machine not started (on macOS run: podman machine init; podman machine start)."
  fi
fi
if [ -z "$RUNTIME" ]; then
  echo "Error: neither a usable Docker nor Podman runtime was found.\n" >&2
  echo " - To use Docker on macOS: start Docker Desktop (open -a Docker) and wait until 'docker info' succeeds." >&2
  echo " - To use Podman: install podman (brew install podman) and start the VM: 'podman machine init && podman machine start'." >&2
  exit 1
fi

echo "Using container runtime: $RUNTIME"

mkdir -p "$DATA_DIR"

# Derive base names
BASE_NAME=$(basename "$PBF_URL")
if [[ "$BASE_NAME" == *.osm.pbf ]]; then
  NAME=${BASE_NAME%.osm.pbf}
elif [[ "$BASE_NAME" == *.pbf ]]; then
  NAME=${BASE_NAME%.pbf}
else
  NAME=${BASE_NAME%.*}
fi

PBF_PATH="$DATA_DIR/$BASE_NAME"
OSRM_BASE="$DATA_DIR/$NAME.osrm"

if [ ! -f "$PBF_PATH" ]; then
  echo "Downloading PBF to $PBF_PATH (this may be large)..."
  curl -L --progress-bar -o "$PBF_PATH" "$PBF_URL"
else
  echo "PBF already exists at $PBF_PATH, skipping download."
fi

DOCKER_IMAGE="docker.io/osrm/osrm-backend:latest"

echo "Step 1: osrm-extract (creates $NAME.osrm)"
if [ ! -f "$DATA_DIR/$NAME.osrm" ]; then
  $RUNTIME run --rm -t -v "$DATA_DIR":/data "$DOCKER_IMAGE" osrm-extract -p /opt/car.lua /data/"$BASE_NAME"
else
  echo "  $NAME.osrm already exists, skipping extract."
fi

echo "Step 2: osrm-partition"
if [ ! -f "$DATA_DIR/$NAME.osrm.partition" ]; then
  $RUNTIME run --rm -t -v "$DATA_DIR":/data "$DOCKER_IMAGE" osrm-partition /data/"$NAME.osrm"
else
  echo "  partition files already exist, skipping partition."
fi

echo "Step 3: osrm-customize"
if [ ! -f "$DATA_DIR/$NAME.osrm.mldgrids" ] && [ ! -f "$DATA_DIR/$NAME.osrm.hsgr" ]; then
  # customize will generate .mldgrids/.hsgr/.ramIndex/... depending on algorithm
  $RUNTIME run --rm -t -v "$DATA_DIR":/data "$DOCKER_IMAGE" osrm-customize /data/"$NAME.osrm"
else
  echo "  customize outputs already exist, skipping customize."
fi

echo "Starting osrm-routed on host port $PORT (container port 5012)"
echo "Press Ctrl-C to stop the server."

# Ensure any existing container with the same name is removed (avoid name clash)
CONTAINER_NAME="scc200-osrm"
if $RUNTIME ps -a --filter "name=$CONTAINER_NAME" --format "{{.Names}}" 2>/dev/null | grep -q "^$CONTAINER_NAME$"; then
  echo "Removing existing container $CONTAINER_NAME"
  $RUNTIME rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
fi

# Run the router detached and then tail logs. This is more robust on macOS
# where attaching directly can hit a proxy / attach error with Podman.
$RUNTIME run --name "$CONTAINER_NAME" -d -p ${PORT}:5012 -v "$DATA_DIR":/data "$DOCKER_IMAGE" osrm-routed --algorithm mld -p 5012 /data/"$NAME.osrm"

cleanup() {
  echo "Stopping container $CONTAINER_NAME"
  $RUNTIME rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
}
trap cleanup INT EXIT

echo "Tailing logs for $CONTAINER_NAME (Ctrl-C to stop)"
$RUNTIME logs -f "$CONTAINER_NAME"
