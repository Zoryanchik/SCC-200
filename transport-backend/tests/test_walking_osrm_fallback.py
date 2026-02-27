import json

import sys
import importlib
import urllib.request
from unittest.mock import MagicMock

# Ensure we import the real `walking` module even if earlier tests substituted a
# MagicMock into sys.modules for isolation. If a MagicMock exists, remove it
# and import the real module from the backend package directory (conftest
# already ensures the transport-backend directory is on sys.path).
if "walking" in sys.modules and isinstance(sys.modules["walking"], MagicMock):
    del sys.modules["walking"]
walking = importlib.import_module("walking")
Walking = walking.Walking


class FakeResp:
    def __init__(self, payload_bytes):
        self._payload = payload_bytes

    def read(self):
        return self._payload

    def close(self):
        return None


def test_walking_time_between_fallback_on_non_ok(monkeypatch):
    # Create two points a few meters apart
    a = (51.0, -0.1)
    b = (51.0005, -0.1005)

    w = Walking({}, {})

    # Replace urlopen to return a non-Ok JSON response
    payload = json.dumps({"code": "Invalid", "routes": [{"duration": 1.0}]})
    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: FakeResp(payload.encode("utf-8")))

    # Expect fallback to haversine-based integer seconds
    expected = int(w._haversine_m(a[0], a[1], b[0], b[1]) / 1.0)
    got = w.walking_time_between(a, b)
    assert got == expected


def test_reachable_stops_fallback_on_non_ok(monkeypatch):
    # One user location and two stops nearby
    user = (51.0, -0.1)
    stop_coords = {
        1: (51.0002, -0.1002),
        2: (51.005, -0.105),
    }

    w = Walking({}, stop_coords, max_walk_seconds=3600)

    # OSRM returns non-Ok and some durations — we should ignore that and fall back
    payload = json.dumps({"code": "Error", "durations": [[0, 60, 600]]})
    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: FakeResp(payload.encode("utf-8")))

    result = w.reachable_stops(user)

    # Both stops are within max_walk_seconds when using haversine fallback
    expected_keys = set(stop_coords.keys())
    assert set(result.keys()) == expected_keys
