#!/usr/bin/env python3
"""Benchmark compiled vs pure-Python RaptorRouter.route() by running many queries.

Usage: run from repository root (SCC-200). Defaults: 50 runs per variant.
"""
import time
import statistics
import sys
import os
from datetime import datetime
import importlib.util
import glob

from importlib import import_module
import importlib.util

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

DEFAULT_RUNS = 50


def load_python_router():
    # load the pure-Python source as a different module name to avoid collision
    src = os.path.join(ROOT, 'raptor_router.py')
    spec = importlib.util.spec_from_file_location('raptor_router_py', src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_timetable_and_walking():
    base = initialize_base()
    loader = base['loader']
    walking_raw = base['walking_raw']
    timetable, _router, walking = build_for_date(loader, walking_raw, DATE_STR)
    return timetable, walking


def bench_variant(rclass, timetable, walking, runs=DEFAULT_RUNS, warmup=3):
    router = rclass(timetable.yesterday, timetable.today, timetable.tomorrow)
    # warmup
    for _ in range(warmup):
        _ = router.route(3, walking, START_TIME, START_POINT, DEST_POINT, None)
    times = []
    for i in range(runs):
        t0 = time.perf_counter()
        _ = router.route(3, walking, START_TIME, START_POINT, DEST_POINT, None)
        t1 = time.perf_counter()
        times.append(t1 - t0)
    return times


def print_stats(name, times):
    print(f"\n=== {name} ===")
    print(f"runs: {len(times)}")
    print(f"total: {sum(times):.6f}s")
    print(f"mean:  {statistics.mean(times):.6f}s")
    print(f"median:{statistics.median(times):.6f}s")
    print(f"min:   {min(times):.6f}s")
    print(f"max:   {max(times):.6f}s")


def main(runs=DEFAULT_RUNS):
    print('Building timetable and walking...')
    timetable, walking = make_timetable_and_walking()

    print('\nBenchmarking compiled extension (loaded from .so)')
    # Try to find a compiled extension (.so) in this directory
    so_candidates = glob.glob(os.path.join(ROOT, 'raptor_router*.so'))
    if not so_candidates:
        print('No compiled extension found; skipping compiled benchmark')
        compiled_times = []
    else:
        so_path = so_candidates[0]
        # load the compiled .so under the module name 'raptor_router'
        if 'raptor_router' in sys.modules:
            del sys.modules['raptor_router']
        spec = importlib.util.spec_from_file_location('raptor_router', so_path)
        rmod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(rmod)
        compiled_times = bench_variant(rmod.RaptorRouter, timetable, walking, runs=runs)
    print_stats('compiled raptor_router', compiled_times)

    print('\nBenchmarking pure-Python router (loaded from source)')
    pmod = load_python_router()
    python_times = bench_variant(pmod.RaptorRouter, timetable, walking, runs=runs)
    print_stats('pure-Python raptor_router', python_times)

    # summary comparison
    print('\n=== summary ===')
    print(f'compiled mean: {statistics.mean(compiled_times):.6f}s')
    print(f'python   mean: {statistics.mean(python_times):.6f}s')
    print(f'speedup (python/compiled): {statistics.mean(python_times)/statistics.mean(compiled_times):.2f}x')

if __name__ == '__main__':
    runs = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_RUNS
    main(runs)
