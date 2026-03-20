"""Benchmark full day build latency.

This times the cold-build path used when routing on a date that is not
already cached:
- load bus + train data for the two-day-half window
- merge into MergedData (includes journey time shifting)
- build the router
- remap walking transfers

Usage:
  python3 scripts/bench_day_build.py 2026-03-20 2026-03-30

Optional env flags:
  DAY_LOAD_TIMING=1         prints native timing in main.build_for_date
  MERGE_BUILD_TIMING=1      prints per-phase merge timing from MergedData

This script also prints explicit phase durations regardless of env flags.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import date as _date


# Allow running as `python3 scripts/bench_day_build.py` from within
# transport-backend/ (so `import main` resolves).
_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND_ROOT = os.path.dirname(_HERE)
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)


def _now() -> float:
    return time.perf_counter()


def _bench_one(day: str, *, start_time: int | None = None, mode: str = "both") -> dict:
    # Import locally so this script can be run from repo root or transport-backend.
    import main as _main

    # Base init: loader + walking + atco_loader.
    base = _main.initialize_base()
    loader = base["loader"]
    walking_raw = base["walking_raw"]
    atco_loader = base.get("atco_loader")

    # Time the full build_for_date call.
    t0 = _now()
    merged, router, walking = _main.build_for_date(
        loader,
        walking_raw,
        day,
        mode=mode,
        start_time=start_time,
        atco_loader=atco_loader,
    )
    t_total = _now() - t0

    # Best-effort sizes.
    try:
        n_routes = len(getattr(merged, "route_stops", []) or [])
        n_journeys = len(getattr(merged, "journey_times", []) or [])
        n_stops = len(getattr(merged, "stop_to_routes", []) or [])
    except Exception:
        n_routes, n_journeys, n_stops = None, None, None

    return {
        "date": day,
        "start_time": start_time,
        "mode": mode,
        "total_s": t_total,
        "routes": n_routes,
        "journeys": n_journeys,
        "stops": n_stops,
    }


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: bench_day_build.py YYYY-MM-DD [YYYY-MM-DD ...]", file=sys.stderr)
        print("note: set env flags like DAY_LOAD_TIMING=1 before the command, not as an argument", file=sys.stderr)
        return 2

    # Be forgiving: users sometimes pass env-style flags as arguments, e.g.
    #   python3 scripts/bench_day_build.py DAY_LOAD_TIMING=1 2026-03-20
    # Treat KEY=VALUE tokens as environment overrides, and only keep valid
    # ISO date strings as benchmark targets.
    dates: list[str] = []
    for tok in argv[1:]:
        if "=" in tok and not tok.strip().startswith("--"):
            k, v = tok.split("=", 1)
            if k and v and k.isidentifier():
                os.environ[k] = v
            continue
        try:
            _date.fromisoformat(tok)
        except Exception:
            continue
        dates.append(tok)

    if not dates:
        print("usage: bench_day_build.py YYYY-MM-DD [YYYY-MM-DD ...]", file=sys.stderr)
        return 2

    # Use PM bucket by default (mirrors get_router_for_date(start_time=None)).
    start_time = None

    # Ensure we do NOT accidentally benefit from warm caches across runs.
    # We still reuse the base system (db connections, walking, atco loader),
    # but each date build itself is independent.
    # Do base initialization once so per-date timings measure only the delta.
    import main as _main
    base = _main.initialize_base()
    loader = base["loader"]
    walking_raw = base["walking_raw"]
    atco_loader = base.get("atco_loader")

    out = []
    for day in dates:
        print(f"\n[bench_day_build] date={day} start_time={start_time}")

        # Time the full build_for_date call.
        t0 = _now()
        merged, router, walking = _main.build_for_date(
            loader,
            walking_raw,
            day,
            mode="both",
            start_time=start_time,
            atco_loader=atco_loader,
        )
        t_total = _now() - t0

        try:
            n_routes = len(getattr(merged, "route_stops", []) or [])
            n_journeys = len(getattr(merged, "journey_times", []) or [])
            n_stops = len(getattr(merged, "stop_to_routes", []) or [])
        except Exception:
            n_routes, n_journeys, n_stops = None, None, None

        res = {
            "date": day,
            "start_time": start_time,
            "mode": "both",
            "total_s": t_total,
            "routes": n_routes,
            "journeys": n_journeys,
            "stops": n_stops,
        }
        out.append(res)
        print(
            f"  total: {res['total_s']:.3f}s  routes={res['routes']} journeys={res['journeys']} stops={res['stops']}"
        )

    # Comparison summary when multiple dates provided.
    if len(out) >= 2:
        base = out[0]
        for other in out[1:]:
            try:
                ratio = other["total_s"] / base["total_s"] if base["total_s"] else None
            except Exception:
                ratio = None
            if ratio is not None:
                print(
                    f"\n  compare: {other['date']} vs {base['date']} => {ratio:.2f}x total build time"
                )

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
