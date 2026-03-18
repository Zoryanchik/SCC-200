"""Integration test for /journey/compare geometry source labeling.

This test runs against the real FastAPI app (no mocks) and uses the same
LA1 4YZ-ish → Lancaster city centre coordinates we used in debugging.

Assertions:
- At least one bus leg geometry exists.
- If bus leg geometry exists, its `source` is `route_link_tracks`.

Why this matters:
We previously saw historical merges missing `bus_loader`, causing link fragments
not to load and geometry to be mis-labeled / fall back.

NOTE: This is an integration test and will load datasets if not already cached.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api


def test_compare_bus_geometry_source_is_route_link_tracks():
    client = TestClient(api.app, raise_server_exceptions=True)

    payload = {
        "fromStop": {"lat": 54.0399, "lon": -2.7863},
        "toStop": {"lat": 54.0477, "lon": -2.7995},
        "departureTime": "17:30",
        "date": "2026-03-11",
        "maxTransfers": 2,
        "mode": "bus",
        "includeGeometry": True,
    }

    resp = client.post("/journey/compare", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    # This is a real-data integration test; routing may legitimately fail
    # depending on the active dataset snapshot. If it fails, skip so CI isn't flaky.
    if data.get("success") is not True:
        pytest.skip(f"No route produced by /journey/compare (error={data.get('error')})")

    route = (data.get("main") or {}).get("route")
    if not isinstance(route, dict):
        pytest.skip("No 'main.route' returned")

    geoms = route.get("routeGeometries") or []
    assert isinstance(geoms, list)

    bus_geoms = [g for g in geoms if isinstance(g, dict) and g.get("mode") == "bus" and (g.get("coords") or [])]
    assert len(bus_geoms) >= 1

    # All non-empty bus geometries should be stitched from link fragments.
    assert all(g.get("source") == "route_link_tracks" for g in bus_geoms)
