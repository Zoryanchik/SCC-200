"""Debug helper: run _compute_delay_from_timetable with minimal stubs.

This is NOT part of production code; it's a local sanity tool.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import os
import sys
from types import SimpleNamespace

# Ensure parent directory is on sys.path so `import api` works when run as a script.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import api as m  # noqa: E402

# Turn on matcher debugging for this process.
os.environ.setdefault('MATCH_DEBUG', '1')


@dataclass
class W:
    stop_coords: dict[int, tuple[float, float]]

    def get_loc_coords(self, i: int):
        return self.stop_coords.get(i)

    def reachable_stops(self, latlon):
        return [(0, 0)]


class M:
    def __init__(self):
        self.journey_metadata = [
            {"line_name": "100", "operator_national_code": "SCCU", "journey_id": "J0"},
            {"line_name": "100", "operator_national_code": "SCCU", "journey_id": "J1"},
        ]
        self.journey_times = [
            [(0, 28800, 28800), (2, 31200, 31200)],
            [(0, 29400, 29400), (3, 31800, 31800)],
        ]
        self.journey_to_route = [0, 0]
        self.journey_stop_index = [{0: 0, 2: 1}, {0: 0, 3: 1}]
        self.legacy_polylines = [[(54.0, -2.8), (54.01, -2.79)]]
        self.route_stops = [[0, 2, 3]]
        self.stop_metadata = ["O", "X", "A", "B"]

    def get_atco_code(self, stop_int: int):
        return {0: "ATCO_ORIGIN", 2: "ATCO_DEST_A", 3: "ATCO_DEST_B"}.get(stop_int)


merged = M()
walking = W({0: (54.0, -2.8), 2: (54.001, -2.799), 3: (54.0015, -2.7985)})


def grfd(today, start_time=None, apply_delay=False):
    return merged, SimpleNamespace(), walking


m.get_router_for_date = grfd


class FD(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 3, 16, 10, 0, 0)


m.datetime = FD

print(
    m._compute_delay_from_timetable(
        "100",
        "",
        54.0,
        -2.8,
        return_jid=True,
        origin_dep_secs=29400,
        operator_ref="SCCU",
        strict_tol=600,
        feed_origin_atco="ATCO_ORIGIN",
        feed_destination_atco="ATCO_DEST_A",
        allow_offtrack=True,
    )
)
