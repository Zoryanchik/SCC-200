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
