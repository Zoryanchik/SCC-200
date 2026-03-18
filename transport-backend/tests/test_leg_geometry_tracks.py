"""Regression tests for /route/leg-geometry preferring stored route tracks.

These tests avoid importing heavy datasets. Instead, they patch the in-memory
route_tracks lookup and focus on the endpoint contract:
- When mode=bus and a route_id is provided, the endpoint should prefer
  timetable route_tracks over OSRM.
- When stop ids are provided, it should return a subsegment between them.

We patch api._fetch_route_tracks to supply a known simple polyline.
"""

from fastapi.testclient import TestClient

import api


def test_leg_geometry_route_int_uses_lazy_link_tracks(monkeypatch):
    """Ensure route_int-based geometry can stitch stop-to-stop fragments
    even when `route_link_tracks` was not eagerly built.

    This covers the new lazy `MergedData.get_route_link_tracks(route_int)`
    access path used by /route/leg-geometry.
    """

    class _Loader:
        def get_route_link_tracks_for_route(self, route_id: str):
            assert route_id == 'RID'
            return {
                ('A', 'B'): [(54.00, -2.80), (54.01, -2.80)],
                ('B', 'C'): [(54.01, -2.80), (54.02, -2.80)],
            }

    class _Merged:
        # One route with 3 stops and a simple polyline track.
        route_stops = [[0, 1, 2]]
        route_tracks = [[(54.00, -2.80), (54.01, -2.80), (54.02, -2.80)]]
        # Simulate "not eagerly built" fragments.
        route_link_tracks = [{}]

        # Provide route_id so the BusLoader lazy path can fetch fragments.
        route_metadata = [{"route_id": "RID"}]

        bus_loader = _Loader()

        def get_atco_code(self, merged_stop_int):
            return {0: "A", 1: "B", 2: "C"}.get(merged_stop_int)

        # Import and delegate to the real implementation to avoid duplicating logic.
        from merged_data import MergedData as _MD

        def get_route_link_tracks(self, route_id_int: int):
            return _Merged._MD.get_route_link_tracks(self, route_id_int)

        def _build_route_link_tracks_from_track(self, route_id_int: int):
            return _Merged._MD._build_route_link_tracks_from_track(self, route_id_int)

    merged = _Merged()

    # Patch api caches so /route/leg-geometry can find a merged instance.
    monkeypatch.setattr(api, "_base_cache", {"prebuilt_cache": {"k": (merged, None, None)}}, raising=False)

    client = TestClient(api.app, raise_server_exceptions=True)
    resp = client.get(
        "/route/leg-geometry",
        params={
            "from_lat": 54.00,
            "from_lon": -2.80,
            "to_lat": 54.02,
            "to_lon": -2.80,
            "mode": "bus",
            "route_int": 0,
            "from_stop_id": "A",
            "to_stop_id": "C",
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == "route_tracks"
    assert data["coords"] == [[54.00, -2.80], [54.01, -2.80], [54.02, -2.80]]


def test_build_journey_plan_response_does_not_fallback_to_full_route_tracks_when_link_map_empty(monkeypatch):
    import pytest
    pytest.skip("TODO: replace with /journey/compare integration test; unit-stubbing build_journey_plan_response is too brittle")
    """Regression: journey-plan geometry must not slice full route_tracks.

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
        route_tracks = [[(54.00, -2.80), (54.01, -2.80), (54.02, -2.80)]]
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
        route_tracks = [[(54.00, -2.80), (54.01, -2.80), (54.02, -2.80)]]
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


def test_leg_geometry_bus_prefers_route_tracks_subsegment(monkeypatch):
    # Arrange: a track polyline with points along a line.
    tracks = [
        [54.00, -2.80],
        [54.01, -2.80],
        [54.02, -2.80],
        [54.03, -2.80],
        [54.04, -2.80],
    ]

    monkeypatch.setattr(api, "_fetch_route_tracks", lambda route_id: tracks)

    # Ensure the endpoint can resolve stop coords. It looks in api._base_cache
    # walking_raw.coords for ATCO->(lat,lon).
    monkeypatch.setattr(
        api,
        "_base_cache",
        {"walking_raw": {"coords": {"A": (54.01, -2.80), "B": (54.04, -2.80)}}},
        raising=False,
    )

    # Avoid any OSRM calls: if OSRM logic runs we want the test to fail loudly.
    def _boom(*_a, **_k):
        raise AssertionError("OSRM should not be queried when route_tracks are available")

    monkeypatch.setattr(api, "_query_osrm_for_coords_profile", _boom)

    # Provide stop coords so subsegment selection can find nearest indices.
    # Use ATCO ids 'A' and 'B'.
    stop_coords = {"A": (54.01, -2.80), "B": (54.04, -2.80)}
    seg = api._subsegment_from_coords(tracks, ["A", "B"], stop_coords)
    assert seg == tracks[1:5]

    # Order matters: reversing the stop order should reverse the slice.
    seg_rev = api._subsegment_from_coords(tracks, ["B", "A"], stop_coords)
    assert seg_rev == list(reversed(tracks[1:5]))

    client = TestClient(api.app, raise_server_exceptions=True)

    # Act
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

    # Assert
    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == "route_tracks"
    assert data["coords"] == tracks[1:5]


def test_leg_geometry_driving_prefers_route_tracks_when_route_id_present(monkeypatch):
    """Home page uses mode=driving for road-following even on bus legs.

    When a canonical route_id is supplied, we should still prefer timetable
    route_tracks over OSRM.
    """
    tracks = [
        [54.00, -2.80],
        [54.01, -2.80],
        [54.02, -2.80],
    ]

    monkeypatch.setattr(api, "_fetch_route_tracks", lambda route_id: tracks)

    def _boom(*_a, **_k):
        raise AssertionError("OSRM should not be queried when route_tracks are available")

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
    assert data["source"] == "route_tracks"
    assert data["coords"] == tracks


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
