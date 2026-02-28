#!/usr/bin/env python3
"""Generate sample routing outputs by calling the response builder.

Produces JSON for a few canonical cases (empty, walking-only, multi-leg)
so we can compare before/after migration to ensure output parity.
"""
import json
import os
import sys
from unittest.mock import MagicMock

# Ensure transport-backend directory is on sys.path so `import api` works
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPT_DIR)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Mock heavy modules the same way tests do so importing `api` doesn't
# trigger expensive initialization (DB connections, downloads, etc.).
_HEAVY_MODULES = [
    "bus_live", "bus_loader", "bus_data", "main", "time_utils",
    "timetable", "walking", "merged_data", "raptor_router",
    "dense_mapper", "train_data",
]
for mod in _HEAVY_MODULES:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

# Provide a minimal time_utils implementation expected by api
sys.modules["time_utils"].seconds_since_midnight = MagicMock(return_value=36000)
sys.modules["time_utils"].seconds_to_time = MagicMock(
    side_effect=lambda s: f"{int(s)//3600:02d}:{(int(s)%3600)//60:02d}:{int(s)%60:02d}"
)

from api import build_journey_plan_response
from types import SimpleNamespace


def pretty(obj):
    print(json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False))


def walking_only_example():
    route = {
        "_meta": {
            "start_point": (54.05, -2.80),
            "destination": (54.07, -2.87),
            "start_walk_seconds": 300,
            "end_walk_seconds": 0,
            "total_arrival": 36300,
        }
    }
    res = build_journey_plan_response(route, SimpleNamespace(), {})
    return res


def multi_leg_example():
    route = {
        0: {
            "arrival_time": 36060,
            "prev_stop": None,
            "mode": None,
        },
        1: {
            "arrival_time": 36500,
            "prev_stop": 0,
            "mode": "bus",
            "journey_info": {"line_name": "NW:10"},
            "journey_origin": "Lancaster",
            "journey_destination": "Morecambe",
            "board_departure": 36100,
        },
        2: {
            "arrival_time": 36900,
            "prev_stop": 1,
            "mode": "walking",
        },
        "_meta": {
            "start_point": (54.05, -2.80),
            "destination": (54.08, -2.88),
            "start_walk_seconds": 60,
            "end_walk_seconds": 90,
            "total_arrival": 36990,
        },
    }
    # Create a minimal merged.today object with stop_metadata
    merged = SimpleNamespace()
    merged.stop_metadata = ["Stop A", "Stop B", "Stop C"]
    stop_coords = {
        0: (54.05, -2.79),
        1: (54.06, -2.83),
        2: (54.07, -2.87),
    }
    res = build_journey_plan_response(route, merged, stop_coords)
    return res


def empty_example():
    return build_journey_plan_response({}, SimpleNamespace(), {})


def main():
    print("--- EMPTY RESULT ---")
    pretty(empty_example())
    print("\n--- WALKING-ONLY RESULT ---")
    pretty(walking_only_example())
    print("\n--- MULTI-LEG RESULT ---")
    pretty(multi_leg_example())


if __name__ == "__main__":
    main()
