#!/usr/bin/env bash
set -euo pipefail

# Build and start the transport-frontend container (Podman preferred).
# - Frees host port 5075 if occupied
# - Ensures the container joins the same user network as the backend
# - Installs npm dependencies inside the container (cached via a named volume)
# - Runs Vite dev server on 0.0.0.0:5075

HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE"

BASE_IMAGE="${FRONTEND_BASE_IMAGE:-node:20-alpine}"
CONTAINER=transport-frontend-edillocnon
PORT=5075

# Network must match backend default (see transport-backend/run_backend.sh)
NETWORK_NAME="${NETWORK_NAME:-scc200-net-edillocnon}"

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

# On macOS, Podman runs inside a Linux VM (podman machine). If that VM is stopped
# (common after sleep / reboot), Podman commands will fail with a socket connection
# error. Try to auto-start the machine to avoid a manual "podman machine start"
# step.
if [ "$RUNTIME" = "podman" ]; then
  if ! podman info >/dev/null 2>&1; then
    echo "Podman engine not reachable; attempting to start podman machine..."
    if command -v podman >/dev/null 2>&1; then
      # Start the default machine (or the only machine) if present.
      # If there's no machine yet, this will fail and we provide a helpful message.
      podman machine start >/dev/null 2>&1 || true
    fi
    # Give the VM a moment to come up.
    sleep 2
    if ! podman info >/dev/null 2>&1; then
      echo "Error: Podman engine is still not reachable." >&2
      echo "Try: podman machine start" >&2
      echo "Then retry: ./run_frontend.sh" >&2
      exit 125
    fi
  fi
fi

# --- Port cleanup (prefer stopping containers over killing host PIDs) ---
# We want this script to be safe when the listener is a container-published
# port forward (common with Podman/Docker). In that case, killing the host PID
# (often a port-forward helper) can break the container runtime.
stop_containers_on_port() {
  local port="$1"

  # Find containers that publish the host port. We support both:
  # - "0.0.0.0:5075->5173/tcp" (podman)
  # - "*:5075->5173/tcp" (docker)
  local ids
  ids=$($RUNTIME ps --format '{{.ID}} {{.Ports}}' 2>/dev/null | awk -v p=":${port}->" '$0 ~ p {print $1}' | tr '\n' ' ' | sed 's/[[:space:]]*$//')

  if [ -n "$ids" ]; then
    echo "Port $port is published by container(s): $ids"
    for id in $ids; do
      echo "Stopping container $id to free port $port..."
      $RUNTIME stop "$id" >/dev/null 2>&1 || true
      # This script recreates containers each run, so removing avoids stale
      # name collisions and guarantees a clean start.
      $RUNTIME rm "$id" >/dev/null 2>&1 || true
    done
  fi
}

kill_host_listeners() {
  local port="$1"

  if ! command -v lsof >/dev/null 2>&1; then
    echo "Warning: lsof not found; cannot auto-stop host listeners on port $port." >&2
    return 0
  fi

  local pids
  pids=$(lsof -ti tcp:"$port" -sTCP:LISTEN || true)
  if [ -z "$pids" ]; then
    return 0
  fi

  echo "Port $port is in use by host PID(s): $pids"

  # Graceful shutdown first.
  # shellcheck disable=SC2086
  kill -TERM $pids >/dev/null 2>&1 || true
  local waited=0
  while [ $waited -lt 5 ]; do
    sleep 1
    waited=$((waited + 1))
    pids=$(lsof -ti tcp:"$port" -sTCP:LISTEN || true)
    [ -z "$pids" ] && return 0
  done

  echo "Port $port still busy; sending SIGKILL to PID(s): $pids"
  # shellcheck disable=SC2086
  kill -KILL $pids >/dev/null 2>&1 || true
}

# Ensure port is free on host
stop_containers_on_port "$PORT"
kill_host_listeners "$PORT"

# Ensure the network exists
if ! $RUNTIME network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
  echo "Creating network: $NETWORK_NAME"
  $RUNTIME network create "$NETWORK_NAME" || true
else
  echo "Using existing network: $NETWORK_NAME"
fi

# Stop/remove existing container if present
if $RUNTIME ps -a --format '{{.Names}}' 2>/dev/null | grep -qx "$CONTAINER"; then
  echo "Stopping and removing existing container $CONTAINER..."
  $RUNTIME stop "$CONTAINER" || true
  $RUNTIME rm "$CONTAINER" || true
fi

# Named volume for node_modules so installs persist across runs
NODE_MODULES_VOL="transport-frontend-node_modules-edillocnon"
if ! $RUNTIME volume inspect "$NODE_MODULES_VOL" >/dev/null 2>&1; then
  echo "Creating volume: $NODE_MODULES_VOL"
  $RUNTIME volume create "$NODE_MODULES_VOL" >/dev/null
fi

