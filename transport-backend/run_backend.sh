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

USE_SHELL=1
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --shell|-s)
      USE_SHELL=1
      shift
      ;;
    --host-network)
      # Export for start_container.sh to pick up
      export USE_HOST_NETWORK=1
      shift
      ;;
    *)
      echo "Unknown arg: $1" >&2
      exit 2
      ;;
  esac
done

echo "Starting backend container (build if needed)..."

# Detect container runtime for portability (prefer podman if connected, fall back to docker)
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

# Delegate container build/start to the helper which handles SELinux labels
"$HERE/start_container.sh"

echo "Waiting for /health to respond (timeout ~90s)..."
for i in {1..30}; do
  if curl -sS http://localhost:5050/health >/dev/null 2>&1; then
    echo "\nBackend is ready at http://localhost:5050/health"
    if [ "$USE_SHELL" -eq 1 ]; then
      echo "Opening an interactive shell inside $CONTAINER (press Ctrl+D to exit)..."
      if [ "$RUNTIME" = "podman" ]; then
        $RUNTIME exec -it "$CONTAINER" /bin/bash || $RUNTIME exec -it "$CONTAINER" /bin/sh
      else
        $RUNTIME exec -it "$CONTAINER" /bin/bash || $RUNTIME exec -it "$CONTAINER" /bin/sh
      fi
    fi
    exit 0
  fi
  printf '.'
  sleep 3
done

echo "\nTimed out waiting for backend to become ready. Tail the logs with: $RUNTIME logs -f $CONTAINER"
exit 1
