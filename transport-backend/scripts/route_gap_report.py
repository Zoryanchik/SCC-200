#!/usr/bin/env python3
"""Compute haversine distances between consecutive stops for a route_id.

Usage: python3 scripts/route_gap_report.py ROUTE_ID

Prints each stop_order with atco, lat, lon and distance to next stop (meters),
then prints the top N largest gaps.
"""
import sys
from math import radians, sin, cos, atan2, sqrt
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
import os
sys.path.insert(0, str(PROJECT_ROOT))
from main import BUS_DB_PATH
from route_id_utils import resolve_prefixed_route_id
import psycopg

if len(sys.argv) < 2:
    print("Usage: python3 scripts/route_gap_report.py ROUTE_ID")
    sys.exit(2)

route_id = sys.argv[1]
THRESH = 3500

def haversine_m(lat1, lon1, lat2, lon2):
    # mean earth radius in meters
    R = 6371000.0
    phi1 = radians(lat1)
    phi2 = radians(lat2)
    dphi = radians(lat2 - lat1)
    dlambda = radians(lon2 - lon1)
    a = sin(dphi/2.0)**2 + cos(phi1)*cos(phi2)*sin(dlambda/2.0)**2
    c = 2 * atan2(sqrt(a), sqrt(1-a))
    return R * c

print(f"Connecting to DB: {BUS_DB_PATH}")
conn = psycopg.connect(BUS_DB_PATH)
cur = conn.cursor()

# Resolve DB-prefixed id (if available) so queries target stored namespaced rows
db_route = resolve_prefixed_route_id(conn, route_id)
if db_route != route_id:
    print(f"Resolved {route_id} -> {db_route} for DB lookups")

cur.execute(
    """
    SELECT s.stop_order, s.atco_code, sc.lat, sc.lon
    FROM bus_route_stops s
    LEFT JOIN stop_coords sc ON sc.atco_code = s.atco_code
    WHERE s.route_id = %s
    ORDER BY s.stop_order
    """,
    (db_route,)
)
rows = cur.fetchall()
if not rows:
    print(f"No rows found for route_id {route_id}")
    sys.exit(0)

print(f"Found {len(rows)} stops for route {route_id}")

gaps = []
for i in range(len(rows)-1):
    o1, atco1, lat1, lon1 = rows[i]
    o2, atco2, lat2, lon2 = rows[i+1]
    if lat1 is None or lat2 is None or lon1 is None or lon2 is None:
        dist = None
    else:
        dist = haversine_m(lat1, lon1, lat2, lon2)
    gaps.append((i+1, o1, atco1, lat1, lon1, o2, atco2, lat2, lon2, dist))

# print all with distances
for g in gaps:
    idx, o1, a1, lat1, lon1, o2, a2, lat2, lon2, dist = g
    dstr = f"{dist:.1f} m" if dist is not None else "MISSING COORDS"
    flag = " <-- LARGE GAP" if (dist is not None and dist > THRESH) else ""
    print(f"{o1:3d}->{o2:3d}: {a1} -> {a2}: {dstr}{flag}")

# summary: top 10 largest gaps
valid = [g for g in gaps if g[9] is not None]
valid_sorted = sorted(valid, key=lambda x: x[9], reverse=True)
print('\nTop gaps:')
for g in valid_sorted[:10]:
    idx, o1, a1, lat1, lon1, o2, a2, lat2, lon2, dist = g
    print(f"{o1}->{o2}: {a1} -> {a2}: {dist:.1f} m")

big = [g for g in valid if g[9] > THRESH]
print(f"\nGaps > {THRESH} m: {len(big)}")
for g in big:
    idx, o1, a1, lat1, lon1, o2, a2, lat2, lon2, dist = g
    print(f"  {o1}->{o2}: {a1} -> {a2}: {dist:.1f} m")

cur.close()
conn.close()
print('\nDone.')
