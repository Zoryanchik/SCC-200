#!/usr/bin/env python3
"""Dump revision and counts for route and journey tables for auditing.

Prints CSV-like output to stdout with columns:
  type, route_or_journey_id, row_count, max_revision

Usage:
  python3 scripts/dump_revision_log.py
"""
import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
from main import BUS_DB_PATH
import psycopg

def dump():
    conn = psycopg.connect(BUS_DB_PATH)
    cur = conn.cursor()

    print('# ROUTE_ID,ROW_COUNT,MAX_REVISION')
    cur.execute("SELECT route_id, COUNT(*), MAX(revision) FROM bus_route_stops GROUP BY route_id ORDER BY COUNT(*) DESC LIMIT 1000")
    for rid, cnt, rev in cur.fetchall():
        print(f"R,{rid},{cnt},{rev if rev is not None else ''}")

    print('\n# JOURNEY_ID,ROW_COUNT,MAX_REVISION')
    cur.execute("SELECT route_id, COUNT(journey_id), MAX(revision) FROM bus_journey_routes GROUP BY route_id ORDER BY COUNT(journey_id) DESC LIMIT 1000")
    for rid, cnt, rev in cur.fetchall():
        print(f"J,{rid},{cnt},{rev if rev is not None else ''}")

    cur.close()
    conn.close()

if __name__ == '__main__':
    dump()
