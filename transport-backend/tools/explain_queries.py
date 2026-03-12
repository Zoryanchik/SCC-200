#!/usr/bin/env python3
"""Run EXPLAIN ANALYZE on selected heavy queries to inspect query plans.

This script uses a simplified "valid_journeys" CTE (based on days_of_week)
which approximates the temp table used by `load_busdata_for_date`.

Run from the `transport-backend/` directory.
"""
import os
import sys
from datetime import date as _date
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from main import BUS_DB_PATH
import psycopg

PROFILE_DAY = os.environ.get('PROFILE_DAY_A', '2026-02-16')

# compute dow_bit similarly to loader
qd = _date.fromisoformat(PROFILE_DAY)
dow_bit = 1 << qd.weekday()

conn = psycopg.connect(BUS_DB_PATH)
cur = conn.cursor()

def run_explain(query, params=None):
    print('\n--- EXPLAIN ANALYZE:')
    print(query)
    try:
        cur.execute('EXPLAIN (ANALYZE, BUFFERS, VERBOSE) ' + query, params or ())
        rows = cur.fetchall()
        for r in rows:
            print(r[0])
    except Exception as e:
        print('EXPLAIN failed:', e)

# 1) journey_times join (approximate valid_journeys via days_of_week)
jt_query = (
    "WITH valid_journeys AS ("
    " SELECT journey_id FROM bus_journey_operating_profile WHERE (days_of_week & %s) != 0"
    " )"
    " SELECT jt.journey_id, jt.atco_code, jt.arrival_time FROM bus_journey_times jt "
    " JOIN valid_journeys vj ON jt.journey_id = vj.journey_id"
    " ORDER BY jt.journey_id, jt.arrival_time"
)
# limit output rows to keep explain fast
run_explain(jt_query + " LIMIT 100000", (dow_bit,))

# 2) count distinct stops
run_explain("SELECT COUNT(DISTINCT atco_code) FROM bus_route_stops")

# 3) route_tracks join
run_explain(
    "SELECT rt.route_id, rt.lat, rt.lon FROM bus_route_tracks rt "
    "JOIN (SELECT DISTINCT route_id FROM bus_journey_routes) vr ON rt.route_id = vr.route_id "
    "ORDER BY rt.route_id, rt.seq LIMIT 100000"
)

conn.close()
print('\nDone.')
