#!/usr/bin/env python3
"""Repair route stop rows to match TXC canonical JourneyPatternSection order.

This tool creates a backup of existing `bus_route_stops` rows for the
specified `route_id`, deletes them, and reinserts rows derived from the
TXC files found under the downloads directory. It supports a --dry-run
mode which only prints the SQL it would execute and a preview of
existing vs desired rows.

Usage:
  python3 repair_route_stops.py --route PC1016989:47:rt_0001 [--dry-run]

Make sure the dataset TXC files are downloaded under
`transport-backend/transport-backend/tmp/downloads/<dataset_id>/` or
pass `--downloads` to point at the folder containing the TXC files.
"""
import argparse
import os
import time
import psycopg
import csv
from urllib.parse import urlparse

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from bus_loader import BusLoader
from route_id_utils import resolve_prefixed_route_id


def compute_canonical_stops(downloads_dir):
    """Parse all XMLs under downloads_dir and return mapping route_id -> [atco,...]
    Prefers the longest observed stop list per route (same heuristic as loader).
    """
    ld = BusLoader('')
    parsed = {}
    if not os.path.isdir(downloads_dir):
        raise SystemExit(f"Downloads dir not found: {downloads_dir}")
    files = sorted([f for f in os.listdir(downloads_dir) if f.lower().endswith('.xml')])
    for fn in files:
        path = os.path.join(downloads_dir, fn)
        try:
            rs, *_ = ld._parse_file(path)
        except Exception as e:
            print('WARN: parse failed for', fn, e)
            continue
        tmp = {}
        for rid, atc, so in rs:
            tmp.setdefault(rid, []).append((so, atc))
        for rid, lst in tmp.items():
            lst_sorted = [a for _, a in sorted(lst, key=lambda x: x[0])]
            old = parsed.get(rid)
            if old is None or len(lst_sorted) > len(old):
                parsed[rid] = lst_sorted
    return parsed


def safe_table_name(route_id):
    return 'bus_route_stops_backup_' + ''.join(c if c.isalnum() else '_' for c in route_id)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--route', required=True, help='Full route_id to repair (e.g. PC1016989:47:rt_0001)')
    p.add_argument('--downloads', default=os.path.join(os.path.dirname(__file__), '..', 'transport-backend', 'tmp', 'downloads', '20951'), help='Path to extracted TXC files')
    p.add_argument('--dsn', default=os.environ.get('BUS_DB_DSN') or os.environ.get('BUS_DB_PATH'), help='Postgres DSN (env BUS_DB_DSN)')
    p.add_argument('--dry-run', action='store_true', help='Only print planned SQL and diffs')
    args = p.parse_args()

    downloads_dir = os.path.abspath(args.downloads)
    route = args.route
    dsn = args.dsn or 'postgresql://pguser:pgpass@127.0.0.1:5011/transport'

    print('Using downloads dir:', downloads_dir)
    print('Connecting to DSN:', dsn)

    parsed = compute_canonical_stops(downloads_dir)
    desired = parsed.get(route)
    if desired is None:
        print(f'ERROR: route {route} not found in parsed TXC files')
        return 2

    # Prepare desired rows with contiguous ordering starting at 0
    desired_rows = [(route, atco, idx) for idx, atco in enumerate(desired)]

    if args.dry_run:
        print('\nDRY RUN: Desired rows (%d):' % len(desired_rows))
        for r in desired_rows:
            print('  ', r)

    # Connect and fetch existing rows. Resolve a DB-prefixed route_id so
    # queries target the stored namespaced rows (e.g. "MO100::PC...:RS1").
    with psycopg.connect(dsn) as conn:
        cur = conn.cursor()
        db_route = resolve_prefixed_route_id(conn, route)
        print('Resolved route for DB lookups:', db_route)
        cur.execute('SELECT atco_code, stop_order FROM bus_route_stops WHERE route_id = %s ORDER BY stop_order', (db_route,))
        existing = cur.fetchall()
        print('\nExisting rows (%d):' % len(existing))
        for atco, so in existing:
            print('  ', so, atco)

        # Compare
        existing_atcos = [at for at, _ in existing]
        only_in_existing = [a for a in existing_atcos if a not in desired]
        only_in_desired = [a for a in desired if a not in existing_atcos]
        print('\nOnly in existing:', only_in_existing)
        print('Only in desired:', only_in_desired)

        if args.dry_run:
            # Print SQL that would be run
            tb = safe_table_name(db_route) + '_' + time.strftime('%Y%m%d_%H%M%S')
            print('\n-- SQL preview --')
            print(f'CREATE TABLE {tb} AS SELECT * FROM bus_route_stops WHERE route_id = %s;')
            print('DELETE FROM bus_route_stops WHERE route_id = %s;')
            print('-- INSERT rows:')
            # Use the DB-resolved route id when inserting so stored rows use
            # the same namespaced id the rest of the DB uses.
            for rid, atco, so in desired_rows:
                print("INSERT INTO bus_route_stops (route_id, atco_code, stop_order) VALUES ('%s', '%s', %d);" % (db_route, atco, so))
            return 0

        # Perform repair in a transaction
        tb = safe_table_name(db_route) + '_' + time.strftime('%Y%m%d_%H%M%S')
        print('\nCreating backup table:', tb)
        cur.execute(f'CREATE TABLE {tb} AS SELECT * FROM bus_route_stops WHERE route_id = %s', (db_route,))
        print('Backup created.')

        print('Deleting existing rows for route')
        cur.execute('DELETE FROM bus_route_stops WHERE route_id = %s', (db_route,))

        print('Inserting desired rows...')
        for rid, atco, so in desired_rows:
            # Insert using the DB-resolved namespaced id so rows align with
            # existing legacy-polylines/section_tracks stored under the same
            # prefix.
            cur.execute('INSERT INTO bus_route_stops (route_id, atco_code, stop_order) VALUES (%s, %s, %s)', (db_route, atco, so))

        conn.commit()
        print('Repair committed. Backup table retained:', tb)

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
