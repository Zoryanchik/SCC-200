"""Tests for station_classifier module and /stops/classify + /search/stops
classification filtering endpoints.

Covers:
- Pure-logic unit tests for compute_stop_metrics, classify_stop, classify_all
- API endpoint tests for GET /stops/classify and GET /search/stops?classification=
"""
import sys
import os
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# ---------------------------------------------------------------------------
# Mock heavy backend modules BEFORE importing (same pattern as other tests)
# ---------------------------------------------------------------------------
_HEAVY_MODULES = [
    "bus_live",
    "bus_loader",
    "bus_data",
    "main",
    "time_utils",
    "timetable",
    "walking",
    "merged_data",
    "raptor_router",
    "dense_mapper",
    "train_data",
]

_mocks = {}
for mod in _HEAVY_MODULES:
    if mod not in sys.modules:
        _mocks[mod] = MagicMock()
        sys.modules[mod] = _mocks[mod]

sys.modules["time_utils"].seconds_since_midnight = MagicMock(return_value=36000)
sys.modules["time_utils"].seconds_to_time = MagicMock(
    side_effect=lambda s: f"{int(s)//3600:02d}:{(int(s)%3600)//60:02d}:{int(s)%60:02d}"
)

# Ensure station_classifier can be imported (it has no heavy deps)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from station_classifier import (  # noqa: E402
    DEFAULT_THRESHOLDS,
    classify_all,
    classify_stop,
    classify_to_lookup,
    compute_stop_metrics,
)

import api as api_module  # noqa: E402
from api import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


# ═══ Helper: build a mock MergedData ═════════════════════════════════


def _make_merged(
    stop_to_routes,
    route_stop_departures,
    route_metadata=None,
    stop_metadata=None,
):
    """Build a minimal mock MergedData with the given topology."""
    merged = MagicMock()
    merged.stop_to_routes = stop_to_routes
    merged.route_stop_departures = route_stop_departures
    merged.route_metadata = route_metadata or []
    merged.stop_metadata = stop_metadata or [""] * len(stop_to_routes)
    # Provide _group_mappers for get_atco_code compatibility
    merged._group_mappers = []
    return merged


# ═══ Unit tests: compute_stop_metrics ════════════════════════════════


class TestComputeStopMetrics:
    """Verify metric computation from MergedData."""

    def test_empty_network(self):
        merged = _make_merged([], [])
        metrics = compute_stop_metrics(merged)
        assert metrics == []

    def test_single_stop_no_routes(self):
        merged = _make_merged(
            stop_to_routes=[[]],
            route_stop_departures=[],
            stop_metadata=["Lonely Stop"],
        )
        m = compute_stop_metrics(merged)
        assert len(m) == 1
        assert m[0]["degree"] == 0
        assert m[0]["frequency"] == 0
        assert m[0]["interchange"] == 0
        assert m[0]["name"] == "Lonely Stop"

    def test_degree_counts_routes(self):
        merged = _make_merged(
            stop_to_routes=[[0, 1, 2]],  # stop 0 served by 3 routes
            route_stop_departures=[{}, {}, {}],
            route_metadata=[
                {"line_name": "X1"},
                {"line_name": "X2"},
                {"line_name": "X3"},
            ],
        )
        m = compute_stop_metrics(merged)
        assert m[0]["degree"] == 3

    def test_frequency_counts_departures(self):
        # Route 0 has 5 departures at stop 0, route 1 has 3
        merged = _make_merged(
            stop_to_routes=[[0, 1]],
            route_stop_departures=[
                {0: [(100, 0), (200, 1), (300, 2), (400, 3), (500, 4)]},
                {0: [(600, 5), (700, 6), (800, 7)]},
            ],
        )
        m = compute_stop_metrics(merged)
        assert m[0]["frequency"] == 8

    def test_interchange_counts_distinct_lines(self):
        merged = _make_merged(
            stop_to_routes=[[0, 1, 2]],
            route_stop_departures=[{}, {}, {}],
            route_metadata=[
                {"line_name": "40"},
                {"line_name": "40"},    # same line
                {"line_name": "100"},
            ],
        )
        m = compute_stop_metrics(merged)
        assert m[0]["interchange"] == 2
        assert sorted(m[0]["lines"]) == ["100", "40"]

    def test_route_metadata_none_entries_handled(self):
        merged = _make_merged(
            stop_to_routes=[[0, 1]],
            route_stop_departures=[{}, {}],
            route_metadata=[None, {"line_name": "X1"}],
        )
        m = compute_stop_metrics(merged)
        assert m[0]["interchange"] == 1

    def test_route_metadata_missing_line_name(self):
        merged = _make_merged(
            stop_to_routes=[[0]],
            route_stop_departures=[{}],
            route_metadata=[{"something_else": "value"}],
        )
        m = compute_stop_metrics(merged)
        assert m[0]["interchange"] == 0

    def test_multiple_stops(self):
        merged = _make_merged(
            stop_to_routes=[[0], [0, 1], []],
            route_stop_departures=[
                {0: [(100, 0)], 1: [(200, 1), (300, 2)]},
                {1: [(400, 3)]},
            ],
            stop_metadata=["Stop A", "Stop B", "Stop C"],
        )
        m = compute_stop_metrics(merged)
        assert len(m) == 3
        assert m[0]["name"] == "Stop A"
        assert m[0]["degree"] == 1
        assert m[1]["degree"] == 2
        assert m[1]["frequency"] == 3  # 2 from route 0 + 1 from route 1
        assert m[2]["degree"] == 0


