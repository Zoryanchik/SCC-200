#!/usr/bin/env python3
import os, sys
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
TOP_DIR = os.path.dirname(THIS_DIR)
if TOP_DIR not in sys.path:
    sys.path.insert(0, TOP_DIR)

from api import get_router_for_date
from datetime import datetime
import math

DATE = datetime.now().strftime('%Y-%m-%d')
LINE = '42'

print('Building router for', DATE)
merged, router, walking = get_router_for_date(DATE)
print('len(route_metadata)=', len(merged.route_metadata))

# find matching route indices as routes_for_line does
matching_routes = []
for r_idx, meta in enumerate(merged.route_metadata):
    if meta is None:
        continue
    raw_line = (meta.get('line_name') or '').strip()
    rline = raw_line.split(':')[-1].upper()
    if rline == LINE:
        matching_routes.append(r_idx)

print('matching_routes count=', len(matching_routes))

# build route -> journey mapping
route_journeys = {r: [] for r in matching_routes}
for j_idx, r_idx in enumerate(merged.journey_to_route):
    if r_idx in route_journeys:
        route_journeys[r_idx].append(j_idx)

# helper to get stops for a journey
# get atco coords map
atco = _base_atco = None
try:
    atco_loader = getattr(__import__('main')._base_cache, 'get', lambda k: None)('atco_loader')
except Exception:
    atco_loader = None
coord_map = {}
try:
    coord_map = merged.atco.get_all_stop_coords() if getattr(merged, 'atco', None) else {}
except Exception:
    coord_map = {}

def journey_stops(j_idx):
    stops = []
    for entry in merged.journey_times[j_idx]:
        s_int = entry[0]
        atco_code = merged.get_atco_code(s_int)
        if not atco_code:
            continue
        coords = coord_map.get(atco_code)
        if not coords:
            continue
        lat, lon = coords
        name = merged.stop_metadata[s_int] if s_int < len(merged.stop_metadata) else ''
        stops.append({'name': name or atco_code, 'lat': lat, 'lon': lon, 'atco_code': atco_code})
    return stops

# mean gap function
def mean_gap(stops):
    if len(stops) < 2:
        return float('inf')
    total = 0.0
    cos_lat = math.cos(math.radians(stops[0]['lat']))
    for i in range(len(stops)-1):
        dlat = (stops[i+1]['lat'] - stops[i]['lat']) * 111320
        dlon = (stops[i+1]['lon'] - stops[i]['lon']) * 111320 * cos_lat
        total += math.sqrt(dlat*dlat + dlon*dlon)
    return total / (len(stops)-1)

# Inspect each matching route
for r_idx in matching_routes:
    meta = merged.route_metadata[r_idx] or {}
    route_id = meta.get('route_id')
    j_list = route_journeys.get(r_idx, [])
    print('\nRoute idx', r_idx, 'route_id=', route_id, 'journeys_count=', len(j_list))
    if not j_list:
        continue
    # candidates: top 8 longest journeys
    candidates = sorted(j_list, key=lambda j: len(merged.journey_times[j]), reverse=True)[:8]
    cand_info = []
    for j in candidates:
        s = journey_stops(j)
        mg = mean_gap(s) if s else float('inf')
        cand_info.append((j, len(s), mg))
    print('Candidates (j_idx, stops_count, mean_gap):')
    for ci in cand_info:
        print(' ', ci)
    # choose best
    best = None
    best_gap = float('inf')
    best_stops = None
    for j in candidates:
        s = journey_stops(j)
        if len(s) < 6:
            continue
        mg = mean_gap(s)
        if mg < best_gap:
            best_gap = mg
            best = j
            best_stops = s
    print('Chosen best journey:', best, 'stops_len=', len(best_stops) if best_stops else 0, 'best_gap=', best_gap)

# Also show any duplicate route_id values across matching_routes
from collections import defaultdict
rid_map = defaultdict(list)
for r_idx in matching_routes:
    meta = merged.route_metadata[r_idx] or {}
    rid_map[meta.get('route_id')].append(r_idx)

print('\nRoute_id -> route_idx map (only duplicates shown):')
for rid, lst in rid_map.items():
    if len(lst) > 1:
        print(' ', rid, '->', lst)
