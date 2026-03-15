"""Tests for /debug/route-tracks/{route_id}.

We keep these tests lightweight by patching api._fetch_route_tracks.
"""

from fastapi.testclient import TestClient

import api


def test_debug_route_tracks_found(monkeypatch):
    tracks = [
        [54.0, -2.8],
        [54.01, -2.81],
        [54.02, -2.82],
    ]
    monkeypatch.setattr(api, "_fetch_route_tracks", lambda route_id: tracks)

    client = TestClient(api.app, raise_server_exceptions=True)
    resp = client.get("/debug/route-tracks/RID")
    assert resp.status_code == 200
    data = resp.json()
    assert data["route_id"] == "RID"
    assert data["found"] is True
    assert data["coords_len"] == len(tracks)
    assert data["coords_sample"] == tracks[:5]


def test_debug_route_tracks_not_found(monkeypatch):
    monkeypatch.setattr(api, "_fetch_route_tracks", lambda route_id: [])

    # Also patch suggestions so the behavior is deterministic.
    monkeypatch.setattr(api, "_suggest_similar_route_ids", lambda route_id, limit=10: ["A", "B"])

    client = TestClient(api.app, raise_server_exceptions=True)
    resp = client.get("/debug/route-tracks/RID", params={"suggest": 2, "sample": 2})
    assert resp.status_code == 200
    data = resp.json()
    assert data["route_id"] == "RID"
    assert data["found"] is False
    assert data["coords_len"] == 0
    assert data["coords_sample"] == []
    assert data["suggestions"] == ["A", "B"]
