#!/usr/bin/env python3
"""Dump full rows for specified route_ids (route_stops, route_tracks, journey_routes).

Usage:
  python3 scripts/dump_route_details.py ROUTE_ID [ROUTE_ID ...]

If no ROUTE_IDs provided, defaults to the two previously-flagged examples.
"""
import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
import os
sys.path.insert(0, str(PROJECT_ROOT))
from main import BUS_DB_PATH
import psycopg

DEFAULTS = [
    'KLCO::PC1016989:47:rt_0001',
    'KLCO::PC1016989:47:rt_0003',
]

route_ids = sys.argv[1:] or DEFAULTS

print(f"Connecting to DSN: {BUS_DB_PATH}")
try:
    conn = psycopg.connect(BUS_DB_PATH)
except Exception as e:
    print(f"ERROR: could not connect to DB: {e}")
    sys.exit(2)
cur = conn.cursor()

for rid in route_ids:
    print('\n' + '='*80)
    print(f"Route: {rid}")
    print('-' * 40)
    # route_stops
    try:
        cur.execute(
            "SELECT route_id, atco_code, stop_order, revision FROM bus_route_stops WHERE route_id = %s ORDER BY stop_order, atco_code",
            (rid,)
        )
        rows = cur.fetchall()
        print(f"bus_route_stops ({len(rows)} rows):")
        for r in rows:
            print('  ', r)
    except Exception as e:
        print(f"  ERROR querying bus_route_stops: {e}")

    # route_tracks
    try:
        cur.execute(
            "SELECT route_id, seq, lat, lon FROM bus_route_tracks WHERE route_id = %s ORDER BY seq",
            (rid,)
        )
        rows = cur.fetchall()
        print(f"bus_route_tracks ({len(rows)} rows):")
        for r in rows[:200]:
            print('  ', r)
    except Exception as e:
        print(f"  ERROR querying bus_route_tracks: {e}")

    # journey_routes referencing this route
    try:
        cur.execute(
            "SELECT journey_id, route_id, line_name, destination_display, revision FROM bus_journey_routes WHERE route_id = %s ORDER BY journey_id LIMIT 200",
            (rid,)
        )
        rows = cur.fetchall()
        print(f"bus_journey_routes referencing this route ({len(rows)} rows returned up to 200):")
        for r in rows:
            print('  ', r)
    except Exception as e:
        print(f"  ERROR querying bus_journey_routes: {e}")

    # Also show any journey_times for journeys that reference this route (sample up to 5 journeys)
    try:
        cur.execute(
            "SELECT journey_id FROM bus_journey_routes WHERE route_id = %s LIMIT 5",
            (rid,)
        )
        jrows = [r[0] for r in cur.fetchall()]
        if jrows:
            print(f"Showing journey_times for up to 5 journeys: {jrows}")
            for jid in jrows:
                cur.execute(
                    "SELECT journey_id, atco_code, arrival_time FROM bus_journey_times WHERE journey_id = %s ORDER BY arrival_time",
                    (jid,)
                )
                jt = cur.fetchall()
                print(f"  journey {jid}: {len(jt)} times")
                for t in jt[:20]:
                    print('    ', t)
        else:
            print("No journeys found for this route in bus_journey_routes.")
    except Exception as e:
        print(f"  ERROR querying journey_times: {e}")

cur.close()
conn.close()
print('\nDone.')
