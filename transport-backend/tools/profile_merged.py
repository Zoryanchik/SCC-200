#!/usr/bin/env python3
"""Profile MergedData construction using real DB-loaded Bus/Train data.

Produces `merged.prof` in the repo root (transport-backend/tools/merged.prof).

Adjust DAY_A / DAY_B as needed to target a date range present in your DB.
"""
import os
import sys
# Ensure repo transport-backend package dir is on sys.path when running
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from main import BUS_DB_PATH, TRAIN_DB_PATH
from bus_loader import BusLoader
from train_loader import TrainLoader
from merged_data import MergedData
import cProfile

# Pick dates known to exist in the DB; adjust if needed.
DAY_A = os.environ.get('PROFILE_DAY_A', '2026-02-15')
DAY_B = os.environ.get('PROFILE_DAY_B', '2026-02-16')
OFFSET_A = -86400
OFFSET_B = 0

OUT = os.path.join(os.path.dirname(__file__), 'merged.prof')


def build_merged():
    print('Using BUS_DB_PATH=', BUS_DB_PATH)
    loader = BusLoader(BUS_DB_PATH, walking_db_path=BUS_DB_PATH)
    train_loader = TrainLoader(TRAIN_DB_PATH)

    print('Loading bus/train data for', DAY_A, DAY_B)
    try:
        bus_a = loader.load_busdata_for_date(DAY_A)
        bus_b = loader.load_busdata_for_date(DAY_B)
        train_a = train_loader.load_traindata_for_date(DAY_A)
        train_b = train_loader.load_traindata_for_date(DAY_B)
    except Exception as e:
        print('DB load failed, falling back to synthetic sample data for profiling:', e)
        # Build small synthetic Data-like objects to exercise MergedData merge logic
        class DummyMapper:
            def __init__(self, n):
                self.n = n
            def get_code(self, local_i):
                return f"S{local_i}"

        class DummyData:
            def __init__(self, n_routes=50, n_journeys=200, n_stops=300):
                self.route_stops = [[(i + j) % n_stops for j in range(5)] for i in range(n_routes)]
                self.route_journeys = [[i * 4 + j for j in range(4)] for i in range(n_routes)]
                # journey_times: each journey is a list of (stop, atime, dtime)
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
                self.route_tracks = [[] for _ in range(n_routes)]
                self.map_stops = DummyMapper(n_stops)

        bus_a = DummyData()
        bus_b = DummyData()
        train_a = DummyData(n_routes=10, n_journeys=40, n_stops=100)
        train_b = DummyData(n_routes=10, n_journeys=40, n_stops=100)

    datasets = [
        (bus_a, OFFSET_A),
        (train_a, OFFSET_A),
        (bus_b, OFFSET_B),
        (train_b, OFFSET_B),
    ]

    print('Constructing MergedData...')
    m = MergedData(datasets, atco_loader=None, stop_name_fn=loader.get_stop_names_bulk)
    print('Merged: stops=', len(m.stop_to_routes), 'journeys=', len(m.journey_times))
    return m


if __name__ == '__main__':
    profiler = cProfile.Profile()
    profiler.runcall(build_merged)
    profiler.dump_stats(OUT)
    print('Wrote profile to', OUT)
