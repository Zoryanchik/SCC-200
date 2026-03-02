#!/usr/bin/env python3
import os, sys
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
TOP_DIR = os.path.dirname(THIS_DIR)
if TOP_DIR not in sys.path:
    sys.path.insert(0, TOP_DIR)
from main import initialize_base, build_for_date
from time_utils import seconds_to_time

DATE_STR = '2026-03-03'
LINE_QUERY = '100'
STOP_NAME_QUERY = 'Underpass'
START_WINDOW = '06:00:00'
END_WINDOW = '09:00:00'

from time_utils import seconds_since_midnight


def find_stop_ids(merged, query):
    q = query.lower()
    matches = []
    for i, name in enumerate(merged.stop_metadata):
        if name and q in name.lower():
            matches.append((i, name))
    return matches


def main():
    base = initialize_base()
    loader = base['loader']
    walking_raw = base['walking_raw']
    atco_loader = base.get('atco_loader')
    merged, router, walking = build_for_date(loader, walking_raw, DATE_STR, start_time=None, atco_loader=atco_loader)
    stops = find_stop_ids(merged, STOP_NAME_QUERY)
    if not stops:
        print('No Underpass stops')
        return
    start_sec = seconds_since_midnight(START_WINDOW)
    end_sec = seconds_since_midnight(END_WINDOW)
    for sid, name in stops:
        print('\nStop id=%d name=%s' % (sid, name))
        times = []
        for j_id, jmeta in enumerate(merged.journey_metadata):
            ln = (jmeta or {}).get('line_name', '')
            if LINE_QUERY in (ln or ''):
                jt = merged.journey_times[j_id]
                for s, atime, dtime in jt:
                    if s == sid and atime >= start_sec and atime <= end_sec:
                        times.append((j_id, ln, atime, dtime))
                        break
        if not times:
            print('  no line 100 times in window')
        else:
            for j_id, ln, atime, dtime in sorted(times, key=lambda x: x[2]):
                print('  %s  journey=%d  line_name="%s"' % (seconds_to_time(int(atime)), j_id, ln))

if __name__ == '__main__':
    main()
