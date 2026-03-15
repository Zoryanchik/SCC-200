#!/usr/bin/env python3
"""
Normalize legacy route ids to canonical RouteSection ids using route_id_aliases.

Supports --dry-run to preview changes, --apply to perform them, and --backup to save affected rows.

This script is conservative: it creates per-table backup tables (selected rows) before applying
changes and handles primary-key conflicts by merging/inserting canonical rows then deleting legacy rows.

Usage:
  python3 normalize_route_ids.py --dry-run
  python3 normalize_route_ids.py --apply --backup

"""
import argparse
import time
from route_id_utils import resolve_prefixed_route_id
import psycopg

def connect(dsn=None):
    dsn = dsn or "postgresql://pguser:pgpass@127.0.0.1:5011/transport"
    return psycopg.connect(dsn)


def load_aliases(conn):
    cur = conn.cursor()
    cur.execute("SELECT legacy_id, canonical_id FROM route_id_aliases")
    rows = cur.fetchall()
    return [(r[0], r[1]) for r in rows]


def preview(conn, aliases, sample_limit=5):
    cur = conn.cursor()
    print(f"Found {len(aliases)} alias mappings")
    legacy_list = [a for a,_ in aliases]
    # Resolve any DB-prefixed stored candidates so counts/searches
    # operate on the actual stored ids (e.g. '<file>::<legacy_id>').
    try:
        db_legacy_list = [resolve_prefixed_route_id(conn, l) for l in legacy_list]
    except Exception:
        db_legacy_list = legacy_list
    # counts per table
    cur.execute("SELECT count(*) FROM bus_route_stops WHERE route_id = ANY(%s)", (db_legacy_list,))
    rs_legacy = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM bus_route_stops WHERE route_id = ANY(SELECT canonical_id FROM route_id_aliases)")
    rs_canonical = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM bus_journey_routes WHERE route_id = ANY(%s)", (db_legacy_list,))
    jr_legacy = cur.fetchone()[0]
    # Note: bus_journeys is unstructured; we'll give a rough sample per alias below
    print("Summary counts (rough):")
    print(f"  bus_route_stops rows with legacy ids: {rs_legacy}")
    print(f"  bus_route_stops rows matching canonical ids: {rs_canonical}")
    print(f"  bus_journey_routes rows with legacy ids: {jr_legacy}")

    print("Sample affected rows per alias:")
    for legacy, canonical in aliases[:sample_limit]:
        print(f"- {legacy} -> {canonical}")
        db_legacy = resolve_prefixed_route_id(conn, legacy)
        cur.execute("SELECT atco_code, stop_order FROM bus_route_stops WHERE route_id = %s LIMIT 5", (db_legacy,))
        rows = cur.fetchall()
        if rows:
            for r in rows:
                print(f"    stop: {r[0]}")
        else:
            print("    (no bus_route_stops rows)")
        cur.execute("SELECT journey_id FROM bus_journey_routes WHERE route_id = %s LIMIT 5", (db_legacy,))
        rows = cur.fetchall()
        if rows:
            for r in rows:
                print(f"    journey_route: {r[0]}")
        else:
            print("    (no bus_journey_routes rows)")
        cur.execute("SELECT id FROM bus_journeys WHERE journey::text LIKE %s LIMIT 3", (f"%{legacy}%",))
        rows = cur.fetchall()
        if rows:
            for r in rows:
                print(f"    logged_journey: {r[0]}")
        else:
            print("    (no bus_journeys matches)")