# VITE API base:
# IMPORTANT: This value is consumed by *browser* JavaScript.
# Even if the Vite dev server runs in a container, the browser still runs on the
# host machine, so it cannot resolve container DNS names like
# "transport-backend-edillocnon".
#
# Therefore, default to a host-reachable backend URL.
# If you are running a setup where the browser can resolve the container DNS
# (rare; usually requires a proxy), you can override this env var.
VITE_API_BASE_URL="${VITE_API_BASE_URL:-http://localhost:5050}"
# OSRM base for *direct* client-side snapping (optional).
#
# Default behaviour: leave this UNSET so the frontend uses the backend's
# /osrm/route proxy endpoint (avoids docker-only DNS names and CORS issues).
#
# If you *really* want the browser to call OSRM directly, set e.g.:
#   export VITE_OSRM_BASE=http://127.0.0.1:5012
VITE_OSRM_BASE="${VITE_OSRM_BASE:-}"

# Memory tuning
# - NODE_OPTIONS sets the V8 heap limit (in MB) for the dev server process.
# - CONTAINER_MEMORY caps the container's memory limit. Set to empty to disable.
NODE_MAX_OLD_SPACE_MB="${NODE_MAX_OLD_SPACE_MB:-4096}"
CONTAINER_MEMORY="${CONTAINER_MEMORY:-6g}"

echo "Starting container $CONTAINER (host:$PORT -> container:$PORT)"

echo "Frontend will use: VITE_API_BASE_URL=$VITE_API_BASE_URL"

if [ -n "$VITE_OSRM_BASE" ]; then
  echo "(Optional) Frontend OSRM base (direct): VITE_OSRM_BASE=$VITE_OSRM_BASE"
else
  echo "Frontend OSRM: using backend proxy (VITE_OSRM_BASE is unset)"
fi

# On SELinux systems with podman, :Z might be required. (No-op elsewhere.)
MOUNT_OPTS=""
if [ "$RUNTIME" = "podman" ]; then
  if command -v selinuxenabled >/dev/null 2>&1 && selinuxenabled; then
    MOUNT_OPTS=":Z"
    echo "SELinux enabled: adding :Z to volume mounts"
  fi
fi

# Run dev server. We attempt `npm ci` first (lockfile), then fall back to a tolerant install
# for dev environments that hit peer-dependency conflicts.
START_SCRIPT="set -e; echo '[startup] node version:'; node -v; echo '[startup] installing deps'; if [ -f package-lock.json ]; then npm ci --legacy-peer-deps; else npm install --legacy-peer-deps; fi; echo '[startup] starting vite'; exec npm run dev -- --host 0.0.0.0 --port $PORT"

# Build extra args (docker/podman compatible)
EXTRA_RUN_ARGS=()
if [ -n "$CONTAINER_MEMORY" ]; then
  EXTRA_RUN_ARGS+=(--memory "$CONTAINER_MEMORY")
fi

$RUNTIME run -d \
  --name "$CONTAINER" \
  --network "$NETWORK_NAME" \
  "${EXTRA_RUN_ARGS[@]}" \
  -p "$PORT:$PORT" \
  -e "VITE_API_BASE_URL=$VITE_API_BASE_URL" \
  $( [ -n "$VITE_OSRM_BASE" ] && printf '%s' "-e VITE_OSRM_BASE=$VITE_OSRM_BASE" ) \
  -e "NODE_OPTIONS=--max-old-space-size=$NODE_MAX_OLD_SPACE_MB" \
  -e "CYPRESS_INSTALL_BINARY=0" \
  -v "$HERE":/app${MOUNT_OPTS} \
  -v "$NODE_MODULES_VOL":/app/node_modules \
  -w /app \
  "$BASE_IMAGE" \
  /bin/sh -c "$START_SCRIPT"

# Wait until Vite is actually serving before printing the URL.
READY_TIMEOUT_SECS="${READY_TIMEOUT_SECS:-90}"
echo "Waiting for frontend to become ready (timeout: ${READY_TIMEOUT_SECS}s)..."

start_ts=$(date +%s)
while true; do
  # If the container exited, treat as failure (usually npm ci failed).
  if ! $RUNTIME ps --format '{{.Names}}' 2>/dev/null | grep -qx "$CONTAINER"; then
    echo "Error: frontend container exited before becoming ready." >&2
    echo "--- container logs (tail) ---" >&2
    $RUNTIME logs --tail 200 "$CONTAINER" 2>/dev/null || true
    exit 1
  fi

  if curl -fsS "http://127.0.0.1:$PORT/" >/dev/null 2>&1; then
    break
  fi

  now_ts=$(date +%s)
  if [ $((now_ts - start_ts)) -ge "$READY_TIMEOUT_SECS" ]; then
    echo "Error: frontend did not become ready within ${READY_TIMEOUT_SECS}s." >&2
    echo "--- container logs (tail) ---" >&2
    $RUNTIME logs --tail 200 "$CONTAINER" 2>/dev/null || true
    echo "Stopping container $CONTAINER..." >&2
    $RUNTIME stop "$CONTAINER" >/dev/null 2>&1 || true
    $RUNTIME rm "$CONTAINER" >/dev/null 2>&1 || true
    exit 1
  fi

  sleep 2
done

echo "--------------- Frontend is ready. ---------------"
echo "--------------- URL: http://localhost:$PORT ---------------"
echo "Logs: $RUNTIME logs -f $CONTAINER"
