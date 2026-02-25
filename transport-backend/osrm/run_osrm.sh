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

# Prefer podman when available and connected; fall back to docker if not.
RUNTIME=""
if command -v podman >/dev/null 2>&1; then
  if podman info >/dev/null 2>&1; then
    RUNTIME=podman
  else
    echo "Podman found but not connected to a VM/service. Will attempt to use Docker if available."
  fi
fi
if [ -z "$RUNTIME" ]; then
  if command -v docker >/dev/null 2>&1; then
    RUNTIME=docker
  else
    echo "Error: neither Podman nor Docker are available. Install Podman (preferred) or Docker and try again." >&2
    exit 1
  fi
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

# Run router (keep container interactive so logs show). Map host port $PORT to container 5321.
${RUNTIME} run --rm -p ${PORT}:5012 -v "$DATA_DIR":/data "$DOCKER_IMAGE" osrm-routed --algorithm mld -p 5012 /data/"$NAME.osrm"
