#!/usr/bin/env python3
import os, sys
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
TOP_DIR = os.path.dirname(THIS_DIR)
if TOP_DIR not in sys.path:
    sys.path.insert(0, TOP_DIR)

from main import initialize_base, build_for_date
from time_utils import seconds_since_midnight, seconds_to_time

DATE_STR = '2026-03-03'
TARGET_TIME = '07:41:00'
WINDOW = 3600
QUERY = 'infolab'


def find_stop_ids(merged, query):
    q = query.lower()
    return [ (i,name) for i,name in enumerate(merged.stop_metadata) if name and q in name.lower() ]


def main():
    base = initialize_base()
    loader = base['loader']
    walking_raw = base['walking_raw']
    atco_loader = base.get('atco_loader')
    start_seconds = seconds_since_midnight('07:25:00')
    target_seconds = seconds_since_midnight(TARGET_TIME)

    merged, router, walking = build_for_date(loader, walking_raw, DATE_STR, start_time=start_seconds, atco_loader=atco_loader)
    infolabs = find_stop_ids(merged, QUERY)
    print('InfoLab stops:', infolabs)
    for sid, sname in infolabs:
        print('\nDepartures near', sname, 'id=', sid)
        routes = merged.stop_to_routes[sid]
        for route in routes:
            stop_deps = merged.get_route_stop_departures(route).get(sid, [])
            for dep_time, j in stop_deps:
                if dep_time >= target_seconds - WINDOW and dep_time <= target_seconds + WINDOW:
                    jmeta = merged.journey_metadata[j] if j < len(merged.journey_metadata) else {}
                    ln = (jmeta or {}).get('line_name','')
                    print(' ', seconds_to_time(int(dep_time)), 'journey=', j, 'line_name=', ln, 'route=', route)

if __name__ == '__main__':
    main()
