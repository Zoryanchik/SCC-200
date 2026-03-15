from fastapi.testclient import TestClient


def test_leg_geometry_prefers_route_tracks_with_from_to(monkeypatch):
    """Regression: frontend sends from/to (epsilon hack) plus route_id.

    We must still return the full in-memory route track when available,
    instead of falling back to a 2-point linear geometry.
    """

    import api as api_module

    route_id = "MO88-TEST::PC0002407:505:RS4"
    tracks = [[51.5, -0.1], [51.5005, -0.1005], [51.501, -0.101]]

    def _fake_fetch_route_tracks(rid: str):
        assert rid == route_id
        return tracks

    monkeypatch.setattr(api_module, "_fetch_route_tracks", _fake_fetch_route_tracks)

    params = {
        "mode": "driving",
        "route_id": route_id,
        # epsilon-ish from/to that should not matter when route_tracks exist
        "from_lat": 51.5,
        "from_lon": -0.1,
        "to_lat": 51.50001,
        "to_lon": -0.10001,
    }

    with TestClient(api_module.app, raise_server_exceptions=True) as client:
        resp = client.get("/route/leg-geometry", params=params)
    assert resp.status_code == 200
    data = resp.json()

    assert data["source"] == "route_tracks"
    assert data["coords"] == tracks
