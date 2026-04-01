"""Regression: geom_context.route_int should not be null when journey_to_route exists.

We saw real routes where a later bus leg (after a walking transfer) had
geom_context.route_int = null, which prevents fragment stitching from
route_link_tracks and forces geometry fallback (often just 2 stop points).

This test ensures build_journey_plan_response:
- resolves route_int from merged.journey_to_route for bus legs
- records that value into leg.geom_context.route_int
- includes a stable merged_key derived from merged meta/bucket
"""

import api


def test_bus_leg_geom_context_includes_route_int_and_merged_key(monkeypatch):
    # Prevent OSRM network usage; we only care about context propagation.
    def _boom(*_a, **_k):
        raise AssertionError("OSRM should not be called")

    monkeypatch.setattr(api, "_query_osrm_for_coords_profile", _boom)

    import bus_loader
    if hasattr(bus_loader, "BusLoader"):
        monkeypatch.setattr(bus_loader, "BusLoader", lambda *a, **k: type("Stub", (), {"insert_logged_journey": lambda *a, **k: None})(), raising=False)

    monkeypatch.setattr(api, "_classification_for_merged", lambda x: {})

    # Minimal ATCO -> coords (not strictly required but keeps geometry clean)
    stops = {
        "A": (54.00, -2.80),
        "B": (54.01, -2.79),
    }

    class _AtcoStub:
        def get_all_stop_coords(self):
            return stops

    prev_base_cache = getattr(api, "_base_cache", None)
    api._base_cache = {"atco_loader": _AtcoStub()}

    class _Merged:
        stop_metadata = ["A stop", "B stop"]
        stop_classification = [None, None]
        route_stops = [[0, 1]]
        route_link_tracks = [{}]
        # Journey 0 maps to route_int 0
        journey_to_route = [0]
        meta = {"date": "2026-05-22"}
        bucket = "PM"

        def get_atco_code(self, idx):
            return {0: "A", 1: "B"}.get(idx)

        def get_route_link_tracks(self, _ri: int):
            return {}

    merged = _Merged()

    # Minimal route: 0 -> 1 by bus journey 0
    route_result = {
        "_meta": {"start_point": (54.00, -2.80), "destination": (54.01, -2.79)},
        1: {
            "prev_stop": 0,
            "arrival_time": 1000,
            "mode": "bus",
            "journey": 0,
            "board_departure": 900,
            "journey_info": {"line_name": "11"},
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

    ctx = bus_legs[0].get("geom_context") or {}
    assert isinstance(ctx, dict)
    assert ctx.get("route_int") == 0
    assert ctx.get("merged_key") == "2026-05-22|PM"
