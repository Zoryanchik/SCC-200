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

# Allow override to run container with host network (useful on Linux when
# you want the container to access host services directly). Set USE_HOST_NETWORK=1
# in the environment to enable.
RUN_OPTS=(--name "$CONTAINER")
if [ "${USE_HOST_NETWORK:-0}" = "1" ]; then
  echo "Using host networking for container (USE_HOST_NETWORK=1)"
  RUN_OPTS+=(--network host)
  # When using host networking we don't publish ports
else
  RUN_OPTS+=(-p 5050:5050)
fi

echo "Starting container $CONTAINER (host:5050 -> container:5050) with cache mounted to $CACHE_DIR"
$RUNTIME run -d "${RUN_OPTS[@]}" -v "$CACHE_DIR":/app/cache${MOUNT_OPTS} "$IMAGE"

echo "Container started (id: $($RUNTIME ps -l --format '{{.ID}}' 2>/dev/null))."
