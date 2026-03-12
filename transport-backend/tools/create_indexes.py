#!/usr/bin/env python3
"""Create recommended indexes (CONCURRENTLY) to speed loader queries.

This script connects to the BUS_DB_PATH DSN from main.py and issues
CREATE INDEX CONCURRENTLY IF NOT EXISTS statements. It sets autocommit
so CONCURRENTLY can run outside a transaction.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from main import BUS_DB_PATH
import psycopg

idx_sql = [
    # index to allow ordered scan by (journey_id, arrival_time)
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_jt_journey_arrival ON bus_journey_times (journey_id, arrival_time)",
    # index to speed up COUNT(DISTINCT atco_code) or IN() lookups
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_route_stops_atco ON bus_route_stops (atco_code)",
]

print('Connecting to', BUS_DB_PATH)
conn = psycopg.connect(BUS_DB_PATH)
# psycopg3: use autocommit for CREATE INDEX CONCURRENTLY
try:
    conn.autocommit = True
except Exception:
    pass
cur = conn.cursor()
for s in idx_sql:
    print('Executing:', s)
    try:
        cur.execute(s)
        print('OK')
    except Exception as e:
        print('Failed:', e)

cur.close()
conn.close()
print('Done.')
