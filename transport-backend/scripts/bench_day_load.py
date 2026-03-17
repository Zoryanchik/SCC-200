"""Benchmark day timetable loads (UI date-jump scenario).

This script focuses on *loading timetables for specific dates* rather than
routing queries.

It exercises a common UX flow: load a date, jump +7 days, then jump back.

Environment knobs:
- DAY_LOAD_TIMING=1              Print step timings (bus/train load, merge, etc.)
- DAY_TIMETABLE_CACHE_DATES=8    LRU size for in-memory per-date BusData/TrainData

Usage:
  python3 scripts/bench_day_load.py 2026-03-17
"""

from __future__ import annotations

import os
import sys
import time
from datetime import date as _date, timedelta as _timedelta


# Allow running as `python3 scripts/bench_day_load.py ...` from any CWD.
BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)


def _plus_days(date_s: str, days: int) -> str:
    return (_date.fromisoformat(date_s) + _timedelta(days=days)).isoformat()


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: python3 scripts/bench_day_load.py YYYY-MM-DD")
        return 2

    date_s = argv[1]
    date_plus_7 = _plus_days(date_s, 7)

    # Encourage consistent output.
    os.environ.setdefault("DAY_LOAD_TIMING", "1")

    from main import initialize_base, build_for_date

    print(f"[bench_day_load] date={date_s} date+7={date_plus_7}")
    base = initialize_base()

    def _run(ds: str) -> float:
        t0 = time.perf_counter()
        build_for_date(
            base["loader"],
            base["walking_raw"],
            ds,
            mode="both",
            start_time=None,
            atco_loader=base.get("atco_loader"),
        )
        return time.perf_counter() - t0

    t_a = _run(date_s)
    t_b = _run(date_plus_7)
    t_c = _run(date_s)

    print("\n[bench_day_load] summary")
    print(f"  first  {date_s}: {t_a:.3f}s")
    print(f"  first  {date_plus_7}: {t_b:.3f}s")
    print(f"  repeat {date_s}: {t_c:.3f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