# ═══ Unit tests: classify_stop ═══════════════════════════════════════


class TestClassifyStop:
    """Verify classification logic with default and custom thresholds."""

    def test_hub(self):
        m = {"degree": 5, "frequency": 100, "interchange": 2}
        assert classify_stop(m) == "hub"

    def test_hub_needs_both_degree_and_freq(self):
        # high degree but low frequency → not hub
        m = {"degree": 5, "frequency": 10, "interchange": 0}
        assert classify_stop(m) != "hub"

    def test_interchange_by_lines(self):
        m = {"degree": 2, "frequency": 50, "interchange": 3}
        assert classify_stop(m) == "interchange"

    def test_interchange_by_degree(self):
        m = {"degree": 4, "frequency": 50, "interchange": 1}
        assert classify_stop(m) == "interchange"

    def test_local(self):
        m = {"degree": 1, "frequency": 20, "interchange": 1}
        assert classify_stop(m) == "local"

    def test_request_stop(self):
        m = {"degree": 1, "frequency": 2, "interchange": 0}
        assert classify_stop(m) == "request_stop"

    def test_zero_everything(self):
        m = {"degree": 0, "frequency": 0, "interchange": 0}
        assert classify_stop(m) == "request_stop"

    def test_custom_thresholds(self):
        # With very low thresholds, even a small stop becomes a hub
        m = {"degree": 1, "frequency": 1, "interchange": 0}
        assert classify_stop(m, degree_hub=1, freq_hub=1) == "hub"

    def test_hub_boundary_exact(self):
        """Exactly at default thresholds → hub."""
        m = {"degree": 5, "frequency": 100, "interchange": 0}
        assert classify_stop(m) == "hub"

    def test_interchange_boundary_exact(self):
        m = {"degree": 4, "frequency": 5, "interchange": 0}
        assert classify_stop(m) == "interchange"

    def test_local_boundary_exact(self):
        m = {"degree": 1, "frequency": 10, "interchange": 0}
        assert classify_stop(m) == "local"

    def test_just_below_local(self):
        m = {"degree": 1, "frequency": 9, "interchange": 0}
        assert classify_stop(m) == "request_stop"


# ═══ Unit tests: classify_all / classify_to_lookup ═══════════════════


