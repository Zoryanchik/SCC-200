#!/usr/bin/env bash
set -euo pipefail

# Lightweight helper to build and run the transport-backend container
# Usage: ./run_backend.sh
# Requires: podman (or docker if you adapt the commands)

HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE"

IMAGE=transport-backend:local
CONTAINER=transport-backend-local
CACHE_DIR="$HERE/cache"

mkdir -p "$CACHE_DIR"

echo "Building container image $IMAGE (this may take a minute)..."
podman build -t "$IMAGE" .

if podman ps -a --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  echo "Stopping and removing existing container $CONTAINER..."
  podman stop "$CONTAINER" || true
  podman rm "$CONTAINER" || true
fi

echo "Starting container $CONTAINER (host:5050 -> container:5050) with cache mounted to $CACHE_DIR"
podman run -d --name "$CONTAINER" -p 5050:5050 -v "$CACHE_DIR":/app/cache "$IMAGE"

echo "Waiting for /health to respond (timeout ~90s)..."
for i in {1..30}; do
  if curl -sS http://localhost:5050/health >/dev/null 2>&1; then
    echo "\nBackend is ready at http://localhost:5050/health"
    exit 0
  fi
  printf '.'
  sleep 3
done

echo "\nTimed out waiting for backend to become ready. Tail the logs with: podman logs -f $CONTAINER"
exit 1
