#!/usr/bin/env bash
set -euo pipefail

# Simple helper to download a PBF and run OSRM (MLD) using the official Docker image
# Defaults: downloads the provided URL and runs osrm on host port 5321

PBF_URL_DEFAULT="https://download.geofabrik.de/europe/united-kingdom/england/lancashire-latest.osm.pbf"
DATA_DIR_DEFAULT="./data"
PORT_DEFAULT=5321

PBF_URL="${1:-$PBF_URL_DEFAULT}"
DATA_DIR="${2:-$DATA_DIR_DEFAULT}"
PORT="${3:-$PORT_DEFAULT}"

echo "Using PBF URL: $PBF_URL"
echo "Data directory: $DATA_DIR"
echo "Host port: $PORT -> container port 5321"

if ! command -v docker >/dev/null 2>&1; then
  echo "Error: docker is not installed or not in PATH. Install Docker Desktop and try again." >&2
  exit 1
fi

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

DOCKER_IMAGE="osrm/osrm-backend:latest"

echo "Step 1: osrm-extract (creates $NAME.osrm)"
if [ ! -f "$DATA_DIR/$NAME.osrm" ]; then
  docker run --rm -t -v "$PWD/$DATA_DIR":/data "$DOCKER_IMAGE" osrm-extract -p /opt/car.lua /data/"$BASE_NAME"
else
  echo "  $NAME.osrm already exists, skipping extract."
fi

echo "Step 2: osrm-partition"
if [ ! -f "$DATA_DIR/$NAME.osrm.partition" ]; then
  docker run --rm -t -v "$PWD/$DATA_DIR":/data "$DOCKER_IMAGE" osrm-partition /data/"$NAME.osrm"
else
  echo "  partition files already exist, skipping partition."
fi

echo "Step 3: osrm-customize"
if [ ! -f "$DATA_DIR/$NAME.osrm.mldgrids" ] && [ ! -f "$DATA_DIR/$NAME.osrm.hsgr" ]; then
  # customize will generate .mldgrids/.hsgr/.ramIndex/... depending on algorithm
  docker run --rm -t -v "$PWD/$DATA_DIR":/data "$DOCKER_IMAGE" osrm-customize /data/"$NAME.osrm"
else
  echo "  customize outputs already exist, skipping customize."
fi

echo "Starting osrm-routed on host port $PORT (container port 5321)"
echo "Press Ctrl-C to stop the server."

# Run router (keep container interactive so logs show). Map host port $PORT to container 5321.
docker run --rm -p ${PORT}:5321 -v "$PWD/$DATA_DIR":/data "$DOCKER_IMAGE" osrm-routed --algorithm mld -p 5321 /data/"$NAME.osrm"