class TestClassifyAll:
    """Integration of metrics + classification."""

    def _hub_merged(self):
        """Build a network with one hub stop."""
        return _make_merged(
            stop_to_routes=[[0, 1, 2, 3, 4]],  # degree=5
            route_stop_departures=[
                {0: [(i * 10, i) for i in range(25)]},
                {0: [(i * 10, i) for i in range(25)]},
                {0: [(i * 10, i) for i in range(25)]},
                {0: [(i * 10, i) for i in range(25)]},
                {},
            ],  # frequency=100
            route_metadata=[
                {"line_name": "A"}, {"line_name": "B"},
                {"line_name": "C"}, {"line_name": "D"},
                {"line_name": "E"},
            ],
            stop_metadata=["Hub Station"],
        )

    def test_classify_all_returns_classification_key(self):
        merged = self._hub_merged()
        result = classify_all(merged)
        assert len(result) == 1
        assert "classification" in result[0]
        assert result[0]["classification"] == "hub"

    def test_classify_to_lookup(self):
        merged = self._hub_merged()
        lookup = classify_to_lookup(merged)
        assert lookup == {0: "hub"}

    def test_custom_thresholds_passed_through(self):
        merged = _make_merged(
            stop_to_routes=[[0]],
            route_stop_departures=[{0: [(1, 0)]}],
            stop_metadata=["Tiny"],
        )
        # Default would classify as request_stop (degree=1, freq=1)
        result = classify_all(merged, thresholds={"freq_local": 1})
        assert result[0]["classification"] == "local"

    def test_mixed_classifications(self):
        """Network with hub, interchange, local, request_stop."""
        merged = _make_merged(
            stop_to_routes=[
                [0, 1, 2, 3, 4],  # stop 0: hub candidate
                [0, 1, 2, 3],     # stop 1: interchange by degree
                [0],              # stop 2: local
                [],               # stop 3: request_stop
            ],
            route_stop_departures=[
                {0: [(i, i) for i in range(30)], 1: [(i, i) for i in range(10)],
                 2: [(i, i) for i in range(15)]},
                {0: [(i, i) for i in range(30)], 1: [(i, i) for i in range(10)]},
                {0: [(i, i) for i in range(30)]},
                {0: [(i, i) for i in range(30)]},
                {},
            ],
            route_metadata=[
                {"line_name": "A"}, {"line_name": "B"},
                {"line_name": "C"}, {"line_name": "D"},
                {"line_name": "E"},
            ],
            stop_metadata=["Hub", "Interchange", "Local", "Request"],
        )
        result = classify_all(merged)
        classes = {r["name"]: r["classification"] for r in result}
        assert classes["Hub"] == "hub"
        assert classes["Interchange"] == "interchange"
        assert classes["Local"] == "local"
        assert classes["Request"] == "request_stop"


# ═══ Endpoint tests: /stops/classify ═════════════════════════════════


@pytest.fixture()
def client():
    """TestClient that does NOT trigger lifespan/startup events."""
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


def _build_mock_merged():
    """Create a mock merged data with a 4-stop network for classify tests."""
    merged = _make_merged(
        stop_to_routes=[
            [0, 1, 2, 3, 4],  # stop 0: hub
            [0, 1, 2, 3],     # stop 1: interchange
            [0],              # stop 2: local
            [],               # stop 3: request_stop
        ],
        route_stop_departures=[
            {0: [(i, i) for i in range(30)], 1: [(i, i) for i in range(10)],
             2: [(i, i) for i in range(15)]},
            {0: [(i, i) for i in range(30)], 1: [(i, i) for i in range(10)]},
            {0: [(i, i) for i in range(30)]},
            {0: [(i, i) for i in range(30)]},
            {},
        ],
        route_metadata=[
            {"line_name": "A"}, {"line_name": "B"},
            {"line_name": "C"}, {"line_name": "D"},
            {"line_name": "E"},
        ],
        stop_metadata=["Hub Station", "Interchange St", "Local Rd", "Request Stop"],
    )
    return merged


