#!/usr/bin/env bash
set -euo pipefail

# Lightweight helper to build and run the transport-backend container
# Usage: ./run_backend.sh
# Requires: podman (or docker if you adapt the commands)

HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE"

IMAGE=transport-backend:local
CONTAINER=transport-backend-edillocnon
CACHE_DIR="$HERE/cache"

USE_SHELL=0
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

# Detect container runtime for portability (prefer podman, fall back to docker)
if command -v podman >/dev/null 2>&1; then
  RUNTIME=podman
elif command -v docker >/dev/null 2>&1; then
  RUNTIME=docker
else
  echo "Error: neither podman nor docker is installed or on PATH." >&2
  exit 2
fi

echo "Using container runtime: $RUNTIME"

# Delegate container build/start to the helper which handles SELinux labels
# Default network for backend to join so it can reach the OSRM container by
# name. This matches the network used by run_osrm.sh (scc200-net-edillocnon) unless
# overridden by the user via NETWORK_NAME.
NETWORK_NAME="${NETWORK_NAME:-scc200-net-edillocnon}"
# If OSRM_URL isn't set in the environment, point it to the named container
# so the backend inside the container can reach OSRM when both are on the
# same user network.
export OSRM_URL="${OSRM_URL:-http://transport-osrm-edillocnon:5012}"
export NETWORK_NAME

"$HERE/start_container.sh"

# Wait for backend health. Default timeout is 10 minutes (600s) to allow
# slower container startups; can be overridden with READY_TIMEOUT_SECS env var.
READY_TIMEOUT_SECS="${READY_TIMEOUT_SECS:-600}"
echo "Waiting for /health to respond (timeout ~${READY_TIMEOUT_SECS}s)..."
# Poll interval (seconds)
INTERVAL=3
# Number of attempts
ATTEMPTS=$(( READY_TIMEOUT_SECS / INTERVAL ))
for i in $(seq 1 $ATTEMPTS); do
  if curl -sS http://localhost:5050/health >/dev/null 2>&1; then
    echo
    echo "Backend is ready at http://localhost:5050"
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

echo
echo "Timed out waiting for backend to become ready. Tail the logs with: $RUNTIME logs -f $CONTAINER"
exit 1
