"""Regression tests for live bus track geometry stitching."""

import sys
from unittest.mock import MagicMock


# Mock heavy backend modules BEFORE importing api
_HEAVY_MODULES = [
    "bus_live", "bus_loader", "bus_data", "main", "time_utils",
    "timetable", "walking", "merged_data", "raptor_router",
    "dense_mapper", "train_data",
]
for mod in _HEAVY_MODULES:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

import api as api_module  # noqa: E402


class _FakeMerged:
    def __init__(self):
        # Circular-like route: same stop_int appears at start and end.
        self.route_stops = [[10, 20, 10]]

    def get_atco_code(self, stop_int):
        return {
            10: "ATCO_A",
            20: "ATCO_B",
        }.get(stop_int)

    def get_route_link_tracks(self, route_int):
        if int(route_int) != 0:
            return None
        return {
            (10, 20): [[54.0, -2.80], [54.01, -2.81]],
            (20, 10): [[54.01, -2.81], [54.02, -2.82]],
        }


def test_compute_vehicle_track_coords_allows_same_stop_int_when_positions_differ(monkeypatch):
    merged = _FakeMerged()

    # Ensure global cache discovery picks this merged instance.
    monkeypatch.setattr(
        api_module,
        "_base_cache",
        {"prebuilt_cache": {("2026-04-20", "PM"): (merged, None, None)}},
    )

    entry = {
        "route_int": 0,
        "origin_atco": "ATCO_A",
        "destination_atco": "ATCO_A",  # same ATCO at different route positions
        "operator_ref": "SCCU",
        "line": "6B",
        "vehicle_ref": "SCCU-11207",
    }

    coords = api_module._compute_vehicle_track_coords_for_live(entry)

    assert coords is not None
    assert len(coords) >= 3
    # Should stitch full loop segment A->B->A, not return None.
    assert coords[0] == [54.0, -2.80]
    assert coords[-1] == [54.02, -2.82]


def test_route_leg_geometry_stitches_loop_when_from_to_same_atco(monkeypatch):
    merged = _FakeMerged()

    # route_leg_geometry resolves merged via merged_key for live overlays.
    monkeypatch.setattr(
        api_module,
        "_get_router_for_merged_key",
        lambda _k, apply_delay=False: (merged, None, None),
    )

    res = api_module.route_leg_geometry(
        from_lat=54.068542,
        from_lon=-2.836265,
        to_lat=54.068642,
        to_lon=-2.836165,
        mode="driving",
        route_int=0,
        merged_key="2026-04-20|PM",
        from_stop_id="ATCO_A",
        to_stop_id="ATCO_A",
    )

    assert isinstance(res, dict)
    assert res.get("source") == "route_link_tracks"
    coords = res.get("coords") or []
    assert len(coords) >= 3