class TestStopsClassifyEndpoint:
    """Tests for GET /stops/classify."""

    def test_returns_all_stops(self, client: TestClient):
        merged = _build_mock_merged()
        with patch.object(api_module, "get_router_for_date",
                          return_value=(merged, MagicMock(), MagicMock())):
            resp = client.get("/stops/classify")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 4
        classes = {d["name"]: d["classification"] for d in data}
        assert classes["Hub Station"] == "hub"
        assert classes["Request Stop"] == "request_stop"

    def test_filter_by_hub(self, client: TestClient):
        merged = _build_mock_merged()
        with patch.object(api_module, "get_router_for_date",
                          return_value=(merged, MagicMock(), MagicMock())):
            resp = client.get("/stops/classify", params={"classification": "hub"})
        data = resp.json()
        assert len(data) == 1
        assert data[0]["classification"] == "hub"
        assert data[0]["name"] == "Hub Station"

    def test_filter_by_request_stop(self, client: TestClient):
        merged = _build_mock_merged()
        with patch.object(api_module, "get_router_for_date",
                          return_value=(merged, MagicMock(), MagicMock())):
            resp = client.get("/stops/classify", params={"classification": "request_stop"})
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "Request Stop"

    def test_filter_no_match(self, client: TestClient):
        """When filtered class has no stops, return empty list."""
        merged = _make_merged(
            stop_to_routes=[[0]],
            route_stop_departures=[{0: [(1, 0)]}],
            stop_metadata=["Only Stop"],
        )
        with patch.object(api_module, "get_router_for_date",
                          return_value=(merged, MagicMock(), MagicMock())):
            resp = client.get("/stops/classify", params={"classification": "hub"})
        assert resp.json() == []

    def test_invalid_classification_returns_400(self, client: TestClient):
        resp = client.get("/stops/classify", params={"classification": "mega_hub"})
        assert resp.status_code == 400
        assert "Invalid classification" in resp.json()["error"]

    def test_router_failure_returns_503(self, client: TestClient):
        with patch.object(api_module, "get_router_for_date",
                          side_effect=RuntimeError("no data")):
            resp = client.get("/stops/classify")
        assert resp.status_code == 503

    def test_response_includes_metrics(self, client: TestClient):
        """Each entry should include degree, frequency, interchange, lines."""
        merged = _build_mock_merged()
        with patch.object(api_module, "get_router_for_date",
                          return_value=(merged, MagicMock(), MagicMock())):
            resp = client.get("/stops/classify")
        data = resp.json()
        entry = data[0]
        assert "degree" in entry
        assert "frequency" in entry
        assert "interchange" in entry
        assert "lines" in entry
        assert "stop_index" in entry


# ═══ Endpoint tests: /search/stops?classification= ═══════════════════


SAMPLE_STOPS = [
    {"id": 0, "name": "Central Station", "atco_code": "2500ABC0001",
     "lat": 54.012, "lon": -2.801},
    {"id": 1, "name": "Centre Vale Park", "atco_code": "2500ABC0002",
     "lat": 54.023, "lon": -2.812},
    {"id": 2, "name": "Church Street", "atco_code": "2500ABC0003",
     "lat": 54.034, "lon": -2.823},
]


def _mock_search(query, limit=10):
    q = query.lower()
    return [s.copy() for s in SAMPLE_STOPS if q in s["name"].lower()][:limit]


