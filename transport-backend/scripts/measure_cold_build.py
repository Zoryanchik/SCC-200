#!/usr/bin/env python3
"""Measure a cold build_for_date() and capture per-phase timing output.

Usage:
  python3 scripts/measure_cold_build.py YYYY-MM-DD [--repeat N]

The script will:
- enable DAY_LOAD_TIMING and MERGE_BUILD_TIMING
- call initialize_base() to set up loaders/walking
- clear in-process per-date caches to force fresh DB loads
- run build_for_date() once (or N times) and capture stdout
- save a timestamped log under transport-backend/.cold_build_logs/

This is a diagnostic helper for profiling first-request / new-day latency.
"""
from __future__ import annotations

import os
import sys
import time
import io
import errno
from datetime import datetime
from contextlib import redirect_stdout


def ensure_log_dir(path: str):
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as e:
        if e.errno != errno.EEXIST:
            raise


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: python3 scripts/measure_cold_build.py YYYY-MM-DD [--repeat N]")
        return 2

    date_s = argv[1]
    repeat = 1
    if len(argv) >= 4 and argv[2] == '--repeat':
        try:
            repeat = int(argv[3])
            repeat = max(1, repeat)
        except Exception:
            repeat = 1

    # Enable timing knobs
    os.environ.setdefault('DAY_LOAD_TIMING', '1')
    os.environ.setdefault('MERGE_BUILD_TIMING', '1')

    # Import heavy app pieces after env knobs are set
    BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if BACKEND_ROOT not in sys.path:
        sys.path.insert(0, BACKEND_ROOT)

    from main import initialize_base, build_for_date, _busdata_cache_lock, _busdata_cache, _traindata_cache_lock, _traindata_cache

    # Do base init (this may itself do some prebuild work). We still
    # clear per-date caches below to force fresh per-date loads.
    print(f"Initializing base (this may take a few seconds)...")
    base = initialize_base()

    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.cold_build_logs')
    log_dir = os.path.normpath(log_dir)
    ensure_log_dir(log_dir)

    results = []
    for i in range(repeat):
        # Clear per-date in-memory LRU caches to force DB work on the next build
        try:
            with _busdata_cache_lock:
                _busdata_cache.clear()
        except Exception:
            pass
        try:
            with _traindata_cache_lock:
                _traindata_cache.clear()
        except Exception:
            pass

        buf = io.StringIO()
        t0 = time.perf_counter()
        try:
            with redirect_stdout(buf):
                merged, router, walking = build_for_date(
                    base['loader'],
                    base['walking_raw'],
                    date_s,
                    mode='both',
                    start_time=None,
                    atco_loader=base.get('atco_loader'),
                )
        except Exception as e:
            elapsed = time.perf_counter() - t0
            out = buf.getvalue()
            out += f"\n[measure_cold_build] ERROR during build: {e}\n"
            out += f"[measure_cold_build] elapsed: {elapsed:.3f}s\n"
            ts = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
            path = os.path.join(log_dir, f"cold_build_{date_s}_{ts}_{i}.log")
            with open(path, 'w') as f:
                f.write(out)
            print(f"Build failed; log written to: {path}")
            return 1

        elapsed = time.perf_counter() - t0
        out = buf.getvalue()
        out += f"\n[measure_cold_build] total_elapsed: {elapsed:.3f}s\n"
        ts = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
        path = os.path.join(log_dir, f"cold_build_{date_s}_{ts}_{i}.log")
        with open(path, 'w') as f:
            f.write(out)
        print(f"Run {i+1}/{repeat}: cold build complete in {elapsed:.3f}s — log: {path}")
        results.append({'run': i + 1, 'elapsed_s': elapsed, 'log': path})

    # Summary
    print('\nSummary:')
    for r in results:
        print(f"  run {r['run']}: {r['elapsed_s']:.3f}s — {r['log']}")

    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
