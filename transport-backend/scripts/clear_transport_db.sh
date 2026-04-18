#!/usr/bin/env bash
set -euo pipefail

# clear_transport_db.sh
# Safely drop and recreate the transport database (destructive).
# Usage:
#   ./clear_transport_db.sh           # interactive prompt
#   ./clear_transport_db.sh --yes     # run non-interactive (destructive)
#   ./clear_transport_db.sh --backup  # create pg_dump backup first
#   ./clear_transport_db.sh --init    # after recreate, initialise schema via BusLoader.create_schema()

PGHOST=${PGHOST:-127.0.0.1}
PGPORT=${PGPORT:-5011}
PGUSER=${PGUSER:-pguser}
PGPASS=${PGPASS:-pgpass}
PGDB=${PGDB:-transport}
CONTAINER_NAME=${CONTAINER_NAME:-transport-postgres-edillocnon}
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKUP_DIR="$BACKEND_DIR/backups"

DO_BACKUP=0
AUTO_YES=0
INIT_SCHEMA=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --backup) DO_BACKUP=1; shift ;;
    --yes|-y) AUTO_YES=1; shift ;;
    --init) INIT_SCHEMA=1; shift ;;
    --help|-h) echo "Usage: $0 [--backup] [--init] [--yes]"; exit 0 ;;
    *) echo "Unknown arg: $1"; exit 2 ;;
  esac
done

echo "This script will DROP and re-CREATE the database: $PGDB on $PGHOST:$PGPORT"
if [ "$AUTO_YES" -ne 1 ]; then
  read -r -p "Type YES to proceed (destructive): " confirm
  if [ "$confirm" != "YES" ]; then
    echo "Aborted by user." >&2
    exit 3
  fi
fi

mkdir -p "$BACKUP_DIR"

timestamp() { date +%Y%m%d_%H%M%S; }

backup_file="$BACKUP_DIR/${PGDB}_backup_$(timestamp).dump"

if [ "$DO_BACKUP" -eq 1 ]; then
  echo "Attempting to create pg_dump backup to: $backup_file"
  if command -v pg_dump >/dev/null 2>&1; then
    PGPASSWORD="$PGPASS" pg_dump -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -F c -f "$backup_file" "$PGDB"
    echo "Backup written to $backup_file"
  else
    echo "pg_dump not found on PATH; attempting to run pg_dump inside container '$CONTAINER_NAME'"
    if command -v podman >/dev/null 2>&1; then
      podman exec -e PGPASSWORD="$PGPASS" "$CONTAINER_NAME" pg_dump -U "$PGUSER" -F c "$PGDB" > "$backup_file"
      echo "Backup written to $backup_file via podman exec"
    elif command -v docker >/dev/null 2>&1; then
      docker exec -e PGPASSWORD="$PGPASS" "$CONTAINER_NAME" pg_dump -U "$PGUSER" -F c "$PGDB" > "$backup_file"
      echo "Backup written to $backup_file via docker exec"
    else
      echo "Unable to find pg_dump or container runtime to perform backup. Skipping backup." >&2
    fi
  fi
fi

echo "Terminating active connections to database '$PGDB'"
PGPASSWORD="$PGPASS" psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$PGDB' AND pid<>pg_backend_pid();" || true

echo "Dropping database '$PGDB' if it exists"
PGPASSWORD="$PGPASS" psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d postgres -c "DROP DATABASE IF EXISTS \"$PGDB\";"

echo "Creating database '$PGDB'"
PGPASSWORD="$PGPASS" psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d postgres -c "CREATE DATABASE \"$PGDB\";"

if [ "$INIT_SCHEMA" -eq 1 ]; then
  echo "Initialising schema using transport-backend.BusLoader.create_schema()"
  export BACKEND_DIR
  export PGHOST PGPORT PGUSER PGPASS PGDB
  python3 - <<PY
import sys, os
sys.path.insert(0, os.environ['BACKEND_DIR'])
from bus_loader import BusLoader
DSN = (
    f"postgresql://{os.environ['PGUSER']}:{os.environ['PGPASS']}"
    f"@{os.environ['PGHOST']}:{os.environ['PGPORT']}/{os.environ['PGDB']}"
)
ld=BusLoader(DSN)
ld.create_schema()
print('Schema created')
PY
fi

echo "Database '$PGDB' cleared and recreated. Backup (if requested) is at: $backup_file"

exit 0
