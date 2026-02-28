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

# By default, attempt a local in-place build so compiled extensions (.so)
# live next to the source files. This is the expected developer workflow:
# the host will run `python3 setup_*.py build_ext --inplace` before starting
# the container. Set SKIP_LOCAL_BUILD=1 in the environment to skip this step
# (useful on CI or when build tools are intentionally absent).
if [ "${SKIP_LOCAL_BUILD:-0}" != "1" ]; then
  if [ -f setup_raptor.py ]; then
    echo "Running local setup_raptor.py build_ext --inplace"
    python3 setup_raptor.py build_ext --inplace || echo "Local build raptor failed"
  fi
  if [ -f setup_walking.py ]; then
    echo "Running local setup_walking.py build_ext --inplace"
    python3 setup_walking.py build_ext --inplace || echo "Local build walking failed"
  fi
else
  echo "SKIP_LOCAL_BUILD=1 — skipping host in-place build"
fi

# Postgres connection defaults used when running the backend container on a
# user network. These mirror the defaults used by run_pgsql.sh and can be
# overridden via the environment before calling this script.
PG_USER="${PG_USER:-pguser}"
PG_PASS="${PG_PASS:-pgpass}"
PG_DB="${PG_DB:-transport}"
PG_PORT="${PG_PORT:-5011}"
PG_HOST="${PG_HOST:-transport-postgres-local}"

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
if $RUNTIME image inspect "$IMAGE" >/dev/null 2>&1; then
  # If we're using podman prefer to remove and rebuild to avoid stale images
  if [ "$RUNTIME" = "podman" ]; then
    echo "Image found locally and runtime is podman — removing and rebuilding $IMAGE (this may take a minute)..."
    # force remove existing image if present; ignore errors
    $RUNTIME rmi -f "$IMAGE" >/dev/null 2>&1 || true
    $RUNTIME build -t "$IMAGE" .
  else
    echo "Image found locally: $IMAGE"
  fi
else
  echo "Image not found locally — building $IMAGE (this may take a minute)..."
  $RUNTIME build -t "$IMAGE" .
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
  # When running on a user network (default) try to forward DB DSNs so the
  # backend inside the container connects to the Postgres container by name
  # instead of trying to use 127.0.0.1 (which would resolve to the container
  # itself). If the Postgres container is not present on the user network we
  # fall back to the host loopback to preserve existing local workflows.
  #
  # Detect whether the Postgres container is attached to the network. If so
  # use the container hostname; otherwise use 127.0.0.1 so host-local Postgres
  # remains reachable.
  # Prefer detecting the Postgres container by name (works across runtimes).
  if $RUNTIME ps -a --format '{{.Names}}' 2>/dev/null | grep -qx 'transport-postgres-local'; then
    # Try to resolve the Postgres container's IP on the user network. Some
    # container runtimes (or macOS host setups) do not provide DNS name
    # resolution by container name inside containers, so using the container
    # IP is more reliable for connectivity.
    POSTGRES_IP=$($RUNTIME inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' transport-postgres-local 2>/dev/null || true)
    if [ -n "${POSTGRES_IP}" ]; then
      TARGET_HOST="$POSTGRES_IP"
      echo "Found Postgres container IP ${POSTGRES_IP} — using host: $TARGET_HOST"
    else
      TARGET_HOST="$PG_HOST"
      echo "Could not determine Postgres IP; falling back to container name: $TARGET_HOST"
    fi
  else
    TARGET_HOST="127.0.0.1"
    echo "Postgres container not found by name — falling back to host: $TARGET_HOST"
  fi

  BACKEND_PG_DSN="postgresql://${PG_USER}:${PG_PASS}@${TARGET_HOST}:${PG_PORT}/${PG_DB}"
  echo "Forwarding DB DSNs into container pointing at: ${TARGET_HOST}:${PG_PORT}"
  # Preserve any existing ENV_FLAGS (for OSRM_URL) and append DB DSNs
  ENV_FLAGS="$ENV_FLAGS -e BUS_DB_DSN=${BACKEND_PG_DSN} -e TRAIN_DB_DSN=${BACKEND_PG_DSN} -e WALK_DB_DSN=${BACKEND_PG_DSN}"

  RUN_FLAGS="$RUN_FLAGS --network $NETWORK_NAME -p 5050:5050"
fi

echo "Starting container $CONTAINER (host:5050 -> container:5050) with cache mounted to $CACHE_DIR"

# Build a startup script string to run inside the container. Keep it in a
# double-quoted shell variable so we can safely pass it as the argument to
# '/bin/sh -c'. Avoid nesting single quotes inside the variable which makes
# the value hard to pass to the runtime correctly.
START_SCRIPT="if [ -f /app/setup_raptor.py ]; then echo '[startup] running setup_raptor.py --inplace'; python3 /app/setup_raptor.py build_ext --inplace || echo '[startup] setup_raptor failed'; fi; \
if [ -f /app/setup_walking.py ]; then echo '[startup] running setup_walking.py --inplace'; python3 /app/setup_walking.py build_ext --inplace || echo '[startup] setup_walking failed'; fi; \
echo '[startup] launching uvicorn'; exec uvicorn api:app --host 0.0.0.0 --port 5050"

# Run the container and pass the startup script as the command to execute.
# We use '/bin/sh -c "$START_SCRIPT"' so the whole script runs inside the
# container's shell. Quoting is important here to avoid word-splitting issues.
$RUNTIME run -d $RUN_FLAGS $ENV_FLAGS -v "$CACHE_DIR":/app/cache${MOUNT_OPTS} "$IMAGE" /bin/sh -c "$START_SCRIPT"

echo "Container started (id: $($RUNTIME ps -l --format '{{.ID}}' 2>/dev/null))."
