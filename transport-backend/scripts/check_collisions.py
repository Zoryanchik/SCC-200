#!/usr/bin/env python3
"""Diagnostics: check for route/journey collisions and anomalies.

Runs several SQL checks against the bus tables and prints a concise
report plus saves JSON output to cache/collision_report.json (if writable).

Usage: python3 scripts/check_collisions.py
"""
import json
import os
import sys
from pathlib import Path

# Add project path to import main.BUS_DB_PATH
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from main import BUS_DB_PATH
import psycopg

OUT_PATH = PROJECT_ROOT / 'cache' / 'collision_report.json'

CHECKS = [
    {
        'id': 'journey_multi_routes',
        'desc': 'Journeys that reference multiple distinct route_id values',
        'sql': """
            SELECT journey_id, COUNT(DISTINCT route_id) AS distinct_routes,
                   array_agg(DISTINCT route_id) AS routes
            FROM bus_journey_routes
            GROUP BY journey_id
            HAVING COUNT(DISTINCT route_id) > 1
            LIMIT 200
        """,
    },
    {
        'id': 'journey_no_times',
        'desc': 'Journeys present in bus_journey_routes but with no journey_times rows',
        'sql': """
            SELECT jr.journey_id, jr.route_id
            FROM bus_journey_routes jr
            LEFT JOIN bus_journey_times jt ON jr.journey_id = jt.journey_id
            WHERE jt.journey_id IS NULL
            LIMIT 200
        """,
    },
    {
        'id': 'route_stop_order_gaps',
        'desc': 'Routes with non-consecutive stop_order (min!=0 or max!=count-1)',
        'sql': """
            SELECT route_id, MIN(stop_order) AS min_o, MAX(stop_order) AS max_o, COUNT(*) AS cnt
            FROM bus_route_stops
            GROUP BY route_id
            HAVING MIN(stop_order) != 0 OR MAX(stop_order) != COUNT(*) - 1
            LIMIT 200
        """,
    },
    {
        'id': 'journey_times_not_monotonic',
        'desc': 'Journeys where arrival_time decreases between consecutive stops',
        'sql': """
            SELECT DISTINCT journey_id FROM (
              SELECT journey_id, arrival_time,
                     lag(arrival_time) OVER (PARTITION BY journey_id ORDER BY arrival_time) AS prev
              FROM bus_journey_times
            ) t
            WHERE prev IS NOT NULL AND arrival_time < prev
            LIMIT 200
        """,
    },
    {
        'id': 'journey_missing_route',
        'desc': 'Journeys that reference a route_id not present in bus_route_stops',
        'sql': """
            SELECT jr.journey_id, jr.route_id
            FROM bus_journey_routes jr
            WHERE NOT EXISTS (SELECT 1 FROM bus_route_stops rs WHERE rs.route_id = jr.route_id)
            LIMIT 200
        """,
    },
    {
        'id': 'duplicate_journey_times',
        'desc': 'Duplicate journey_time entries for the same (journey_id, atco_code)',
        'sql': """
            SELECT journey_id, atco_code, COUNT(*) AS cnt
            FROM bus_journey_times
            GROUP BY journey_id, atco_code
            HAVING COUNT(*) > 1
            LIMIT 200
        """,
    },
    {
        'id': 'routes_with_single_stop',
        'desc': 'Routes that only have a single stop (likely malformed)',
        'sql': """
            SELECT route_id, COUNT(*) AS cnt FROM bus_route_stops GROUP BY route_id HAVING COUNT(*) <= 1 LIMIT 200
        """,
    },
]


def run_checks(dsn):
    report = {'dsn': dsn, 'results': {}}
    try:
        conn = psycopg.connect(dsn)
    except Exception as e:
        print(f"ERROR: Unable to connect to DB using DSN {dsn}: {e}")
        return None

    cur = conn.cursor()
    for chk in CHECKS:
        try:
            cur.execute(chk['sql'])
            rows = cur.fetchall()
            report['results'][chk['id']] = {
                'desc': chk['desc'],
                'count': len(rows),
                'sample': [list(r) for r in (rows[:50] if rows else [])]
            }
        except Exception as e:
            report['results'][chk['id']] = {
                'desc': chk['desc'],
                'error': str(e)
            }
    cur.close()
    conn.close()
    return report


def save_report(r, path=OUT_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, 'w') as f:
            json.dump(r, f, indent=2)
        return True
    except Exception as e:
        print(f"WARNING: Failed to write report to {path}: {e}")
        return False


if __name__ == '__main__':
    print(f"Running collision/anomaly checks against DSN: {BUS_DB_PATH}")
    rep = run_checks(BUS_DB_PATH)
    if rep is None:
        sys.exit(2)
    # Print concise summary
    print('\nSummary:')
    for k, v in rep['results'].items():
        if 'error' in v:
            print(f" - {k}: ERROR: {v['error']}")
        else:
            print(f" - {k}: {v['count']} (desc: {v['desc']})")
    # Save full JSON report
    ok = save_report(rep)
    if ok:
        print(f"\nFull report written to: {OUT_PATH}")
    else:
        print("\nFull report not saved; see printed summary above.")
    # Also print first few samples for any non-zero issues
    print('\nDetails (first 5 samples per issue):')
    for k, v in rep['results'].items():
        if 'error' in v:
            print(f"\n[{k}] ERROR: {v['error']}")
        elif v['count']:
            print(f"\n[{k}] {v['desc']} — {v['count']} examples (showing up to 5):")
            for row in v['sample'][:5]:
                print('  ', row)

