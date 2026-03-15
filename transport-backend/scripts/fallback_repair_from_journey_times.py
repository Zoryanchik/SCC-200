#!/usr/bin/env python3
"""Fallback repair helper: derive canonical route stop order from
representative bus_journey_times when TXC is not available.

Produces a dry-run SQL preview that (a) creates a backup table and
(b) replaces rows in `bus_route_stops` for the specified route(s).

Usage:
  python3 fallback_repair_from_journey_times.py --route <route_id> [--apply]
  python3 fallback_repair_from_journey_times.py               # reads collision_report.json

This script is conservative by default (dry-run). Use --apply to
execute the backup+replace transaction (requires confirmation).
"""
import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from main import BUS_DB_PATH
from route_id_utils import resolve_prefixed_route_id
import psycopg


def load_routes_from_report():
    p = PROJECT_ROOT / 'cache' / 'collision_report.json'
    if not p.exists():
        return []
    with open(p, 'r') as f:
        doc = json.load(f)
    items = doc.get('results', {}).get('route_stop_order_gaps', {}).get('sample', [])
    return [r[0] for r in items]


def representative_journey(cur, route_id: str):
    """Pick the representative journey_id for a route using largest journey (most stops).

    Returns journey_id or None.
    """
    cur.execute(
        """
        SELECT jr.journey_id, COUNT(jt.*) AS cnt, MIN(jt.arrival_time) AS first_at
        FROM bus_journey_routes jr
        JOIN bus_journey_times jt ON jr.journey_id = jt.journey_id
        WHERE jr.route_id = %s
        GROUP BY jr.journey_id
        ORDER BY cnt DESC, first_at ASC
        LIMIT 1
        """,
        (route_id,)
    )
    row = cur.fetchone()
    return row[0] if row else None


def build_stop_rows(cur, journey_id):
    cur.execute(
        """
        SELECT jt.atco_code, coalesce(sc.name, jt.atco_code) AS name, sc.lat, sc.lon
        FROM bus_journey_times jt
        LEFT JOIN stop_coords sc ON sc.atco_code = jt.atco_code
        WHERE jt.journey_id = %s
        ORDER BY jt.arrival_time
        """,
        (journey_id,)
    )
    return cur.fetchall()


def sql_preview_for_route(route_id, rows):
    lines = []
    backup_table = f"bus_route_stops_backup_{route_id.replace(':','_').replace('/','_')}"
    lines.append(f"-- DRY-RUN SQL for route: {route_id}")
    lines.append(f"-- Backup table (created if not exists): {backup_table}")
    lines.append(f"CREATE TABLE IF NOT EXISTS {backup_table} AS TABLE bus_route_stops WITH NO DATA;")
    lines.append(f"-- Copy existing rows to backup")
    lines.append(f"INSERT INTO {backup_table} SELECT * FROM bus_route_stops WHERE route_id = '{route_id}';")
    lines.append(f"-- Delete existing canonical rows for route")
    lines.append(f"DELETE FROM bus_route_stops WHERE route_id = '{route_id}';")
    if not rows:
        lines.append(f"-- NOTE: no stops found for route {route_id}; nothing to insert")
        return "\n".join(lines)

    # Build multi-row INSERT (escape single quotes)
    vals = []
    for idx, (atco, name, lat, lon) in enumerate(rows):
        name_safe = (name or atco).replace("'", "''")
        lat_sql = 'NULL' if lat is None else f"{float(lat)}"
        lon_sql = 'NULL' if lon is None else f"{float(lon)}"
        vals.append(f"('{route_id}', {idx}, '{atco.replace("'","''")}', '{name_safe}', {lat_sql}, {lon_sql})")

    lines.append("-- Insert canonical route stops (route_id, stop_order, atco_code, name, lat, lon)")
    lines.append("INSERT INTO bus_route_stops (route_id, stop_order, atco_code, name, lat, lon) VALUES\n" + ",\n".join(vals) + ";")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--route', action='append', help='Route id to repair (can be repeated)')
    ap.add_argument('--apply', action='store_true', help='Actually apply the backup+replace transaction')
    args = ap.parse_args()

    routes = args.route or load_routes_from_report()
    if not routes:
        print('No routes specified and no candidates found in cache/collision_report.json')
        sys.exit(2)

    print(f"Connecting to DB using DSN from main.BUS_DB_PATH: {BUS_DB_PATH}")
    conn = psycopg.connect(BUS_DB_PATH)
    cur = conn.cursor()

    for route in routes:
        print('\n' + '='*80)
        print(f"Processing route: {route}")
        # Resolve stored DB-prefixed id (if present) so subsequent queries
        # target the correct namespaced rows.
        db_route = resolve_prefixed_route_id(conn, route)
        if db_route != route:
            print(f"Resolved {route} -> {db_route} for DB lookups")
        jid = representative_journey(cur, route)
        if not jid:
            print(f"WARNING: No representative journey found for route {route}; skipping")
            continue
        print(f"Representative journey_id: {jid}")
        rows = build_stop_rows(cur, jid)
        print(f"Found {len(rows)} stops from journey {jid}")
        preview = sql_preview_for_route(db_route, rows)
        print(preview)

        if args.apply:
            confirm = input(f"Apply repair for {route}? Type YES to proceed: ")
            if confirm.strip() == 'YES':
                # Build and execute transaction: backup + delete + insert
                backup_table = f"bus_route_stops_backup_{db_route.replace(':','_').replace('/','_')}"
                try:
                    with conn.transaction():
                        cur.execute(f"CREATE TABLE IF NOT EXISTS {backup_table} AS TABLE bus_route_stops WITH NO DATA;")
                        cur.execute("INSERT INTO %s SELECT * FROM bus_route_stops WHERE route_id = %%s;" % backup_table, (db_route,))
                        cur.execute("DELETE FROM bus_route_stops WHERE route_id = %s", (db_route,))
                        for idx, (atco, name, lat, lon) in enumerate(rows):
                            cur.execute(
                                "INSERT INTO bus_route_stops (route_id, stop_order, atco_code, name, lat, lon) VALUES (%s, %s, %s, %s, %s, %s)",
                                (db_route, idx, atco, name or atco, lat, lon)
                            )
                    print(f"Applied repair for {route}; backup in {backup_table}")
                except Exception as e:
                    print(f"ERROR applying repair for {route}: {e}")
            else:
                print("Skipped apply")

    cur.close()
    conn.close()


if __name__ == '__main__':
    main()
