#!/usr/bin/env python3
"""Find when a particular line serves a particular merged stop.

Usage: run from transport-backend:
    python3 tools/find_line_stop.py

Adjust DATE_STR, LINE_QUERY, STOP_NAME_QUERY constants below.
"""
import os, sys
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
TOP_DIR = os.path.dirname(THIS_DIR)
if TOP_DIR not in sys.path:
    sys.path.insert(0, TOP_DIR)

from main import initialize_base, build_for_date
from time_utils import seconds_to_time

DATE_STR = '2026-03-03'
LINE_QUERY = '100'  # substring match on journey_metadata['line_name']
STOP_NAME_QUERY = 'Underpass'


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

    merged, router, walking = build_for_date(loader, walking_raw, DATE_STR, start_time=None, atco_loader=atco_loader)

    stops = find_stop_ids(merged, STOP_NAME_QUERY)
    if not stops:
        print('No stops matched name', STOP_NAME_QUERY)
        return
    print('Matched stops:')
    for sid, name in stops:
        print(' ', sid, name)

    for sid, name in stops:
        print('\nChecking journeys that stop at', sid, name)
        served = []
        for j_id, jmeta in enumerate(merged.journey_metadata):
            ln = (jmeta or {}).get('line_name', '')
            if LINE_QUERY in (ln or ''):
                # check if journey stops at sid
                jtimes = merged.journey_times[j_id]
                for pos, (s, atime, dtime) in enumerate(jtimes):
                    if s == sid:
                        served.append((j_id, ln, atime, dtime, pos))
                        break
        if not served:
            print('  No journeys of line', LINE_QUERY, 'serve this stop on', DATE_STR)
        else:
            print('  Found', len(served), 'journeys of line', LINE_QUERY)
            for j_id, ln, atime, dtime, pos in served:
                print('   - journey', j_id, 'line_name="%s" arr=%s dep=%s pos=%d' % (
                    ln, seconds_to_time(int(atime)), seconds_to_time(int(dtime)), pos))

if __name__ == '__main__':
    main()
