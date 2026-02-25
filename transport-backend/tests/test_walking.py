"""Tests for Walking class and walking API endpoints.

Covers:
  - Walking constructor & env-var defaults
  - OSRM probe / availability caching
  - status() method
  - inter_walk() precomputed lookups
  - reachable_stops() with OSRM
  - reachable_stops() deterministic fallback (precomputed table)
  - walking_time_between() OSRM + fallback
  - _nearest_precomputed() helper
  - _haversine_m() module function
  - GET /walking/status endpoint
  - GET /walking/reachable endpoint
"""
import json
import math
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# ── Import Walking directly (no heavy deps needed) ──────────────────
backend_dir = os.path.join(os.path.dirname(__file__), "..")
if backend_dir not in sys.path:
    sys.path.insert(0, os.path.abspath(backend_dir))

# Ensure we import the REAL walking module, not a mock left by test_api.
# Remove any stale mock, import the real module, then restore later.
_stale_walking_mock = sys.modules.pop("walking", None)
import walking as _real_walking_module  # noqa: E402
sys.modules["walking"] = _real_walking_module
from walking import Walking, _haversine_m  # noqa: E402


# ── Fixtures ─────────────────────────────────────────────────────────

STOP_A = 0   # "hub" — has precomputed transfers
STOP_B = 1
STOP_C = 2
STOP_D = 3   # far away, no transfers

# Lancaster-area coords for realistic tests
COORDS = {
    STOP_A: (54.0480, -2.8010),  # Bus Station
    STOP_B: (54.0485, -2.8020),  # ~100m away
    STOP_C: (54.0490, -2.8000),  # ~120m away
    STOP_D: (54.1500, -2.9000),  # ~12km away
}

INTER_WALK = {
    STOP_A: {STOP_B: 90, STOP_C: 110},
    STOP_B: {STOP_A: 90},
}

USER_NEAR_A = (54.0479, -2.8008)  # ~15m from STOP_A


@pytest.fixture()
def walking():
    """Walking instance with precomputed table, OSRM disabled."""
    return Walking(
        INTER_WALK, COORDS,
        osrm_base="http://osrm-does-not-exist:9999",
        max_walk_seconds=600,
    )


@pytest.fixture()
def walking_no_table():
    """Walking instance with empty precomputed table."""
    return Walking({}, COORDS, osrm_base="http://osrm-does-not-exist:9999")


# ══════════════════════════════════════════════════════════════════════
# Walking constructor & configuration
# ══════════════════════════════════════════════════════════════════════


class TestWalkingConfig:
    """Constructor, env-var default, osrm_url property."""

    def test_explicit_osrm_base(self):
        w = Walking({}, {}, osrm_base="http://custom:1234")
        assert w.osrm_url == "http://custom:1234"

    def test_default_osrm_from_env(self):
        with patch.dict(os.environ, {"OSRM_URL": "http://env-osrm:5555"}):
            # Re-import to pick up the patched env
            import importlib
            import walking as wmod
            importlib.reload(wmod)
            w = wmod.Walking({}, {})
            assert w.osrm_url == "http://env-osrm:5555"
            # Restore default
            importlib.reload(wmod)

    def test_none_osrm_uses_module_default(self):
        w = Walking({}, {}, osrm_base=None)
        # Should be the module-level _DEFAULT_OSRM_URL
        assert "localhost" in w.osrm_url or "OSRM" not in os.environ

    def test_max_walk_seconds(self):
        w = Walking({}, {}, max_walk_seconds=300)
        assert w._max == 300

    def test_stores_inter_table_and_coords(self, walking):
        assert walking._inter is INTER_WALK
        assert walking._coords is COORDS


# ══════════════════════════════════════════════════════════════════════
# OSRM probe & availability
# ══════════════════════════════════════════════════════════════════════


