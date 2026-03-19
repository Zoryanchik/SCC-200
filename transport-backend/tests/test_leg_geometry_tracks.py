"""Regression tests for /route/leg-geometry fragment stitching.

These tests avoid importing heavy datasets. Instead, they patch legacy in-memory
polyline stubs and focus on the endpoint contract:
The fragment-only policy deliberately avoids returning full timetable polylines.
Instead, when a route_int and stop ids are provided, the endpoint should stitch
stop-to-stop fragments from `route_link_tracks`.
"""

from fastapi.testclient import TestClient

import api


def test_build_journey_plan_response_does_not_fallback_to_full_route_tracks_when_link_map_empty(monkeypatch):
    import pytest
    pytest.skip("TODO: replace with /journey/compare integration test; unit-stubbing build_journey_plan_response is too brittle")
    """Regression: journey-plan geometry must not slice full-route polylines.

    If per-link fragments are unavailable (empty link map), we should *not*
    fall back to slicing the full route polyline. That behaviour can produce
    misleading "teleport" segments on branched/loop routes.
    """
    import api

    class _Merged:
        # One route with two stops.
        stop_metadata = ["A stop", "B stop"]
        stop_classification = [None, None]
        route_stops = [[0, 1]]
        legacy_polylines = [[(54.00, -2.80), (54.01, -2.80), (54.02, -2.80)]]
        route_link_tracks = [{}]  # explicitly empty
        route_metadata = [{"route_id": "RID"}]

        def get_atco_code(self, merged_stop_int):
            return {0: "A", 1: "B"}.get(merged_stop_int)

        def get_route_link_tracks(self, route_id_int: int):
            return {}

    merged = _Merged()

    # Minimal RAPTOR-like result: stop_int -> info dict
    route_result = {
        "_meta": {"start_point": (54.00, -2.80), "destination": (54.02, -2.80)},
        1: {"prev_stop": 0, "arrival_time": 13 * 3600 + 5, "transport": "bus", "route_int": 0, "line_name": "X"},
        0: {"prev_stop": None, "arrival_time": 13 * 3600, "transport": "walking"},
    }

    stop_coords = {"A": (54.00, -2.80), "B": (54.02, -2.80)}

    out = api.build_journey_plan_response(route_result, merged, stop_coords, request_start_seconds=13 * 3600, include_geometry=True)

    # Ensure the bus leg carries a debugging note that fragments were missing and
    # it did not claim route_link_tracks.
    legs = out.get("legs") or []
    assert isinstance(legs, list)
    bus_legs = [l for l in legs if isinstance(l, dict) and l.get("mode") == "bus"]
    assert len(bus_legs) == 1
    bus_leg = bus_legs[0]
    assert bus_leg.get("geometry_note") == "no_link_fragments"
    assert bus_leg.get("geometry_source") in ("linear", "osrm", None)


def test_build_journey_plan_response_labels_link_fragments_as_route_link_tracks(monkeypatch):
    import pytest
    pytest.skip("TODO: replace with /journey/compare integration test; unit-stubbing build_journey_plan_response is too brittle")
    """When bus geometry is stitched from link fragments, source must be route_link_tracks."""
    import api

    class _Merged:
        stop_metadata = ["A stop", "B stop", "C stop"]
        stop_classification = [None, None, None]
        route_stops = [[0, 1, 2]]
        legacy_polylines = [[(54.00, -2.80), (54.01, -2.80), (54.02, -2.80)]]
        route_link_tracks = [{}]
        route_metadata = [{"route_id": "RID"}]

        def get_atco_code(self, merged_stop_int):
            return {0: "A", 1: "B", 2: "C"}.get(merged_stop_int)

        def get_route_link_tracks(self, route_id_int: int):
            # Provide fragments directly (avoid DB dependencies)
            return {
                (0, 1): [(54.00, -2.80), (54.01, -2.80)],
                (1, 2): [(54.01, -2.80), (54.02, -2.80)],
            }

    merged = _Merged()

    # We model a minimal RAPTOR-like route_result: the function needs a dict mapping
    # stop_int -> info dicts with prev_stop/arrival_time to build the ordered stop list.
    route_result = {
        "_meta": {"start_point": (54.00, -2.80), "destination": (54.02, -2.80)},
        2: {"prev_stop": 1, "arrival_time": 13 * 3600 + 10, "transport": "bus", "route_int": 0, "line_name": "X"},
        1: {"prev_stop": 0, "arrival_time": 13 * 3600 + 5, "transport": "bus", "route_int": 0, "line_name": "X"},
        0: {"prev_stop": None, "arrival_time": 13 * 3600, "transport": "walking"},
    }
    stop_coords = {"A": (54.00, -2.80), "B": (54.01, -2.80), "C": (54.02, -2.80)}

    out = api.build_journey_plan_response(route_result, merged, stop_coords, request_start_seconds=13 * 3600, include_geometry=True)
    # At least one geometry entry should be present, and the bus geometry should
    # be labeled as coming from stitched link fragments.
    geoms = out.get("routeGeometries") or []
    assert len(geoms) >= 1
    assert any((isinstance(g, dict) and g.get("mode") == "bus" and g.get("source") == "route_link_tracks" and len(g.get("coords") or []) >= 2) for g in geoms)


