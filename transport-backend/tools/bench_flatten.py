#!/usr/bin/env python3
"""Benchmark MergedData construction with and without flattened route->journey arrays.

This reuses the dataset-loading logic from `profile_merged.py`. Run from
`transport-backend/` (the script will add the parent directory to sys.path).
"""
import os
import sys
import time
import gc
import argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from main import BUS_DB_PATH, TRAIN_DB_PATH
from bus_loader import BusLoader
from train_loader import TrainLoader
from merged_data import MergedData

DAY_A = os.environ.get('PROFILE_DAY_A', '2026-02-15')
DAY_B = os.environ.get('PROFILE_DAY_B', '2026-02-16')
OFFSET_A = -86400
OFFSET_B = 0


def make_dummy_data(n_routes=50, n_journeys=200, n_stops=300):
    class DummyMapper:
        def __init__(self, n):
            self.n = n
        def get_code(self, local_i):
            return f"S{local_i}"

    class DummyData:
        def __init__(self, n_routes=n_routes, n_journeys=n_journeys, n_stops=n_stops):
            self.route_stops = [[(i + j) % n_stops for j in range(5)] for i in range(n_routes)]
            self.route_journeys = [[i * 4 + j for j in range(4)] for i in range(n_routes)]
            self.journey_times = []
            for j in range(n_journeys):
                jt = []
                base = j * 60
                for s in range(5):
                    sid = (j * 3 + s) % n_stops
                    at = base + s * 300
                    dt = at + 60
                    jt.append((sid, at, dt))
                self.journey_times.append(jt)
            self.stop_to_routes = [[i % n_routes] for i in range(n_stops)]
            self.journey_to_route = [i % n_routes for i in range(n_journeys)]
            self.route_metadata = [{} for _ in range(n_routes)]
            self.journey_metadata = [{} for _ in range(n_journeys)]
            # Legacy full-route polylines were removed; keep a placeholder attribute off by default.
            self.map_stops = DummyMapper(n_stops)

    return DummyData()


def load_datasets(use_synthetic=False):
    if use_synthetic:
        bus_a = make_dummy_data()
        bus_b = make_dummy_data()
        train_a = make_dummy_data(n_routes=10, n_journeys=40, n_stops=100)
        train_b = make_dummy_data(n_routes=10, n_journeys=40, n_stops=100)
        loader = None
    else:
        loader = BusLoader(BUS_DB_PATH, walking_db_path=BUS_DB_PATH)
        train_loader = TrainLoader(TRAIN_DB_PATH)
        try:
            bus_a = loader.load_busdata_for_date(DAY_A)
            bus_b = loader.load_busdata_for_date(DAY_B)
            train_a = train_loader.load_traindata_for_date(DAY_A)
            train_b = train_loader.load_traindata_for_date(DAY_B)
        except Exception as e:
            print('DB load failed, falling back to synthetic sample data for benchmarking:', e)
            return load_datasets(use_synthetic=True)

    datasets = [
        (bus_a, OFFSET_A),
        (train_a, OFFSET_A),
        (bus_b, OFFSET_B),
        (train_b, OFFSET_B),
    ]
    return datasets, loader


def time_build(datasets, stop_name_fn, build_flatten, iterations=20):
    times = []
    for i in range(iterations):
        gc.collect()
        start = time.perf_counter()
        m = MergedData(datasets, atco_loader=None, stop_name_fn=stop_name_fn, build_flatten=build_flatten)
        end = time.perf_counter()
        times.append(end - start)
        # small sanity check
        print(f'Iter {i+1}/{iterations} build_flatten={build_flatten}: stops={len(m.stop_to_routes)} journeys={len(m.journey_times)} time={times[-1]:.4f}s')
    return times


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--synthetic', action='store_true', help='Use synthetic deterministic datasets (no DB)')
    parser.add_argument('--iterations', '-n', type=int, default=20, help='Iterations per measurement')
    args = parser.parse_args()

    datasets, loader = load_datasets(use_synthetic=args.synthetic)
    print('Warmup (with flatten)')
    # loader may be None in synthetic mode — provide a noop stop_name_fn
    stop_name_fn = (loader.get_stop_names_bulk if loader else (lambda codes: {}))
    _ = MergedData(datasets, atco_loader=None, stop_name_fn=stop_name_fn, build_flatten=True)

    print('\nBenchmarking without flattened arrays...')
    times_no = time_build(datasets, stop_name_fn, build_flatten=False, iterations=args.iterations)

    print('\nBenchmarking with flattened arrays...')
    times_yes = time_build(datasets, stop_name_fn, build_flatten=True, iterations=args.iterations)

    def stats(arr):
        return {'min': min(arr), 'avg': sum(arr)/len(arr), 'max': max(arr)}

    s_no = stats(times_no)
    s_yes = stats(times_yes)

    print('\nResults:')
    print('Without flattened arrays:  min={min:.4f}s avg={avg:.4f}s max={max:.4f}s'.format(**s_no))
    print('With    flattened arrays:  min={min:.4f}s avg={avg:.4f}s max={max:.4f}s'.format(**s_yes))
    improvement = (s_no['avg'] - s_yes['avg']) / s_no['avg'] * 100.0 if s_no['avg'] > 0 else 0.0
    print(f'Average improvement: {improvement:.2f}%')
