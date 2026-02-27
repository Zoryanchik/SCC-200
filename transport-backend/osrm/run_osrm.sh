#!/usr/bin/env bash
set -euo pipefail

# Simple helper to download a PBF and run OSRM (MLD) using the official Docker image
# Defaults: downloads the provided URL and runs osrm on host port 5012

PBF_URL_DEFAULT="https://download.geofabrik.de/europe/united-kingdom/england/lancashire-latest.osm.pbf"
# Default data dir: directory next to this script (transport-backend/osrm/data)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR_DEFAULT="$SCRIPT_DIR/data"
PORT_DEFAULT=5012

# Support an optional -d flag to run OSRM detached (background) and
# put the container on a shared network so the backend container can reach
# it by name. Also allow overriding NETWORK_NAME via env.
DETACH=0
if [ "${1:-}" = "-d" ]; then
  DETACH=1
  shift
fi

NETWORK_NAME="${NETWORK_NAME:-scc200-net}"

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

  # Profile to extract (car, foot, bicycle). Default to 'foot' so the
  # running OSRM supports walking endpoints used by the backend. If you
  # prefer driving, set PROFILE=car in your environment or on the command line.
  PROFILE="${PROFILE:-foot}"

  # Profile-specific osrm base (e.g. lancashire-latest.foot.osrm)
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

echo "Checking for image $DOCKER_IMAGE..."
# Ensure we have a fresh image when using podman. For Docker keep existing behaviour.
if $RUNTIME image inspect "$DOCKER_IMAGE" >/dev/null 2>&1; then
  if [ "$RUNTIME" = "podman" ]; then
    echo "Image found locally and runtime is podman — removing and pulling latest $DOCKER_IMAGE..."
    $RUNTIME rmi -f "$DOCKER_IMAGE" >/dev/null 2>&1 || true
    $RUNTIME pull "$DOCKER_IMAGE"
  else
    echo "Image found locally: $DOCKER_IMAGE"
  fi
else
  echo "Image not found locally — pulling $DOCKER_IMAGE..."
  $RUNTIME pull "$DOCKER_IMAGE"
fi

echo "Step 1: osrm-extract (profile=$PROFILE) -> $NAME.osrm"
# If profile-specific osrm file already exists, skip; otherwise run extract
if [ ! -f "$DATA_DIR/$NAME.osrm" ]; then
  $RUNTIME run --rm -t -v "$DATA_DIR":/data "$DOCKER_IMAGE" osrm-extract -p /opt/${PROFILE}.lua /data/"$BASE_NAME"
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

# Ensure the network exists (when not using host networking)
if ! $RUNTIME network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
  echo "Creating network: $NETWORK_NAME"
  $RUNTIME network create "$NETWORK_NAME" || true
else
  echo "Using existing network: $NETWORK_NAME"
fi

# Choose run flags depending on detach mode. Name the container so other
# containers can reach it as 'scc200-osrm'. When detached, keep the
# container persistent (don't use --rm) so it remains on the network.
CONTAINER_NAME="scc200-osrm"
MOUNT_OPTS=""
if [ "$RUNTIME" = "podman" ]; then
  if command -v selinuxenabled >/dev/null 2>&1 && selinuxenabled; then
    MOUNT_OPTS=":Z"
  fi
fi

if [ "$DETACH" -eq 1 ]; then
  echo "Starting detached OSRM container named $CONTAINER_NAME on network $NETWORK_NAME (host:$PORT -> container:5012)"
  # remove any existing container with same name
  if $RUNTIME ps -a --format '{{.Names}}' 2>/dev/null | grep -qx "$CONTAINER_NAME"; then
    echo "Stopping and removing existing container $CONTAINER_NAME..."
    $RUNTIME stop "$CONTAINER_NAME" || true
    $RUNTIME rm "$CONTAINER_NAME" || true
  fi
  $RUNTIME run -d --name "$CONTAINER_NAME" --network "$NETWORK_NAME" -p ${PORT}:5012 -v "$DATA_DIR":/data${MOUNT_OPTS} "$DOCKER_IMAGE" osrm-routed --algorithm mld -p 5012 /data/"$NAME.osrm"
  echo "Detached OSRM container started."
else
  echo "Starting interactive OSRM (will attach to network $NETWORK_NAME). Press Ctrl-C to stop."
  $RUNTIME run --rm --name "$CONTAINER_NAME" --network "$NETWORK_NAME" -p ${PORT}:5012 -v "$DATA_DIR":/data${MOUNT_OPTS} "$DOCKER_IMAGE" osrm-routed --algorithm mld -p 5012 /data/"$NAME.osrm"
fi