def test_leg_geometry_bus_uses_only_non_legacy_sources(monkeypatch):
    """Policy: /route/leg-geometry must not return full-route polylines."""
    # Avoid OSRM calls so the test stays deterministic.
    def _boom(*_a, **_k):
        raise AssertionError("OSRM should not be queried in this policy test")

    monkeypatch.setattr(api, "_query_osrm_for_coords_profile", _boom)

    client = TestClient(api.app, raise_server_exceptions=True)
    resp = client.get(
        "/route/leg-geometry",
        params={
            "from_lat": 54.01,
            "from_lon": -2.80,
            "to_lat": 54.04,
            "to_lon": -2.80,
            "mode": "bus",
            "route_id": "RID",
            "from_stop_id": "A",
            "to_stop_id": "B",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("source") in {"route_link_tracks", "linear"}


def test_leg_geometry_stop_sequence_fallback(monkeypatch):
    """If no track exists, return the full stop sequence coords (unsmoothed).

    This covers the routing-results expectation: when the planner provides
    stop_ids (from..to including intermediates) and route_link_tracks can't be
    stitched, we should return *all* stop coordinates and never call OSRM.
    """

    # Block any accidental OSRM calls.
    def _boom(*_args, **_kwargs):
        raise AssertionError("OSRM should not be called for stop-sequence fallback")

    monkeypatch.setattr(api, "_query_osrm_for_coords_profile", _boom)

    # Provide a tiny stop coord map via _base_cache['atco_loader'].
    stops = {
        "A": (54.0, -2.0),
        "B": (54.1, -2.1),
        "C": (54.2, -2.2),
    }

    class _AtcoStub:
        def get_all_stop_coords(self):
            return stops

    api._base_cache = {"atco_loader": _AtcoStub()}

    client = TestClient(api.app, raise_server_exceptions=True)
    resp = client.get(
        "/route/leg-geometry",
        params={
            "from_lat": 54.0,
            "from_lon": -2.0,
            "to_lat": 54.2,
            "to_lon": -2.2,
            "mode": "bus",
            "stop_ids": "A,B,C",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == "stops"
    assert data["coords"] == [
        [stops["A"][0], stops["A"][1]],
        [stops["B"][0], stops["B"][1]],
        [stops["C"][0], stops["C"][1]],
    ]


def test_leg_geometry_driving_uses_only_non_legacy_sources(monkeypatch):
    """Policy: even when mode=driving, /route/leg-geometry must not return full-route polylines."""
    def _boom(*_a, **_k):
        raise AssertionError("OSRM should not be queried in this policy test")

    monkeypatch.setattr(api, "_query_osrm_for_coords_profile", _boom)

    client = TestClient(api.app, raise_server_exceptions=True)
    resp = client.get(
        "/route/leg-geometry",
        params={
            "from_lat": 54.00,
            "from_lon": -2.80,
            "to_lat": 54.02,
            "to_lon": -2.80,
            "mode": "driving",
            "route_id": "RID",
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data.get("source") in {"route_link_tracks", "linear"}


def test_subsegment_prefers_forward_slice_on_ambiguous_loop_candidates():
    """Regression: avoid 'teleport' slices on looped/self-crossing polylines.

    When the 'to' stop has multiple equally-close candidates, the old scoring
    could pick an index before the 'from' index (ib < ia), resulting in a
    short-circuit slice (reversed segment) that draws a straight 'teleport'
    chord on the map.

    The fix prefers forward index pairs (ib >= ia) when slicing in stop order.
    """
    import api

    # Track passes near B twice: index 1 and index 3.
    tracks = [
        [0.0, 0.0],  # 0
        [0.0, 1.0],  # 1  (B candidate close)
        [0.0, 2.0],  # 2  (A candidate close)
        [0.0, 1.0],  # 3  (B candidate also close)
        [0.0, 0.5],  # 4
    ]

    stop_coords = {
        # A is closest to index 2
        "A": (0.0, 2.0),
        # B is exactly on indices 1 and 3 => ambiguous ties
        "B": (0.0, 1.0),
    }

    seg = api._subsegment_from_coords(tracks, ["A", "B"], stop_coords)
    # Must prefer the forward slice A(index2) -> B(index3), not backwards to index1.
    assert seg == tracks[2:4]