class TestSearchStopsClassificationFilter:
    """Tests for GET /search/stops with ?classification= param."""

    def _setup_loader(self):
        mock_loader = MagicMock()
        mock_loader.search_stops.side_effect = _mock_search
        api_module._base_cache = {"loader": mock_loader}
        api_module._classification_cache = None

    def test_without_classification_returns_geocoded(self, client: TestClient):
        """Default search (no classification) returns only geocoded locations."""
        self._setup_loader()
        locations = [
            {"id": "loc:0", "name": "Centenary Way", "lat": 54.0,
             "lon": -2.8, "atco_code": None, "type": "location"},
        ]
        with patch("api.geocode_locations", return_value=locations):
            resp = client.get("/search/stops", params={"q": "cent"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["type"] == "location"
        api_module._classification_cache = None

    def test_with_classification_filters_results(self, client: TestClient):
        self._setup_loader()
        mock_lookup = {
            "2500ABC0001": "hub",
            "2500ABC0002": "local",
            "2500ABC0003": "interchange",
        }
        with patch.object(api_module, "_get_classification_lookup",
                          return_value=mock_lookup):
            resp = client.get("/search/stops",
                              params={"q": "cent", "classification": "hub"})
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "Central Station"
        assert data[0]["classification"] == "hub"
        api_module._classification_cache = None

    def test_classification_local_filter(self, client: TestClient):
        self._setup_loader()
        mock_lookup = {
            "2500ABC0001": "hub",
            "2500ABC0002": "local",
        }
        with patch.object(api_module, "_get_classification_lookup",
                          return_value=mock_lookup):
            resp = client.get("/search/stops",
                              params={"q": "cent", "classification": "local"})
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "Centre Vale Park"
        api_module._classification_cache = None

    def test_classification_no_match(self, client: TestClient):
        self._setup_loader()
        mock_lookup = {"2500ABC0001": "local", "2500ABC0002": "local"}
        with patch.object(api_module, "_get_classification_lookup",
                          return_value=mock_lookup):
            resp = client.get("/search/stops",
                              params={"q": "cent", "classification": "hub"})
        assert resp.json() == []
        api_module._classification_cache = None

    def test_invalid_classification_returns_400(self, client: TestClient):
        self._setup_loader()
        resp = client.get("/search/stops",
                          params={"q": "cent", "classification": "invalid"})
        assert resp.status_code == 400
        api_module._classification_cache = None

    def test_classification_excludes_geocode_results(self, client: TestClient):
        """When classification filter is active, geocode locations are skipped."""
        self._setup_loader()
        mock_lookup = {"2500ABC0001": "hub"}
        with patch.object(api_module, "_get_classification_lookup",
                          return_value=mock_lookup), \
             patch("api.geocode_locations") as mock_geo:
            resp = client.get("/search/stops",
                              params={"q": "cent", "classification": "hub"})
            # geocode_locations should NOT have been called
            mock_geo.assert_not_called()
        api_module._classification_cache = None

    def test_classification_respects_limit(self, client: TestClient):
        self._setup_loader()
        mock_lookup = {
            "2500ABC0001": "local",
            "2500ABC0002": "local",
            "2500ABC0003": "local",
        }
        with patch.object(api_module, "_get_classification_lookup",
                          return_value=mock_lookup):
            resp = client.get("/search/stops",
                              params={"q": "c", "classification": "local",
                                      "limit": "1"})
        data = resp.json()
        assert len(data) <= 1
        api_module._classification_cache = None


# ═══ Unit tests: _get_classification_lookup / _resolve_stop_classification ═══


class TestClassificationHelpers:
    """Test the api-level helper functions."""

    def test_resolve_stop_classification_found(self):
        lookup = {"ATCO1": "hub", "ATCO2": "local"}
        assert api_module._resolve_stop_classification("ATCO1", lookup) == "hub"

    def test_resolve_stop_classification_not_found(self):
        assert api_module._resolve_stop_classification("MISSING", {}) == "request_stop"

    def test_resolve_stop_classification_none_code(self):
        assert api_module._resolve_stop_classification(None, {"A": "hub"}) == "request_stop"

    def test_get_classification_lookup_caches(self):
        """Second call should return cached result without calling router."""
        api_module._classification_cache = {"CACHED": "hub"}
        result = api_module._get_classification_lookup()
        assert result == {"CACHED": "hub"}
        api_module._classification_cache = None  # cleanup

    def test_get_classification_lookup_builds_from_merged(self):
        api_module._classification_cache = None
        merged = _build_mock_merged()
        # Make get_atco_code return distinct ATCO codes
        codes = ["ATCO_A", "ATCO_B", "ATCO_C", "ATCO_D"]
        merged.get_atco_code = lambda i: codes[i] if i < len(codes) else None
        with patch.object(api_module, "get_router_for_date",
                          return_value=(merged, MagicMock(), MagicMock())):
            result = api_module._get_classification_lookup()
        # Should have 4 entries (one per stop)
        assert len(result) == 4
        assert "ATCO_A" in result
        api_module._classification_cache = None

    def test_get_classification_lookup_handles_router_error(self):
        api_module._classification_cache = None
        with patch.object(api_module, "get_router_for_date",
                          side_effect=RuntimeError("no data")):
            result = api_module._get_classification_lookup()
        assert result == {}
        api_module._classification_cache = None
