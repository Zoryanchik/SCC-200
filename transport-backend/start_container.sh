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

echo "Checking for image $IMAGE..."
if ! podman image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "Image not found locally — building $IMAGE (this may take a minute)..."
  podman build -t "$IMAGE" .
else
  echo "Image found locally: $IMAGE"
fi

if podman ps -a --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  echo "Stopping and removing existing container $CONTAINER..."
  podman stop "$CONTAINER" || true
  podman rm "$CONTAINER" || true
fi

echo "Starting container $CONTAINER (host:5050 -> container:5050) with cache mounted to $CACHE_DIR"
podman run -d --name "$CONTAINER" -p 5050:5050 -v "$CACHE_DIR":/app/cache "$IMAGE"

echo "Container started (id: $(podman ps -l --format '{{.ID}}'))."
#!/usr/bin/env bash
set -euo pipefail

# Start the transport-backend container (build image if missing).
# This script only ensures the container is running and returns quickly.
# Usage: ./start_container.sh

HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE"

IMAGE=transport-backend:local
CONTAINER=transport-backend-local
CACHE_DIR="$HERE/cache"

mkdir -p "$CACHE_DIR"

echo "Checking for image $IMAGE..."
if ! podman image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "Image not found locally — building $IMAGE (this may take a minute)..."
  podman build -t "$IMAGE" .
else
  echo "Image found locally: $IMAGE"
fi

if podman ps -a --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  echo "Stopping and removing existing container $CONTAINER..."
  podman stop "$CONTAINER" || true
  podman rm "$CONTAINER" || true
fi

echo "Starting container $CONTAINER (host:5050 -> container:5050) with cache mounted to $CACHE_DIR"
podman run -d --name "$CONTAINER" -p 5050:5050 -v "$CACHE_DIR":/app/cache "$IMAGE"

echo "Container started (id: $(podman ps -l --format '{{.ID}}'))."
