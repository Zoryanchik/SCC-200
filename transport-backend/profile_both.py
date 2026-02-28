#!/usr/bin/env python3
"""Profile the 16:00 route using both compiled (.so) and pure-Python raptor_router.

This script prints top cumulative callers for each variant for quick comparison.
"""
import cProfile
import pstats
import io
import sys
import os
import importlib.util
import glob

ROOT = os.path.dirname(__file__)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from main import initialize_base, build_for_date
from walking import Walking

# Reuse the same coordinates and date from the repro
START_POINT = (54.063, -2.856)
DEST_POINT = (54.003, -2.783)
DATE_STR = '2026-02-28'
START_TIME = 16 * 3600  # 16:00 in seconds


def make_router_and_walking(mod):
    base = initialize_base()
    loader = base['loader']
    walking_raw = base['walking_raw']
    timetable, _router, walking = build_for_date(loader, walking_raw, DATE_STR)
    # create RaptorRouter instance from provided module/class
    router = mod.RaptorRouter(timetable.yesterday, timetable.today, timetable.tomorrow)
    return router, walking


def profile_variant(name, router, walking):
    print('\n===== Profiling:', name, '=====')
    pr = cProfile.Profile()
    pr.enable()
    res = router.route(
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
    print('\n==== result summary ====>')
    if isinstance(res, dict):
        meta = res.get('_meta')
        print('legs:', len([k for k in res.keys() if k != '_meta']))
        if meta:
            print('total_arrival:', meta.get('total_arrival'))
    else:
        print(res)
    print('\n==== Top callers (cumulative) ====>')
    print(s.getvalue())


# First: compiled .so if any
so_candidates = glob.glob(os.path.join(ROOT, 'raptor_router*.so'))
if so_candidates:
    so_path = so_candidates[0]
    print('Loading compiled extension from', so_path)
    if 'raptor_router' in sys.modules:
        del sys.modules['raptor_router']
    spec = importlib.util.spec_from_file_location('raptor_router', so_path)
    rmod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rmod)
    router_c, walking_c = make_router_and_walking(rmod)
    profile_variant('compiled (.so)', router_c, walking_c)
else:
    print('No compiled .so found; skipping compiled profiling')

# Then: pure-Python module loaded from file location (as raptor_router_py)
py_src = os.path.join(ROOT, 'raptor_router.py')
print('\nLoading pure-Python source from', py_src)
spec = importlib.util.spec_from_file_location('raptor_router_py', py_src)
py_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(py_mod)
router_p, walking_p = make_router_and_walking(py_mod)
profile_variant('pure-Python', router_p, walking_p)
