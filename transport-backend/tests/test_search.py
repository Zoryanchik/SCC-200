"""Tests for the /search/stops endpoint.

Validates stop search with mocked BusLoader data  no real
filesystem or database access required.
"""
import sys
import os
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Mock heavy backend modules BEFORE importing api (same pattern as test_api)
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

import api as api_module  # noqa: E402
from api import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


#  Sample stop data 

SAMPLE_STOPS = [
    {"id": 0, "name": "Central Station", "atco_code": "2500ABC0001", "lat": 54.0123, "lon": -2.8012},
    {"id": 1, "name": "Centre Vale Park", "atco_code": "2500ABC0002", "lat": 54.0234, "lon": -2.8123},
    {"id": 2, "name": "Church Street", "atco_code": "2500ABC0003", "lat": 54.0345, "lon": -2.8234},
    {"id": 3, "name": "Lancaster Bus Station", "atco_code": "2500ABC0004", "lat": 54.0456, "lon": -2.8345},
    {"id": 4, "name": "Lancaster University", "atco_code": "2500ABC0005", "lat": 54.0100, "lon": -2.7850},
]


def _mock_search(query, limit=10):
    """Simulate BusLoader.search_stops  case-insensitive substring match."""
    q = query.lower()
    results = [s for s in SAMPLE_STOPS if q in s["name"].lower()]
    return results[:limit]


@pytest.fixture()
def client():
    """TestClient that does NOT trigger lifespan/startup events."""
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


#  /search/stops endpoint tests 


class TestSearchStopsEndpoint:
    """Validate the GET /search/stops endpoint contract."""

    def test_search_returns_200(self, client: TestClient):
        """GET /search/stops?q=cent returns HTTP 200."""
        mock_loader = MagicMock()
        mock_loader.search_stops.side_effect = _mock_search
        api_module._base_cache = {"loader": mock_loader}

        response = client.get("/search/stops", params={"q": "cent"})
        assert response.status_code == 200

    def test_search_returns_matching_stops(self, client: TestClient):
        """Searching for 'cent' should match 'Central Station' and 'Centre Vale Park'."""
        mock_loader = MagicMock()
        mock_loader.search_stops.side_effect = _mock_search
        api_module._base_cache = {"loader": mock_loader}

        response = client.get("/search/stops", params={"q": "cent"})
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 2
        names = {s["name"] for s in data}
        assert "Central Station" in names
        assert "Centre Vale Park" in names

    def test_search_returns_correct_shape(self, client: TestClient):
        """Each result must contain id, name, atco_code, lat, lon."""
        mock_loader = MagicMock()
        mock_loader.search_stops.side_effect = _mock_search
        api_module._base_cache = {"loader": mock_loader}

        response = client.get("/search/stops", params={"q": "central"})
        data = response.json()
        assert len(data) >= 1
        item = data[0]
        assert "id" in item
        assert "name" in item
        assert "atco_code" in item
        assert "lat" in item
        assert "lon" in item

    def test_search_respects_limit(self, client: TestClient):
        """Limit parameter caps the number of results returned."""
        mock_loader = MagicMock()
        mock_loader.search_stops.side_effect = _mock_search
        api_module._base_cache = {"loader": mock_loader}

        response = client.get("/search/stops", params={"q": "cent", "limit": 1})
        data = response.json()
        assert len(data) <= 1

    def test_search_empty_query_returns_empty(self, client: TestClient):
        """Empty query string returns an empty list."""
        mock_loader = MagicMock()
        mock_loader.search_stops.side_effect = _mock_search
        api_module._base_cache = {"loader": mock_loader}

        response = client.get("/search/stops", params={"q": ""})
        data = response.json()
        assert data == []

    def test_search_no_match_returns_empty(self, client: TestClient):
        """Query that matches nothing returns an empty list."""
        mock_loader = MagicMock()
        mock_loader.search_stops.side_effect = _mock_search
        api_module._base_cache = {"loader": mock_loader}

        response = client.get("/search/stops", params={"q": "zzzzz"})
        data = response.json()
        assert data == []

    def test_search_case_insensitive(self, client: TestClient):
        """Search should be case-insensitive."""
        mock_loader = MagicMock()
        mock_loader.search_stops.side_effect = _mock_search
        api_module._base_cache = {"loader": mock_loader}

        response = client.get("/search/stops", params={"q": "LANCASTER"})
        data = response.json()
        assert len(data) == 2
        names = {s["name"] for s in data}
        assert "Lancaster Bus Station" in names
        assert "Lancaster University" in names

    def test_search_default_limit_is_10(self, client: TestClient):
        """When limit is not provided, default should be 10."""
        mock_loader = MagicMock()
        mock_loader.search_stops.side_effect = _mock_search
        api_module._base_cache = {"loader": mock_loader}

        response = client.get("/search/stops", params={"q": "cent"})
        assert response.status_code == 200
        # Verify search_stops was called with limit=10
        mock_loader.search_stops.assert_called_once_with("cent", 10)

    def test_search_content_type_is_json(self, client: TestClient):
        """Response content-type must be application/json."""
        mock_loader = MagicMock()
        mock_loader.search_stops.side_effect = _mock_search
        api_module._base_cache = {"loader": mock_loader}

        response = client.get("/search/stops", params={"q": "cent"})
        assert "application/json" in response.headers["content-type"]

    def test_search_without_backend_returns_503(self, client: TestClient):
        """When _base_cache is None, the endpoint should return 503."""
        api_module._base_cache = None

        response = client.get("/search/stops", params={"q": "cent"})
        assert response.status_code == 503

    def test_search_lat_lon_types(self, client: TestClient):
        """lat and lon fields should be numeric (float or None)."""
        mock_loader = MagicMock()
        mock_loader.search_stops.side_effect = _mock_search
        api_module._base_cache = {"loader": mock_loader}

        response = client.get("/search/stops", params={"q": "central"})
        data = response.json()
        for item in data:
            assert isinstance(item["lat"], (int, float)) or item["lat"] is None
            assert isinstance(item["lon"], (int, float)) or item["lon"] is None

    def test_search_in_openapi_schema(self, client: TestClient):
        """The /search/stops path should appear in the OpenAPI schema."""
        response = client.get("/openapi.json")
        schema = response.json()
        assert "/search/stops" in schema["paths"]

    def test_search_loader_called_with_correct_args(self, client: TestClient):
        """search_stops should be called with query and limit."""
        mock_loader = MagicMock()
        mock_loader.search_stops.return_value = []
        api_module._base_cache = {"loader": mock_loader}

        client.get("/search/stops", params={"q": "test", "limit": 5})
        mock_loader.search_stops.assert_called_once_with("test", 5)

    def test_search_handles_loader_exception(self, client: TestClient):
        """If the loader raises an error, endpoint returns 500 gracefully."""
        mock_loader = MagicMock()
        mock_loader.search_stops.side_effect = RuntimeError("DB error")
        api_module._base_cache = {"loader": mock_loader}

        response = client.get("/search/stops", params={"q": "cent"})
        assert response.status_code == 500
        data = response.json()
        assert "error" in data
