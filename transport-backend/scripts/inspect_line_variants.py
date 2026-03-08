#!/usr/bin/env python3
"""Inspect route-variant selection for a given line using the same
heuristics as `api.routes_for_line()` and print detailed diagnostics.

Usage: python3 inspect_line_variants.py --line 11
"""
import argparse
import math
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from main import BUS_DB_PATH
from api import get_router_for_date


def mean_gap(stops):
    if len(stops) < 2:
        return 0.0
    total = 0.0
    cos_lat = math.cos(math.radians(stops[0]["lat"]))
    for i in range(len(stops) - 1):
        dlat = (stops[i + 1]["lat"] - stops[i]["lat"]) * 111_320
        dlon = (stops[i + 1]["lon"] - stops[i]["lon"]) * 111_320 * cos_lat
        total += math.sqrt(dlat * dlat + dlon * dlon)
    return total / (len(stops) - 1)


def max_gap(stops):
    if len(stops) < 2:
        return 0.0
    cos_lat = math.cos(math.radians(stops[0]["lat"]))
    mx = 0.0
    for i in range(len(stops) - 1):
        dlat = (stops[i + 1]["lat"] - stops[i]["lat"]) * 111_320
        dlon = (stops[i + 1]["lon"] - stops[i]["lon"]) * 111_320 * cos_lat
        mx = max(mx, math.sqrt(dlat * dlat + dlon * dlon))
    return mx


def _journey_stops(merged, j_idx, coord_map):
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
        name = merged.stop_metadata[s_int] if s_int < len(merged.stop_metadata) else ""
        stops.append({"name": name or atco_code, "lat": lat, "lon": lon, "atco_code": atco_code})
    return stops


def inspect(line: str, date_str=None):
    date_str = date_str or __import__('datetime').datetime.now().strftime('%Y-%m-%d')
    merged, _router, _walking = get_router_for_date(date_str)

    # Build coord map (prefer atco_loader from api._base_cache if available)
    coord_map = {}
    try:
        # api._base_cache is set by get_router_for_date() during initialization
        import api as _api_mod
        atco_loader = getattr(_api_mod, '_base_cache', {}) and _api_mod._base_cache.get('atco_loader')
        if atco_loader:
            try:
                coord_map = atco_loader.get_all_stop_coords()
            except Exception:
                coord_map = {}
    except Exception:
        coord_map = {}

    # matching routes
    matching = []
    for r_idx, meta in enumerate(merged.route_metadata):
        if meta is None:
            continue
        raw_line = (meta.get('line_name') or '').strip()
        rline = raw_line.split(':')[-1].upper()
        if rline == line.strip().upper():
            matching.append(r_idx)

    print(f"Line {line}: matching_routes={len(matching)} -> indices={matching}")

    # Build route -> journeys map
    route_journeys = {r: [] for r in matching}
    for j_idx, r_idx in enumerate(merged.journey_to_route):
        if r_idx in route_journeys:
            route_journeys[r_idx].append(j_idx)

    # thresholds from env fallbacks
    import os
    min_stops = int(os.environ.get('ROUTE_MIN_STOPS', '6'))
    max_gap_m = int(os.environ.get('ROUTE_MAX_GAP_METERS', '3500'))
    mean_gap_mult = float(os.environ.get('ROUTE_MEAN_GAP_MULT', '1.8'))

    all_variants = []
    for r_idx in matching:
        j_list = route_journeys[r_idx]
        if not j_list:
            continue
        candidates = sorted(j_list, key=lambda j: len(merged.journey_times[j]), reverse=True)[:8]
        candidate_info = []
        best_stops = None
        best_gap = float('inf')
        for j in candidates:
            s = _journey_stops(merged, j, coord_map)
            candidate_info.append((j, len(s)))
            if len(s) < min_stops:
                continue
            gap = mean_gap(s)
            if gap < best_gap:
                best_gap = gap
                best_stops = s
        if best_stops and len(best_stops) >= 2:
            meta = merged.route_metadata[r_idx] or {}
            route_id = meta.get('route_id', f'route_{r_idx}')
            all_variants.append({'route_idx': r_idx, 'route_id': route_id, 'stops': best_stops, 'candidate_counts': candidate_info, 'best_gap': best_gap})

    print(f"candidates_total={sum(len(v['candidate_counts']) for v in all_variants)}")
    print(f"variants_before_dedupe={len(all_variants)}")

    # dedupe by ATCO signature
    seen = set()
    unique = []
    for v in all_variants:
        sig = tuple(s['atco_code'] for s in v['stops'])
        if sig not in seen:
            seen.add(sig)
            unique.append(v)

    print(f"unique_variants={len(unique)}")
    for idx, v in enumerate(unique):
        print(f"  [{idx}] route_id={v['route_id']} stops={len(v['stops'])} mean_gap={v['best_gap']:.1f}")

    if not unique:
        print('No unique variants to filter.')
        return

    # sort and truncate
    unique.sort(key=lambda v: len(v['stops']), reverse=True)
    unique = unique[:3]

    mean_gaps = [mean_gap(v['stops']) for v in unique]
    best = min(mean_gaps)
    filtered = []
    for v, mg in zip(unique, mean_gaps):
        mg_ok = mg <= best * mean_gap_mult
        mg_max_ok = max_gap(v['stops']) < max_gap_m
        filtered.append({'route_id': v['route_id'], 'mean_gap': mg, 'mean_ok': mg_ok, 'max_ok': mg_max_ok})

    print('\nFilter thresholds:')
    print(f"  min_stops={min_stops}, max_gap_m={max_gap_m}, mean_gap_mult={mean_gap_mult}")
    print('\nFilter results:')
    for f in filtered:
        print(f"  {f['route_id']}: mean_gap={f['mean_gap']:.1f} mean_ok={f['mean_ok']} max_ok={f['max_ok']}")

    kept = [f for f in filtered if f['mean_ok'] and f['max_ok']]
    if kept:
        print('\nKept after filters:')
        for k in kept:
            print(' ', k['route_id'])
    else:
        print('\nNo variants survived filters — would fallback to unfiltered top-3')
        print('Unfiltered top-3:')
        for v in unique:
            print(' ', v['route_id'], 'stops=', len(v['stops']))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--line', required=True)
    p.add_argument('--date', help='Date YYYY-MM-DD')
    args = p.parse_args()
    inspect(args.line, date_str=args.date)


if __name__ == '__main__':
    main()
