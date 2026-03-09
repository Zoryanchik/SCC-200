#!/usr/bin/env python3
"""Print arrival times for each journey on a given line.

Usage: python3 print_line_arrivals.py --line 11
"""
import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from api import get_router_for_date


def sec_to_hms(sec):
    if sec is None:
        return "--:--:--"
    sec = int(sec)
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--line', required=True, help='Line string to match, e.g. 11')
    p.add_argument('--date', help='Date YYYY-MM-DD')
    args = p.parse_args()

    date_str = args.date or __import__('datetime').datetime.now().strftime('%Y-%m-%d')
    merged, _router, _walking = get_router_for_date(date_str)

    # Find routes matching the line
    matching = []
    for r_idx, meta in enumerate(merged.route_metadata):
        if not meta:
            continue
        raw_line = (meta.get('line_name') or '').strip()
        rline = raw_line.split(':')[-1].upper()
        if rline == args.line.strip().upper():
            matching.append((r_idx, meta))

    if not matching:
        print(f"No routes found for line {args.line}")
        return

    print(f"Found {len(matching)} route(s) for line {args.line}: indices={[r for r,_ in matching]}")

    # For each matching route, iterate journeys and print arrival times
    for r_idx, meta in matching:
        print('\n' + '='*60)
        print(f"Route index: {r_idx}, route_id: {meta.get('route_id')}, line_name: {meta.get('line_name')}")
        journey_ids = merged.route_journeys[r_idx]
        print(f"  journeys: {len(journey_ids)}")
        # iterate a few journeys (limit to 10 to avoid huge output)
        for j_idx in journey_ids[:20]:
            jm = merged.journey_metadata[j_idx] or {}
            jlabel = jm.get('journey_id') or jm.get('vehicle_id') or str(j_idx)
            print('\n  Journey', jlabel, f"(j_idx={j_idx}):")
            entries = merged.journey_times[j_idx]
            for pos, (stop_int, atime, dtime) in enumerate(entries):
                atco = merged.get_atco_code(stop_int)
                name = merged.stop_metadata[stop_int] if stop_int < len(merged.stop_metadata) else ''
                atime_str = sec_to_hms(atime)
                print(f"    {pos:02d}. {name or atco} ({atco})  arrival={atime_str}")
                if 'george' in (name or '').lower():
                    print('      --> Contains George in stop name')


if __name__ == '__main__':
    main()
