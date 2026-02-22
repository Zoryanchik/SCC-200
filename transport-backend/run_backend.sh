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

echo "Starting backend container (build if needed)..."
"$HERE/start_container.sh"

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
