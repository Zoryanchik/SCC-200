#!/usr/bin/env bash
set -euo pipefail

# Build and start the transport-backend container. Intended to be called
# by run_backend.sh or used standalone.

HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE"


IMAGE=transport-backend:local
CONTAINER=transport-backend-local
CACHE_DIR="$HERE/cache"

# Network to attach containers to so they can resolve each other by name
NETWORK_NAME="${NETWORK_NAME:-scc200-net}"

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

# Ensure the user network exists so containers can talk by name
if [ "${USE_HOST_NETWORK:-0}" != "1" ]; then
  if ! $RUNTIME network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
    echo "Creating network: $NETWORK_NAME"
    $RUNTIME network create "$NETWORK_NAME" || true
  else
    echo "Using existing network: $NETWORK_NAME"
  fi
fi

# Allow override to run container with host network (useful on Linux when
# you want the container to access host services directly). Set USE_HOST_NETWORK=1
# in the environment to enable.
RUN_FLAGS="--name $CONTAINER"

# Forward OSRM_URL into the container if set on the host so the backend
# inside the container can probe the correct OSRM endpoint. Also expose
# CACHE_DIR inside the container so the app can pick up the mounted
# cache (we mount the host cache to /app/cache inside the container).
ENV_FLAGS=""
# If the host didn't set OSRM_URL, default to the OSRM container name on
# the shared network so the backend inside the container can reach it by
# name. Users can still override by setting OSRM_URL in their environment.
if [ -z "${OSRM_URL:-}" ] && [ "${USE_HOST_NETWORK:-0}" != "1" ]; then
  OSRM_URL="http://scc200-osrm:5012"
  echo "No OSRM_URL provided; defaulting to $OSRM_URL (container name on network: $NETWORK_NAME)"
fi
if [ -n "${OSRM_URL:-}" ]; then
  echo "Forwarding OSRM_URL into container: $OSRM_URL"
  ENV_FLAGS="$ENV_FLAGS -e OSRM_URL=$OSRM_URL"
fi
# Expose the in-container cache path so the app can honour it via env.
# This helps `api.py` / `main.py` find the mounted cache when running
# inside the container (it will default to /app/cache).
ENV_FLAGS="$ENV_FLAGS -e CACHE_DIR=/app/cache"
# Ensure BACKEND_PORT is set inside the container so uvicorn uses the
# expected port. Allow users to override BACKEND_PORT in their environment.
BACKEND_PORT="${BACKEND_PORT:-5050}"
echo "Forwarding BACKEND_PORT into container: $BACKEND_PORT"
ENV_FLAGS="$ENV_FLAGS -e BACKEND_PORT=$BACKEND_PORT"
if [ "${USE_HOST_NETWORK:-0}" = "1" ]; then
  echo "Using host networking for container (USE_HOST_NETWORK=1)"
  RUN_FLAGS="$RUN_FLAGS --network host"
  # When using host networking we don't publish ports
else
  RUN_FLAGS="$RUN_FLAGS -p 5050:5050 --network $NETWORK_NAME"
fi

# Helper: probe OSRM via a short-lived curl container attached to the network.
# Returns 0 if OSRM replies (any HTTP status code) and non-zero if connection fails.
probe_osrm() {
  if [ -z "${OSRM_URL:-}" ]; then
    return 1
  fi
  # Use the container runtime to run a curl container on the same network.
  # We capture the numeric HTTP status code; if empty, treat as failure.
  HTTP_CODE=$($RUNTIME run --rm --network "$NETWORK_NAME" docker.io/curlimages/curl:8.7.1 -sS -m 3 -o /dev/null -w "%{http_code}" "${OSRM_URL}/" 2>/dev/null || true)
  if [ -n "$HTTP_CODE" ]; then
    return 0
  else
    return 1
  fi
}

# Optionally wait for OSRM to be reachable before starting the backend container.
# Set WAIT_FOR_OSRM=0 to disable. Default: enabled.
if [ "${WAIT_FOR_OSRM:-1}" = "1" ] && [ "${USE_HOST_NETWORK:-0}" != "1" ]; then
  echo "Checking OSRM reachability at: ${OSRM_URL:-<not set>}"
  tries=0
  max_tries=10
  sleep_secs=1
  until probe_osrm; do
    tries=$((tries+1))
    if [ "$tries" -ge "$max_tries" ]; then
      echo "Warning: OSRM not reachable at ${OSRM_URL:-<not set>} after $((max_tries*sleep_secs))s; starting backend anyway."
      break
    fi
    echo "OSRM not ready yet, retrying in ${sleep_secs}s... ($tries/$max_tries)"
    sleep "$sleep_secs"
  done
  # If OSRM is reachable from the host tooling, export a marker into the
  # container so the in-process probe can be skipped (some environments
  # experience unreliable in-container probes). The Walking class will
  # honour OSRM_AVAILABLE=1 to treat OSRM as available immediately.
  if probe_osrm; then
    ENV_FLAGS="$ENV_FLAGS -e OSRM_AVAILABLE=1"
  fi
fi

echo "Starting container $CONTAINER (host:5050 -> container:5050) with cache mounted to $CACHE_DIR"
$RUNTIME run -d $RUN_FLAGS $ENV_FLAGS -v "$CACHE_DIR":/app/cache${MOUNT_OPTS} "$IMAGE"

echo "Container started (id: $($RUNTIME ps -l --format '{{.ID}}' 2>/dev/null))."
