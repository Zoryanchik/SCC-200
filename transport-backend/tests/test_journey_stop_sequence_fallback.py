"""Regression: routing results should fall back to *stop sequence* coords.

When route_link_tracks are missing for a bus leg, we must not draw a misleading
2-point straight line between the boarding and alighting stops.

Instead, if we know the stop sequence for the route (route_int -> route_stops),
we should call /route/leg-geometry with stop_ids so it returns all stop coords
with source 'stops'.
"""

from unittest.mock import MagicMock

import api


def test_build_journey_plan_response_bus_no_fragments_uses_stop_sequence(monkeypatch):
    # Prevent any OSRM usage in this regression test.
    def _boom(*_a, **_k):
        raise AssertionError("OSRM should not be called in stop-sequence fallback")

    import bus_loader
    if hasattr(bus_loader, "BusLoader"):
        monkeypatch.setattr(bus_loader, "BusLoader", lambda *a, **k: type("Stub", (), {"insert_logged_journey": lambda *a, **k: None})(), raising=False)

    monkeypatch.setattr(api, "_classification_for_merged", lambda x: {})

    monkeypatch.setattr(api, "_query_osrm_for_coords_profile", _boom)

    # Provide ATCO -> (lat, lon)
    stops = {
        "A": (54.00, -2.80),
        "B": (54.01, -2.79),
        "C": (54.02, -2.78),
        # Extra stop on the route that is NOT part of either tested leg segment.
        "D": (54.50, -3.50),
    }

    class _AtcoStub:
        def get_all_stop_coords(self):
            return stops

    # Ensure the test is isolated from any global cache state created by
    # other tests (this suite shares the `api` module across tests).
    prev_base_cache = getattr(api, "_base_cache", None)
    api._base_cache = {"atco_loader": _AtcoStub()}

    class _Merged:
        stop_metadata = ["A stop", "B stop", "C stop", "D stop"]
        stop_classification = [None, None, None, None]
        # Route contains an extra stop (D) beyond the A->B->C journey.
        route_stops = [[0, 1, 2, 3]]
        route_link_tracks = [{}]  # explicitly empty => no fragments
        journey_to_route = [0]
        meta = {"date": "2026-03-19"}
        bucket = "AM"

        def get_atco_code(self, idx):
            return {0: "A", 1: "B", 2: "C", 3: "D"}.get(idx)

        def get_route_link_tracks(self, _ri: int):
            return {}

    merged = _Merged()

    # Minimal RAPTOR-like path: A -> B -> C with bus on segments.
    route_result = {
        "_meta": {"start_point": (54.00, -2.80), "destination": (54.02, -2.78)},
        2: {
            "prev_stop": 1,
            "arrival_time": 1000,
            "mode": "bus",
            "journey": 0,
            "board_departure": 900,
            "journey_info": {"line_name": "81"},
        },
        1: {
            "prev_stop": 0,
            "arrival_time": 900,
            "mode": "bus",
            "journey": 0,
            "board_departure": 800,
            "journey_info": {"line_name": "81"},
        },
        0: {"prev_stop": None, "arrival_time": 800, "mode": "walking"},
    }

    try:
        out = api.build_journey_plan_response(
            route_result,
            merged,
            stop_coords={},
            request_start_seconds=700,
            include_geometry=True,
        )
    finally:
        api._base_cache = prev_base_cache

    bus_legs = [l for l in (out.get("legs") or []) if isinstance(l, dict) and l.get("mode") == "bus"]
    assert bus_legs, "expected at least one bus leg"
    # Each bus leg should carry stop-derived fallback geometry AND it must
    # not include the extra route stop D.
    for bl in bus_legs:
        geom = bl.get("geometry") or {}
        if geom.get("source") != "stops":
            # Helpful debugging when this test fails only under full-suite runs.
            # Pytest will capture this output and show it in the failure.
            print("unexpected_geometry=", geom)
        assert geom.get("source") == "stops"
        coords = geom.get("coords") or []
        assert len(coords) >= 2
        # Ensure the unrelated stop (D) isn't present in the segment geometry.
        assert [stops["D"][0], stops["D"][1]] not in coords
