#!/usr/bin/env bash
set -euo pipefail

# Run a local Postgres container (docker or podman) and expose it on host port 5011
# Usage: ./run_pgsql.sh
# Environment overrides:
#   ENGINE (docker|podman)  - default: podman
#   CONTAINER_NAME           - default: transport-postgres-local
#   IMAGE                    - default: postgres:15
#   HOST_PORT                - default: 5011
#   PG_USER / PG_PASS / PG_DB - defaults provided below

ENGINE=${ENGINE:-podman}
CONTAINER_NAME=${CONTAINER_NAME:-transport-postgres-local}
IMAGE=${IMAGE:-postgres:15}
HOST_PORT=${HOST_PORT:-5011}
CONTAINER_PORT=${CONTAINER_PORT:-5011}
PG_USER=${PG_USER:-pguser}
PG_PASS=${PG_PASS:-pgpass}
PG_DB=${PG_DB:-transport}
DATA_DIR="$(cd "$(dirname "${0}")" && pwd)/pgdata"

mkdir -p "${DATA_DIR}"

echo "Using ${ENGINE} to start Postgres container '${CONTAINER_NAME}' (host port ${HOST_PORT})"

# Check if container already exists
if ${ENGINE} ps -a --format '{{.Names}}' 2>/dev/null | grep -q "^${CONTAINER_NAME}$"; then
    echo "Container ${CONTAINER_NAME} already exists — starting it"
    ${ENGINE} start "${CONTAINER_NAME}"
else
    echo "Creating and running container ${CONTAINER_NAME}"
    # Choose a volume strategy. On macOS podman, bind-mounting a host
    # directory often prevents the container from chown/chmod'ing files.
    # Use a named volume for podman to avoid permission issues. For docker
    # we keep a host bind by default so data is visible on the host.
    if [ "${ENGINE}" = "podman" ]; then
        VOLUME_ARG="-v transport-postgres-data:/var/lib/postgresql/data"
    else
        VOLUME_ARG="-v \"${DATA_DIR}\":/var/lib/postgresql/data"
    fi
    # Start postgres with an overridden port so the server listens on
    # the container port we expose (default 5011). We pass -c 'port=...' to
    # the postgres entrypoint to override the default 5432.
    ${ENGINE} run -d --name "${CONTAINER_NAME}" -p 127.0.0.1:${HOST_PORT}:${CONTAINER_PORT} \
        -e POSTGRES_USER="${PG_USER}" -e POSTGRES_PASSWORD="${PG_PASS}" -e POSTGRES_DB="${PG_DB}" \
        ${VOLUME_ARG} \
        --restart unless-stopped "${IMAGE}" postgres -c "port=${CONTAINER_PORT}"
fi

echo "Waiting for Postgres to become ready (timeout ~30s)"
for i in $(seq 1 30); do
    if ${ENGINE} exec "${CONTAINER_NAME}" pg_isready -U "${PG_USER}" -p "${CONTAINER_PORT}" >/dev/null 2>&1; then
        echo "Postgres is ready and listening on localhost:${HOST_PORT}"
        exit 0
    fi
    sleep 1
done

echo "Postgres did not become ready within timeout" >&2
exit 1
