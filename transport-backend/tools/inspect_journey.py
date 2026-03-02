#!/usr/bin/env python3
import os, sys
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
TOP_DIR = os.path.dirname(THIS_DIR)
if TOP_DIR not in sys.path:
    sys.path.insert(0, TOP_DIR)
from main import initialize_base, build_for_date
from time_utils import seconds_to_time

DATE_STR = '2026-03-03'
J_ID = 11983

def main():
    base = initialize_base()
    loader = base['loader']
    walking_raw = base['walking_raw']
    atco_loader = base.get('atco_loader')
    merged, router, walking = build_for_date(loader, walking_raw, DATE_STR, start_time=None, atco_loader=atco_loader)

    if J_ID >= len(merged.journey_times):
        print('Journey id out of range')
        return
    jt = merged.journey_times[J_ID]
    print('Journey', J_ID, 'stop sequence:')
    for p, (s, atime, dtime) in enumerate(jt):
        name = merged.stop_metadata[s] if s < len(merged.stop_metadata) else f'stop#{s}'
        print(' %2d  id=%5d  %40s  arr=%8s  dep=%8s' % (p, s, name[:40], seconds_to_time(int(atime)), seconds_to_time(int(dtime))))

if __name__ == '__main__':
    main()