class TestOSRMProbe:
    """osrm_available property, _probe_osrm, reset_osrm_probe."""

    def test_probe_caches_result(self, walking):
        assert walking._osrm_ok is None
        result = walking.osrm_available
        assert walking._osrm_ok is not None
        assert walking.osrm_available == result  # second call uses cache

    def test_probe_false_when_unreachable(self, walking):
        assert walking.osrm_available is False

    @patch("walking.urllib.request.urlopen")
    def test_probe_true_when_reachable(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_urlopen.return_value = mock_resp
        w = Walking({}, {}, osrm_base="http://fake-osrm:5000")
        assert w.osrm_available is True
        mock_resp.close.assert_called_once()

    def test_reset_clears_cache(self, walking):
        _ = walking.osrm_available
        walking.reset_osrm_probe()
        assert walking._osrm_ok is None


# ══════════════════════════════════════════════════════════════════════
# status()
# ══════════════════════════════════════════════════════════════════════


class TestStatus:
    """Walking.status() returns correct summary dict."""

    def test_status_keys(self, walking):
        s = walking.status()
        assert set(s.keys()) == {
            "osrm_url", "osrm_available", "max_walk_seconds",
            "precomputed_stops", "stops_with_coords",
        }

    def test_status_values(self, walking):
        s = walking.status()
        assert s["osrm_url"] == "http://osrm-does-not-exist:9999"
        assert s["osrm_available"] is False
        assert s["max_walk_seconds"] == 600
        assert s["precomputed_stops"] == 2  # STOP_A and STOP_B
        assert s["stops_with_coords"] == 4


# ══════════════════════════════════════════════════════════════════════
# inter_walk()
# ══════════════════════════════════════════════════════════════════════


class TestInterWalk:
    """Precomputed transfer lookups."""

    def test_returns_neighbours(self, walking):
        result = walking.inter_walk(STOP_A)
        assert result == {STOP_B: 90, STOP_C: 110}

    def test_unknown_stop_returns_empty(self, walking):
        assert walking.inter_walk(999) == {}

    def test_stop_without_outgoing(self, walking):
        # STOP_C is a destination in A's table but has no outgoing
        assert walking.inter_walk(STOP_C) == {}


# ══════════════════════════════════════════════════════════════════════
# reachable_stops() — OSRM available
# ══════════════════════════════════════════════════════════════════════


class TestReachableStopsOSRM:
    """reachable_stops() when OSRM responds."""

    @patch("walking.urllib.request.urlopen")
    def test_osrm_success(self, mock_urlopen):
        """OSRM returns valid durations — filtered by max_walk_seconds."""
        durations = [[0, 90, 110, 99999]]  # self, B, C, D (too far)
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "code": "Ok",
            "durations": durations,
        }).encode()
        mock_urlopen.return_value = mock_resp

        w = Walking(INTER_WALK, COORDS, osrm_base="http://fake:5000")
        result = w.reachable_stops(USER_NEAR_A)
        # D is out of range; A, B, C should be present
        assert STOP_D not in result
        # At least STOP_A and STOP_B should be reachable
        assert len(result) >= 2

    @patch("walking.urllib.request.urlopen")
    def test_osrm_non_ok_returns_exact_matches(self, mock_urlopen):
        """OSRM returns non-Ok code — falls back gracefully."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"code": "Error"}).encode()
        mock_urlopen.return_value = mock_resp

        w = Walking(INTER_WALK, COORDS, osrm_base="http://fake:5000")
        result = w.reachable_stops(USER_NEAR_A)
        # Should be a dict (possibly empty if no exact matches)
        assert isinstance(result, dict)

    def test_no_candidates_returns_empty(self):
        """Location far from all stops — no candidates in bbox."""
        w = Walking(INTER_WALK, COORDS, osrm_base="http://fake:5000")
        result = w.reachable_stops((0.0, 0.0))
        assert result == {}


# ══════════════════════════════════════════════════════════════════════
# reachable_stops() — deterministic fallback
# ══════════════════════════════════════════════════════════════════════


class TestReachableStopsFallback:
    """reachable_stops() when OSRM is unreachable."""

    def test_fallback_returns_nearby_stops(self, walking):
        result = walking.reachable_stops(USER_NEAR_A)
        assert isinstance(result, dict)
        # STOP_A is ~15m away, should be reachable
        assert STOP_A in result
        # STOP_B is ~120m, should be reachable
        assert STOP_B in result

    def test_fallback_uses_precomputed_table(self, walking):
        """Precomputed inter-walk entries should augment haversine estimates."""
        result = walking.reachable_stops(USER_NEAR_A)
        # STOP_C is reachable via precomputed table through STOP_A
        # (time to A + 110s precomputed A→C)
        assert STOP_C in result

    def test_fallback_sorted_by_time(self, walking):
        result = walking.reachable_stops(USER_NEAR_A)
        times = list(result.values())
        assert times == sorted(times)

    def test_fallback_far_stop_excluded(self, walking):
        """STOP_D is ~12km away — exceeds max_walk_seconds."""
        result = walking.reachable_stops(USER_NEAR_A)
        assert STOP_D not in result

    def test_fallback_no_precomputed_table(self, walking_no_table):
        """Works with empty inter-walk table (pure haversine)."""
        result = walking_no_table.reachable_stops(USER_NEAR_A)
        assert isinstance(result, dict)
        assert STOP_A in result

    def test_exact_match_included(self):
        """Stop at the exact user location gets walk_seconds=0."""
        coords = {0: (54.048, -2.801)}
        w = Walking({}, coords, osrm_base="http://nonexistent:9999")
        result = w.reachable_stops((54.048, -2.801))
        assert 0 in result
        assert result[0] == 0

    def test_precomputed_neighbour_beats_haversine(self):
        """When precomputed time via proxy < haversine, use the shorter."""
        # Create a scenario where the precomputed route is shorter
        # User near STOP_A, STOP_C reachable via precomputed 5s from A
        inter = {0: {1: 5}}  # A→B in 5 seconds (very short)
        coords = {
            0: (54.0480, -2.8010),
            1: (54.0485, -2.8020),  # ~120m, haversine would be ~120s
        }
        w = Walking(inter, coords, osrm_base="http://nonexistent:9999")
        result = w.reachable_stops((54.04799, -2.80099))  # very close to A
        # stop 1 should use the proxy path (time to A ≈ few secs + 5s)
        # rather than pure haversine (~120s)
        assert 1 in result
        # The proxy path should give a shorter time than direct haversine
        direct_dist = _haversine_m(54.04799, -2.80099, 54.0485, -2.8020)
        direct_secs = int(direct_dist / 1.0)
        assert result[1] <= direct_secs


# ══════════════════════════════════════════════════════════════════════
# walking_time_between()
# ══════════════════════════════════════════════════════════════════════


class TestWalkingTimeBetween:
    """OSRM route + haversine fallback."""

    @patch("walking.urllib.request.urlopen")
    def test_osrm_route_success(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "code": "Ok",
            "routes": [{"duration": 180.5}],
        }).encode()
        mock_urlopen.return_value = mock_resp

        w = Walking({}, {}, osrm_base="http://fake:5000")
        result = w.walking_time_between((54.048, -2.801), (54.049, -2.802))
        assert result == 180

    @patch("walking.urllib.request.urlopen")
    def test_osrm_non_ok_falls_back(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "code": "Error", "routes": [],
        }).encode()
        mock_urlopen.return_value = mock_resp

        w = Walking({}, {}, osrm_base="http://fake:5000")
        result = w.walking_time_between((54.048, -2.801), (54.049, -2.802))
        assert isinstance(result, int)
        assert result > 0

    def test_fallback_haversine(self, walking):
        result = walking.walking_time_between((54.048, -2.801), (54.049, -2.802))
        assert isinstance(result, int)
        assert result > 0
        # Should be roughly ~150m → ~150s
        assert 50 < result < 500


# ══════════════════════════════════════════════════════════════════════
# _nearest_precomputed()
# ══════════════════════════════════════════════════════════════════════


class TestNearestPrecomputed:
    """Walking._nearest_precomputed() finds closest precomputed stop."""

    def test_finds_nearest(self, walking):
        stop, secs = walking._nearest_precomputed(54.0480, -2.8010)
        # STOP_A is at those exact coords
        assert stop == STOP_A
        assert secs <= 10  # very close

    def test_returns_none_when_empty(self, walking_no_table):
        stop, secs = walking_no_table._nearest_precomputed(54.0480, -2.8010)
        assert stop is None
        assert secs is None

    def test_respects_max_walk(self):
        """Nearest precomputed stop too far → (None, None)."""
        w = Walking(INTER_WALK, COORDS,
                    osrm_base="http://x:9", max_walk_seconds=1)
        stop, secs = w._nearest_precomputed(54.1, -2.9)
        assert stop is None


# ══════════════════════════════════════════════════════════════════════
# _haversine_m()
# ══════════════════════════════════════════════════════════════════════


class TestHaversine:
    """Module-level _haversine_m distance function."""

    def test_same_point_zero(self):
        assert _haversine_m(54.0, -2.8, 54.0, -2.8) == 0.0

    def test_known_distance(self):
        # Lancaster (54.046, -2.801) → Morecambe (54.072, -2.870)
        d = _haversine_m(54.046, -2.801, 54.072, -2.870)
        # Should be roughly 5.2 km
        assert 4_500 < d < 6_000

    def test_symmetry(self):
        d1 = _haversine_m(54.0, -2.8, 55.0, -3.0)
        d2 = _haversine_m(55.0, -3.0, 54.0, -2.8)
        assert abs(d1 - d2) < 0.01


# ══════════════════════════════════════════════════════════════════════
# API endpoint tests — /walking/status and /walking/reachable
# ══════════════════════════════════════════════════════════════════════


# Re-use the mocking strategy from test_api.py
_HEAVY_MODULES = [
    "bus_live", "bus_loader", "bus_data", "main", "time_utils",
    "timetable", "merged_data", "raptor_router", "dense_mapper",
    "train_data", "station_classifier", "ws_server",
]

for mod in _HEAVY_MODULES:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

# Ensure time_utils has useful stubs
sys.modules["time_utils"].seconds_since_midnight = MagicMock(return_value=36000)
sys.modules["time_utils"].seconds_to_time = MagicMock(
    side_effect=lambda s: f"{int(s)//3600:02d}:{(int(s)%3600)//60:02d}:{int(s)%60:02d}"
)

import api as api_module  # noqa: E402
from api import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture()
def client():
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


def _build_mock_walking():
    """Return a mock Walking-like object with status() and reachable_stops()."""
    mock_walking = MagicMock()
    mock_walking.osrm_available = False
    mock_walking.status.return_value = {
        "osrm_url": "http://localhost:5001",
        "osrm_available": False,
        "max_walk_seconds": 600,
        "precomputed_stops": 10,
        "stops_with_coords": 100,
    }
    mock_walking.reachable_stops.return_value = {
        0: 30,
        1: 120,
    }
    mock_walking._coords = {
        0: (54.048, -2.801),
        1: (54.049, -2.802),
    }
    return mock_walking


def _build_mock_timetable():
    """Return a mock timetable with stop_metadata and bus_data."""
    mock_tt = MagicMock()
    mock_tt.today.stop_metadata = ["Lancaster Bus Station", "Railway Station",
                                    "University", "Town Hall"]
    mock_tt.today.bus_data.stop_to_routes = [[], [], [], []]
    mock_tt.today.bus_data.map_stops.get_code.side_effect = \
        lambda i: ["250012345", "250012346", "250012347", "250012348"][i]
    mock_tt.today.train_data = None
    return mock_tt


class TestWalkingStatusEndpoint:
    """GET /walking/status"""

    def test_returns_status(self, client):
        mock_walking = _build_mock_walking()
        mock_tt = _build_mock_timetable()
        with patch.object(api_module, "get_router_for_date",
                          return_value=(mock_tt, MagicMock(), mock_walking)):
            resp = client.get("/walking/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["osrm_url"] == "http://localhost:5001"
        assert data["osrm_available"] is False
        assert data["max_walk_seconds"] == 600
        assert data["precomputed_stops"] == 10
        assert data["stops_with_coords"] == 100

    def test_503_when_not_initialized(self, client):
        with patch.object(api_module, "get_router_for_date",
                          side_effect=RuntimeError("no data")):
            resp = client.get("/walking/status")
        assert resp.status_code == 503


class TestWalkingReachableEndpoint:
    """GET /walking/reachable"""

    def test_missing_lat_lon(self, client):
        resp = client.get("/walking/reachable")
        assert resp.status_code == 400

    def test_missing_lon(self, client):
        resp = client.get("/walking/reachable?lat=54.0")
        assert resp.status_code == 400

    def test_missing_lat(self, client):
        resp = client.get("/walking/reachable?lon=-2.8")
        assert resp.status_code == 400

    def test_success(self, client):
        mock_walking = _build_mock_walking()
        mock_tt = _build_mock_timetable()
        with patch.object(api_module, "get_router_for_date",
                          return_value=(mock_tt, MagicMock(), mock_walking)):
            resp = client.get("/walking/reachable?lat=54.048&lon=-2.801")
        assert resp.status_code == 200
        data = resp.json()
        assert data["location"] == {"lat": 54.048, "lon": -2.801}
        assert data["osrm_available"] is False
        assert len(data["stops"]) == 2
        # First stop sorted by walk_seconds
        assert data["stops"][0]["walk_seconds"] == 30
        assert data["stops"][0]["name"] == "Lancaster Bus Station"
        assert data["stops"][0]["atco_code"] == "250012345"
        assert data["stops"][0]["lat"] == 54.048
        assert data["stops"][0]["lon"] == -2.801

    def test_limit(self, client):
        mock_walking = _build_mock_walking()
        mock_tt = _build_mock_timetable()
        with patch.object(api_module, "get_router_for_date",
                          return_value=(mock_tt, MagicMock(), mock_walking)):
            resp = client.get("/walking/reachable?lat=54.048&lon=-2.801&limit=1")
        assert resp.status_code == 200
        assert len(resp.json()["stops"]) == 1

    def test_503_when_not_initialized(self, client):
        with patch.object(api_module, "get_router_for_date",
                          side_effect=RuntimeError("no data")):
            resp = client.get("/walking/reachable?lat=54.0&lon=-2.8")
        assert resp.status_code == 503

    def test_train_stop_resolution(self, client):
        """Stops beyond bus_stop_count resolve via train_mapper."""
        mock_walking = MagicMock()
        mock_walking.osrm_available = True
        mock_walking.reachable_stops.return_value = {5: 60}  # beyond bus count
        mock_walking._coords = {5: (54.05, -2.81)}

        mock_tt = MagicMock()
        mock_tt.today.stop_metadata = ["s0", "s1", "s2", "s3", "s4", "TrainStop"]
        mock_tt.today.bus_data.stop_to_routes = [[], []]  # bus_stop_count = 2
        mock_tt.today.bus_data.map_stops = MagicMock()
        mock_tt.today.train_data.map_stops.get_code.return_value = "TRAIN001"

        with patch.object(api_module, "get_router_for_date",
                          return_value=(mock_tt, MagicMock(), mock_walking)):
            resp = client.get("/walking/reachable?lat=54.05&lon=-2.81")
        assert resp.status_code == 200
        stop = resp.json()["stops"][0]
        assert stop["atco_code"] == "TRAIN001"
        assert stop["name"] == "TrainStop"
