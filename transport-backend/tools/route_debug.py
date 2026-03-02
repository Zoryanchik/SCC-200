#!/usr/bin/env python3
"""Small helper to run the router with debug for specific stop name(s).

Usage: run from repository root:

    cd transport-backend
    python3 tools/route_debug.py

The script will:
 - initialize the base (downloads/prebuild may run if needed),
 - build the network for the provided date/time,
 - search `merged.stop_metadata` for the substring 'Underpass' (case-insensitive),
 - call `router.route(..., debug_stop_ids={ids})` and print the recorded events.

Adjust START/DEST/DATE/TIME below for your case.
"""

import os
import sys

# Ensure the transport-backend package path is on sys.path so local imports work
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
TOP_DIR = os.path.dirname(THIS_DIR)  # transport-backend
if TOP_DIR not in sys.path:
    sys.path.insert(0, TOP_DIR)

from main import initialize_base, build_for_date, print_route
from time_utils import seconds_since_midnight, seconds_to_time

# Example inputs from your report
DATE_STR = '2026-03-03'
TIME_STR = '07:25:00'
START_POINT = (54.01033, -2.78359)
DEST_POINT = (54.05156, -2.79937)
MAX_TRANSFERS = 5
MODE = 'both'  # or 'bus', 'train'
STOP_NAME_QUERY = 'Underpass'  # case-insensitive substring to match


def find_stop_ids(merged, query):
    q = query.lower()
    matches = []
    for i, name in enumerate(merged.stop_metadata):
        if name and q in name.lower():
            matches.append((i, name))
    return matches


def main():
    print('Initializing base (may take a moment the first time)...')
    base = initialize_base()
    loader = base['loader']
    walking_raw = base['walking_raw']
    atco_loader = base.get('atco_loader')

    start_seconds = seconds_since_midnight(TIME_STR)

    print(f'Building network for {DATE_STR} at {TIME_STR}...')
    merged, router, walking = build_for_date(
        loader, walking_raw, DATE_STR, start_time=start_seconds, atco_loader=atco_loader)

    print('Searching stop names for query:', STOP_NAME_QUERY)
    matches = find_stop_ids(merged, STOP_NAME_QUERY)
    if not matches:
        print('No stops matched that substring in merged.stop_metadata')
    else:
        print(f'Found {len(matches)} matching stop(s):')
        for sid, sname in matches:
            print(f'  id={sid} name="{sname}"')

    debug_ids = {sid for sid, _ in matches}
    if not debug_ids:
        print('\nNo debug IDs found — running router without debug and printing route')
        result = router.route(
            n_transfer_limit=MAX_TRANSFERS,
            walking=walking,
            start_time=start_seconds,
            start_point=START_POINT,
            destination=DEST_POINT,
            allowed_modes={"bus", "train"},
        )
        print_route(result, merged)
        return

    print('\nRunning router with debug_stop_ids =', debug_ids)
    # Also inspect which stops are reachable directly from the origin
    initial_reachable = walking.reachable_stops(START_POINT)
    print('\nInitial reachable stops (from start) — first 30 shown:')
    for s, wt in initial_reachable[:30]:
        mark = ' (debug)' if s in debug_ids else ''
        print(f'  id={s} walk_secs={wt}{mark}')

    # Check if any debug stop is in the initial reachable set
    initial_ids = {s for s, _ in initial_reachable}
    inters = debug_ids & initial_ids
    if inters:
        print('\nDebug stop(s) reachable by initial walk:', inters)
    else:
        print('\nNone of the debug stops are directly reachable by initial walk from the start point.')
    result = router.route(
        n_transfer_limit=MAX_TRANSFERS,
        walking=walking,
        start_time=start_seconds,
        start_point=START_POINT,
        destination=DEST_POINT,
        allowed_modes={"bus", "train"},
        debug_stop_ids=debug_ids,
    )

    # For each debug stop show nearby departures (useful to see why boarding failed)
    print('\nDepartures near start time for debug stops:')
    for sid in sorted(debug_ids):
        name = merged.stop_metadata[sid] if sid < len(merged.stop_metadata) else f'stop#{sid}'
        print(f'\n  Stop id={sid} name="{name}"')
        routes = merged.stop_to_routes[sid]
        if not routes:
            print('    (no routes serve this stop)')
            continue
        for route in routes:
            stop_deps = merged.route_stop_departures[route].get(sid, [])
            for dep_time, j in stop_deps:
                # show a one-hour window around the planned start
                if dep_time >= start_seconds - 600 and dep_time <= start_seconds + 3600:
                    print(f'    dep {seconds_to_time(int(dep_time))}  (route={route}, journey={j})')

    # Pull debug events from router instance (populated into self._debug_events)
    events = getattr(router, '_debug_events', None)
    if not events:
        print('\nNo debug events recorded for those stops (they may not have been touched).')
    else:
        print('\nDebug events (chronological):')
        for ev in events:
            print(' ', ev)

    print('\nFinal route:')
    print_route(result, merged)


if __name__ == '__main__':
    main()
