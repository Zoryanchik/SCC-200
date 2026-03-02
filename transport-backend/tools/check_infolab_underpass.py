#!/usr/bin/env python3
"""Check ordering of InfoLab21 vs Underpass for the boarded journey.

Find merged stop ids for 'InfoLab' and 'Underpass', then find the
line-100 journey that departs InfoLab around 07:41:00 and print its
full stop sequence (pos, stop_name, arr, dep). This shows the order.
"""
import os, sys
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
TOP_DIR = os.path.dirname(THIS_DIR)
if TOP_DIR not in sys.path:
    sys.path.insert(0, TOP_DIR)

from main import initialize_base, build_for_date
from time_utils import seconds_since_midnight, seconds_to_time

DATE_STR = '2026-03-03'
START_TIME = '07:25:00'
TARGET_BOARD_DEPART = '07:41:00'  # the departure time seen in the route print
LINE_SUBSTR = '100'
INFOLAB_QUERY = 'infolab'
UNDERPASS_QUERY = 'underpass'


def find_stop_ids(merged, query):
    q = query.lower()
    matches = []
    for i, name in enumerate(merged.stop_metadata):
        if name and q in name.lower():
            matches.append((i, name))
    return matches


def main():
    print('Initializing base...')
    base = initialize_base()
    loader = base['loader']
    walking_raw = base['walking_raw']
    atco_loader = base.get('atco_loader')

    start_seconds = seconds_since_midnight(START_TIME)
    target_board_seconds = seconds_since_midnight(TARGET_BOARD_DEPART)

    merged, router, walking = build_for_date(loader, walking_raw, DATE_STR, start_time=start_seconds, atco_loader=atco_loader)

    infolab = find_stop_ids(merged, INFOLAB_QUERY)
    underpass = find_stop_ids(merged, UNDERPASS_QUERY)

    print('InfoLab matches:', infolab)
    print('Underpass matches:', underpass)

    # Find InfoLab id to use (choose first match)
    if not infolab:
        print('No InfoLab found in merged.stop_metadata')
        return
    infolab_id = infolab[0][0]

    # Find candidate journeys serving infolab at around target_board_seconds and line 100
    candidates = []  # (j_id, dep_time)
    # iterate routes serving infolab
    for route in merged.stop_to_routes[infolab_id]:
        stop_deps = merged.route_stop_departures[route].get(infolab_id, [])
        for dep_time, j_id in stop_deps:
            # restrict to a 10-minute window around target_board_seconds
            if abs(dep_time - target_board_seconds) <= 600:
                # check journey metadata line_name contains LINE_SUBSTR
                jmeta = merged.journey_metadata[j_id] if j_id < len(merged.journey_metadata) else {}
                ln = (jmeta or {}).get('line_name','')
                if LINE_SUBSTR in (ln or ''):
                    candidates.append((j_id, dep_time, route, ln))

    if not candidates:
        print('No candidate line-100 journeys found boarding at InfoLab near', TARGET_BOARD_DEPART)
        return

    # Pick the earliest matching candidate by dep_time
    candidates.sort(key=lambda x: x[1])
    j_id, dep_time, route, ln = candidates[0]
    print('\nSelected journey id=%d dep=%s route=%s line_name=%s' % (j_id, seconds_to_time(int(dep_time)), route, ln))

    # Print the full stop sequence for the journey
    jt = merged.journey_times[j_id]
    print('\nJourney stop sequence (pos, merged_stop_id, stop_name, arr, dep):')
    for pos, (s, atime, dtime) in enumerate(jt):
        name = merged.stop_metadata[s] if s < len(merged.stop_metadata) else f'stop#{s}'
        print(' %3d %6d  %40s  %8s  %8s' % (pos, s, name[:40], seconds_to_time(int(atime)), seconds_to_time(int(dtime))))

    # If underpass ids exist, show their positions in this journey
    if underpass:
        print('\nUnderpass positions in this journey:')
        for sid, sname in underpass:
            pos = None
            for p, (s, atime, dtime) in enumerate(jt):
                if s == sid:
                    pos = p
                    print('  id=%d name="%s" pos=%d arr=%s dep=%s' % (sid, sname, p, seconds_to_time(int(atime)), seconds_to_time(int(dtime))))
            if pos is None:
                print('  id=%d name="%s" not present in this journey' % (sid, sname))

if __name__ == '__main__':
    main()
