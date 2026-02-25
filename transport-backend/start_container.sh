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

# Detect container runtime: prefer podman if connected, fall back to docker
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
    echo "Error: neither a working Podman nor Docker found. On macOS, Podman often requires 'podman machine init' and 'podman machine start'." >&2
    exit 2
  fi
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

# Allow override to run container with host network (useful on Linux when
# you want the container to access host services directly). Set USE_HOST_NETWORK=1
# in the environment to enable.
RUN_FLAGS="--name $CONTAINER"

# Ensure a user network exists so the backend can talk to an OSRM container by name
# We use a predictable network name so helpers can attach containers to it.
NETWORK_NAME="scc200-net"
$RUNTIME network create "$NETWORK_NAME" >/dev/null 2>&1 || true

# If an OSRM container exists, try to attach it to the network. Prefer the
# OSRM container's default listening port (5000). Allow the host environment
# to override `OSRM_URL` if present (useful when running OSRM on a host port).
if $RUNTIME ps -a --format '{{.Names}}' 2>/dev/null | grep -qx "scc200-osrm"; then
  echo "Found existing OSRM container 'scc200-osrm' — ensuring it's on network $NETWORK_NAME"
  # try to connect it to the network (no-op if already connected)
  $RUNTIME network connect "$NETWORK_NAME" scc200-osrm >/dev/null 2>&1 || true
  # Default to the container's default port (5012) unless the host overrides OSRM_URL.
  OSRM_URL="${OSRM_URL:-http://scc200-osrm:5012}"
fi

# Forward OSRM_URL into the container if set in the environment so the backend
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
  RUN_FLAGS="$RUN_FLAGS -p 5050:5050 --network $NETWORK_NAME"
fi

echo "Starting container $CONTAINER (host:5050 -> container:5050) with cache mounted to $CACHE_DIR"
$RUNTIME run -d $RUN_FLAGS $ENV_FLAGS -v "$CACHE_DIR":/app/cache${MOUNT_OPTS} "$IMAGE"

echo "Container started (id: $($RUNTIME ps -l --format '{{.ID}}' 2>/dev/null))."
