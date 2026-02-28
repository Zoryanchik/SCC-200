#!/usr/bin/env python3
"""Profile a single RaptorRouter.route() call for the known failing 16:00 case.

This script loads merged data via the existing project loader (main.py / __main__),
builds the network for the date used in earlier debugging (2026-02-28), constructs
Walking, creates RaptorRouter, and runs route(...) while profiling with cProfile.

It prints the top functions by cumulative time.
"""
import cProfile
import pstats
import io
import sys
import os
from datetime import datetime

# Make sure transport-backend is on sys.path
ROOT = os.path.dirname(__file__)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from main import initialize_base, build_for_date
from walking import Walking
from raptor_router import RaptorRouter

# Reuse the same coordinates and date from the repro
START_POINT = (54.063, -2.856)
DEST_POINT = (54.003, -2.783)
DATE_STR = '2026-02-28'
START_TIME = 16 * 3600  # 16:00 in seconds


def make_router():
    base = initialize_base()
    loader = base['loader']
    walking_raw = base['walking_raw']
    timetable, router, walking = build_for_date(loader, walking_raw, DATE_STR)
    return router, walking


def run_once():
    router, walking = make_router()
    res = router.route(
        n_transfer_limit=3,
        walking=walking,
        start_time=START_TIME,
        start_point=START_POINT,
        destination=DEST_POINT,
        allowed_modes=None,
    )
    return res


def main():
    # Build router/walking first (exclude build time from profiling)
    router, walking = make_router()

    pr = cProfile.Profile()
    pr.enable()
    result = router.route(
        n_transfer_limit=3,
        walking=walking,
        start_time=START_TIME,
        start_point=START_POINT,
        destination=DEST_POINT,
        allowed_modes=None,
    )
    pr.disable()

    s = io.StringIO()
    ps = pstats.Stats(pr, stream=s).strip_dirs().sort_stats('cumulative')
    ps.print_stats(40)
    print('\n==== route() result (summary) ====>')
    if isinstance(result, dict):
        meta = result.get('_meta')
        print('legs:', len([k for k in result.keys() if k != '_meta']))
        if meta:
            print('total_arrival:', meta.get('total_arrival'))
    else:
        print(result)
    print('\n==== Top 40 functions by cumulative time ====>')
    print(s.getvalue())

if __name__ == '__main__':
    main()
