"""Benchmark + correctness check for MergedData merge performance.

This script builds merged networks twice:
  1) default single-process merge
  2) optional parallel shift merge (MERGE_PARALLEL_SHIFT=1)

It prints merge timings and performs a lightweight correctness check by
comparing key structural sizes and a few sampled entries.

Usage (optional):
  python3 scripts/bench_merge_data.py 2026-03-17

Notes:
- This is intended for local benchmarking. It will load bus/train data for
  the specified date and adjacent dates, so it can take time and memory.
"""

from __future__ import annotations

import os
import pathlib
import random
import sys
import time


# Ensure project root (transport-backend/) is importable when running from scripts/
_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _now() -> float:
    return time.perf_counter()


def _sample(seq, k=5):
    if not seq:
        return []
    # Deterministic sampling: stable across runs/modes.
    if len(seq) <= k:
        return list(range(len(seq)))
    step = max(1, len(seq) // k)
    idxs = list(range(0, len(seq), step))[:k]
    # ensure last element included when possible
    if idxs and idxs[-1] != len(seq) - 1:
        idxs[-1] = len(seq) - 1
    return idxs


def _fingerprint(merged):
    """Create a lightweight fingerprint for sanity checking."""
    fp = {
        "routes": len(merged.route_stops),
        "journeys": len(merged.journey_times),
        "stops": len(merged.stop_to_routes),
        "route_tracks": len(getattr(merged, "route_tracks", []) or []),
        "route_link_tracks": len(getattr(merged, "route_link_tracks", []) or []),
    }

    # sample a few journeys to catch offset mistakes
    j_idxs = _sample(merged.journey_times, k=8)
    j_samp = []
    for j in j_idxs:
        jt = merged.journey_times[j]
        if jt:
            j_samp.append((j, jt[0], jt[-1], len(jt)))
        else:
            j_samp.append((j, None, None, 0))
    fp["journey_samples"] = j_samp

    # sample a few routes
    r_idxs = _sample(merged.route_stops, k=8)
    r_samp = []
    for r in r_idxs:
        rs = merged.route_stops[r]
        r_samp.append((r, rs[0] if rs else None, rs[-1] if rs else None, len(rs)))
    fp["route_samples"] = r_samp

    return fp


def build_for_date(date_s: str, *, parallel_shift: bool):
    # Force env var read inside merged_data
    os.environ["MERGE_PARALLEL_SHIFT"] = "1" if parallel_shift else "0"

    # Imports here so env var is set before module init
    from atco_loader import AtcoLoader
    from bus_loader import BusLoader
    from train_loader import TrainLoader
    from merged_data import MergedData

    # Mirror transport-backend/main.py defaults so this script works
    # out-of-the-box in the same environment.
    DEFAULT_PG = "postgresql://pguser:pgpass@127.0.0.1:5011/transport"
    BUS_DB_PATH = os.environ.get("BUS_DB_DSN") or DEFAULT_PG
    TRAIN_DB_PATH = os.environ.get("TRAIN_DB_DSN") or DEFAULT_PG
    WALK_DB_PATH = os.environ.get("WALK_DB_DSN") or DEFAULT_PG

    loader = BusLoader(BUS_DB_PATH, walking_db_path=WALK_DB_PATH)
    train_loader = TrainLoader(TRAIN_DB_PATH)
    atco_loader = AtcoLoader(WALK_DB_PATH)

    # adjacent dates
    import datetime as _dt

    d = _dt.date.fromisoformat(date_s)
    yesterday = (d - _dt.timedelta(days=1)).isoformat()
    tomorrow = (d + _dt.timedelta(days=1)).isoformat()

    # Load (not benchmarked here; we focus on merge step)
    bus_y = loader.load_busdata_for_date(yesterday)
    bus_t = loader.load_busdata_for_date(date_s)
    bus_tm = loader.load_busdata_for_date(tomorrow)
    train_y = train_loader.load_traindata_for_date(yesterday)
    train_t = train_loader.load_traindata_for_date(date_s)
    train_tm = train_loader.load_traindata_for_date(tomorrow)

    datasets = [
        (bus_y, -86400),
        (train_y, -86400),
        (bus_t, 0),
        (train_t, 0),
        (bus_tm, 86400),
        (train_tm, 86400),
    ]

    t0 = _now()
    merged = MergedData(datasets, atco_loader=atco_loader, stop_name_fn=loader.get_stop_names_bulk)
    dt = _now() - t0

    return merged, dt


def main():
    date_s = sys.argv[1] if len(sys.argv) > 1 else time.strftime("%Y-%m-%d")

    print(f"[bench_merge_data] date={date_s}")

    m1, t1 = build_for_date(date_s, parallel_shift=False)
    fp1 = _fingerprint(m1)
    print(f"single-process merge: {t1:.3f}s  fp.routes={fp1['routes']} fp.journeys={fp1['journeys']} fp.stops={fp1['stops']}")

    m2, t2 = build_for_date(date_s, parallel_shift=True)
    fp2 = _fingerprint(m2)
    print(f"parallel shift merge: {t2:.3f}s  fp.routes={fp2['routes']} fp.journeys={fp2['journeys']} fp.stops={fp2['stops']}")

    ok = (fp1 == fp2)
    print(f"fingerprint match: {ok}")
    if not ok:
        # Print a minimal diff hint
        for k in fp1.keys():
            if fp1[k] != fp2.get(k):
                print(f"diff {k}:\n  single={fp1[k]}\n  parallel={fp2.get(k)}")
                break


if __name__ == "__main__":
    main()
