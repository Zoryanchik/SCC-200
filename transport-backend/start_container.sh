#!/usr/bin/env bash
set -euo pipefail

# Build and start the transport-backend container. Intended to be called
# by run_backend.sh or used standalone.

HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE"


IMAGE=transport-backend:local
CONTAINER=transport-backend-local
CACHE_DIR="$HERE/cache"

mkdir -p "$CACHE_DIR"

# Detect container runtime: prefer podman, fall back to docker
if command -v podman >/dev/null 2>&1; then
  RUNTIME=podman
elif command -v docker >/dev/null 2>&1; then
  RUNTIME=docker
else
  echo "Error: neither podman nor docker is installed or on PATH." >&2
  exit 2
fi

echo "Using container runtime: $RUNTIME"

# Detect SELinux (common on Fedora/RHEL). If SELinux is enabled and we're
# using podman, add :Z to the bind mount so the container can access the
# labelled files. On Docker the label is typically not needed.
MOUNT_OPTS=""
if [ "$RUNTIME" = "podman" ]; then
  if command -v selinuxenabled >/dev/null 2>&1 && selinuxenabled; then
    MOUNT_OPTS=":Z"
    echo "SELinux enabled: adding :Z to volume mounts"
  fi
fi

echo "Checking for image $IMAGE..."
if ! $RUNTIME image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "Image not found locally — building $IMAGE (this may take a minute)..."
  $RUNTIME build -t "$IMAGE" .
else
  echo "Image found locally: $IMAGE"
fi


if $RUNTIME ps -a --format '{{.Names}}' 2>/dev/null | grep -qx "$CONTAINER"; then
  echo "Stopping and removing existing container $CONTAINER..."
  $RUNTIME stop "$CONTAINER" || true
  $RUNTIME rm "$CONTAINER" || true
fi

# Network configuration. By default we place the backend on the same
# user network used by the OSRM container so containers can reach each
# other by name (default: scc200-net). Override by setting NETWORK_NAME
# in the environment before calling this script.
NETWORK_NAME="${NETWORK_NAME:-scc200-net}"

# Allow override to run container with host network (useful on Linux when
# you want the container to access host services directly). Set USE_HOST_NETWORK=1
# in the environment to enable.
RUN_FLAGS="--name $CONTAINER"

# Forward OSRM_URL into the container if set on the host so the backend
# inside the container can probe the correct OSRM endpoint.
ENV_FLAGS=""
if [ -n "${OSRM_URL:-}" ]; then
  echo "Forwarding OSRM_URL into container: $OSRM_URL"
  ENV_FLAGS="-e OSRM_URL=$OSRM_URL"
fi
if [ "${USE_HOST_NETWORK:-0}" = "1" ]; then
  echo "Using host networking for container (USE_HOST_NETWORK=1)"
  RUN_FLAGS="$RUN_FLAGS --network host"
  # When using host networking we don't publish ports
else
  # Ensure the network exists and attach the container to it so other
  # containers (for example scc200-osrm) can be reached by name.
  if ! $RUNTIME network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
    echo "Creating network: $NETWORK_NAME"
    $RUNTIME network create "$NETWORK_NAME" || true
  else
    echo "Using existing network: $NETWORK_NAME"
  fi
  RUN_FLAGS="$RUN_FLAGS --network $NETWORK_NAME -p 5050:5050"
fi

echo "Starting container $CONTAINER (host:5050 -> container:5050) with cache mounted to $CACHE_DIR"
$RUNTIME run -d $RUN_FLAGS $ENV_FLAGS -v "$CACHE_DIR":/app/cache${MOUNT_OPTS} "$IMAGE"

echo "Container started (id: $($RUNTIME ps -l --format '{{.ID}}' 2>/dev/null))."