def apply_changes(conn, aliases, backup=True):
    ts = time.strftime('%Y%m%d_%H%M%S')
    legacy_ids = [a for a,_ in aliases]
    cur = conn.cursor()

    if backup:
        print("Creating backup tables for affected rows...")
        # Table names can't be parameterized, but legacy_ids can be passed as params
        cur.execute(f"CREATE TABLE IF NOT EXISTS bus_route_stops_bak_{ts} AS SELECT * FROM bus_route_stops WHERE route_id = ANY(%s)", (legacy_ids,))
        cur.execute(f"CREATE TABLE IF NOT EXISTS bus_journey_routes_bak_{ts} AS SELECT * FROM bus_journey_routes WHERE route_id = ANY(%s)", (legacy_ids,))
        patterns = [f"%{l}%" for l in legacy_ids]
        cur.execute(f"CREATE TABLE IF NOT EXISTS bus_journeys_bak_{ts} AS SELECT * FROM bus_journeys WHERE journey::text LIKE ANY(%s)", (patterns,))
        print("Backups created (selected rows).")

    print("Applying normalization in a transaction...")
    try:
        with conn.transaction():
            # 1) bus_route_stops: insert missing canonical rows from legacy rows (avoid PK conflicts), then delete legacy rows
            print("  - Merging bus_route_stops rows by inserting into canonical ids then deleting legacy rows")
            # Insert rows for canonical ids only where an identical (route_id, atco_code) doesn't already exist
            cur.execute(
                "INSERT INTO bus_route_stops (route_id, atco_code, stop_order, revision) "
                "SELECT s.canonical_id, s.atco_code, s.stop_order, s.revision FROM ("
                "  SELECT a.canonical_id AS canonical_id, l.atco_code AS atco_code, MIN(l.stop_order) AS stop_order, MIN(COALESCE(l.revision,0)) AS revision "
                "  FROM bus_route_stops l JOIN route_id_aliases a ON l.route_id = a.legacy_id "
                "  GROUP BY a.canonical_id, l.atco_code" 
                ") s LEFT JOIN bus_route_stops existing ON existing.route_id = s.canonical_id AND existing.atco_code = s.atco_code "
                "WHERE existing.route_id IS NULL"
            )
            # Now delete the legacy rows
            cur.execute(
                "DELETE FROM bus_route_stops l USING route_id_aliases a WHERE l.route_id = a.legacy_id"
            )

            # 2) bus_journey_routes: insert canonical rows (if missing) then delete legacy rows
            print("  - Inserting missing canonical rows into bus_journey_routes and deleting legacy rows")
            cur.execute(
                "INSERT INTO bus_journey_routes (journey_id, route_id, line_name, destination_display, revision) "
                "SELECT j.journey_id, a.canonical_id, j.line_name, j.destination_display, j.revision "
                "FROM bus_journey_routes j JOIN route_id_aliases a ON j.route_id = a.legacy_id "
                "ON CONFLICT (journey_id, route_id) DO NOTHING"
            )
            cur.execute(
                "DELETE FROM bus_journey_routes j USING route_id_aliases a WHERE j.route_id = a.legacy_id"
            )

            # 3) bus_journeys: update JSON blobs by replacing legacy ids with canonical ids
            print("  - Updating bus_journeys JSON payloads (string-replace per alias)")
            for legacy, canonical in aliases:
                cur.execute(
                    "UPDATE bus_journeys SET journey = REPLACE(journey::text, %s, %s)::jsonb WHERE journey::text LIKE %s",
                    (legacy, canonical, f"%{legacy}%"),
                )

        print("Normalization applied successfully.")
    except Exception as e:
        print("ERROR during apply:", e)
        raise


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--apply', action='store_true', help='Apply normalization')
    p.add_argument('--backup', action='store_true', help='Create backups of affected rows')
    p.add_argument('--dry-run', action='store_true', help='Preview changes')
    p.add_argument('--db', default=None, help='Postgres DSN')
    args = p.parse_args()

    conn = connect(args.db)
    aliases = load_aliases(conn)
    if not aliases:
        print('No alias mappings found in route_id_aliases. Nothing to do.')
        return

    if args.dry_run or not args.apply:
        preview(conn, aliases)

    if args.apply:
        apply_changes(conn, aliases, backup=args.backup)

if __name__ == '__main__':
    main()
