from fastapi.testclient import TestClient


def test_leg_geometry_with_from_to_and_route_id_uses_non_legacy_sources(monkeypatch):
    """Regression: frontend sends from/to (epsilon hack) plus route_id.

    Full-route polylines are intentionally removed, so with this input the
    endpoint should only return fragment-based geometry (when available) or a
    routing fallback.
    """

    import api as api_module

    route_id = "MO88-TEST::PC0002407:505:RS4"

    params = {
        "mode": "driving",
        "route_id": route_id,
        # epsilon-ish from/to that should not matter when fragment geometry exists
        "from_lat": 51.5,
        "from_lon": -0.1,
        "to_lat": 51.50001,
        "to_lon": -0.10001,
    }

    with TestClient(api_module.app, raise_server_exceptions=True) as client:
        resp = client.get("/route/leg-geometry", params=params)
    assert resp.status_code == 200
    data = resp.json()

    # Policy: legacy full-route polylines are not used/returned.
    # Response should be one of the supported non-legacy sources.
    assert data.get("source") in {"route_link_tracks", "osrm", "linear"}
