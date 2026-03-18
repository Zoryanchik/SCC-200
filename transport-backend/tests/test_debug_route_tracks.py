"""Tests for debug geometry endpoints.

`/debug/route-tracks/{route_id}` is deprecated (legacy full-route polylines removed) and should
return HTTP 410.

`/debug/route-link-tracks/{route_id}` is the fragment-based replacement.
"""

from fastapi.testclient import TestClient

import api


def test_debug_route_tracks_is_gone(monkeypatch):
    client = TestClient(api.app, raise_server_exceptions=True)
    resp = client.get("/debug/route-tracks/RID")
    assert resp.status_code == 410


def test_debug_route_link_tracks_found(monkeypatch):
    class _FakeMerged:
        route_metadata = [
            {
                'route_id': 'RID',
                'route_int': 0,
            }
        ]

        def get_route_link_tracks(self, route_int=None):
            assert route_int == 0
            # Key is (from_atco, to_atco) -> list of fragments
            return {
                ('A', 'B'): [
                    [[54.0, -2.8], [54.01, -2.81], [54.02, -2.82]],
                ]
            }

    # Provide a minimal prebuilt cache so the endpoint can discover merged.
    monkeypatch.setattr(api, '_base_cache', {'prebuilt_cache': {'x': (_FakeMerged(), None, None)}}, raising=False)

    client = TestClient(api.app, raise_server_exceptions=True)
    resp = client.get('/debug/route-link-tracks/RID', params={'sample': 2, 'suggest': 0})
    assert resp.status_code == 200
    data = resp.json()
    assert data['route_id'] == 'RID'
    assert data['found'] is True
    assert data['link_count'] == 1
    assert data['route_int'] == 0
    assert data['sample_links'] == [['A', 'B']]
    assert len(data['sample_fragment']) >= 2
