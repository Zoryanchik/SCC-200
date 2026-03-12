#!/usr/bin/env python3
"""Verify index usage, EXPLAIN the loader query, and report NULL arrival_time counts.

Run from transport-backend/.
"""
import os
import sys
from datetime import date as _date
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from main import BUS_DB_PATH
import psycopg

PROFILE_DAY = os.environ.get('PROFILE_DAY_A', '2026-02-16')
qd = _date.fromisoformat(PROFILE_DAY)
dow_bit = 1 << qd.weekday()

conn = psycopg.connect(BUS_DB_PATH)
cur = conn.cursor()

print('Checking for index idx_jt_journey_arrival on bus_journey_times...')
cur.execute("SELECT indexname, indexdef FROM pg_indexes WHERE tablename='bus_journey_times' AND indexname='idx_jt_journey_arrival'")
rows = cur.fetchall()
if rows:
    print('Index found:')
    for name, idxdef in rows:
        print(name)
        print(idxdef)
else:
    print('Index idx_jt_journey_arrival not found')

print('\nCounting NULL arrival_time rows...')
cur.execute('SELECT COUNT(*) FROM bus_journey_times WHERE arrival_time IS NULL')
nulls = cur.fetchone()[0]
print('NULL arrival_time rows:', nulls)

print('\nEXPLAIN ANALYZE for the exact loader query (limit 100000)')
query = (
    "WITH valid_journeys AS ("
    " SELECT journey_id FROM bus_journey_operating_profile WHERE (days_of_week & %s) != 0"
    " )"
    " SELECT jt.journey_id, jt.atco_code, jt.arrival_time FROM bus_journey_times jt "
    " JOIN valid_journeys vj ON jt.journey_id = vj.journey_id"
    " ORDER BY jt.journey_id, jt.arrival_time"
    " LIMIT 100000"
)
try:
    cur.execute('EXPLAIN (ANALYZE, BUFFERS, VERBOSE) ' + query, (dow_bit,))
    for r in cur.fetchall():
        print(r[0])
except Exception as e:
    print('EXPLAIN failed:', e)

conn.close()
print('\nDone.')
