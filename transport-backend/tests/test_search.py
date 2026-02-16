"""Tests for the /search/stops endpoint and geocode_locations function.

Validates stop search with mocked BusLoader data and geocoding
with mocked urlopen — no real filesystem, database, or network
access required.
"""
import io
import json
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


@pytest.fixture(autouse=False)
def _mock_geocode():
    with patch("api.geocode_locations", return_value=[]):
        yield


#  /search/stops endpoint tests 


@pytest.mark.usefixtures("_mock_geocode")
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
        assert "type" in item
        assert item["type"] == "stop"

    def test_search_includes_location_results(self, client: TestClient):
        """Geocoded locations should be included with type=location."""
        mock_loader = MagicMock()
        mock_loader.search_stops.return_value = []
        api_module._base_cache = {"loader": mock_loader}

        locations = [
            {
                "id": "loc:0",
                "name": "Lancaster, UK",
                "atco_code": None,
                "lat": 54.047,
                "lon": -2.801,
                "type": "location",
            }
        ]

        with patch("api.geocode_locations", return_value=locations) as geocode_mock:
            response = client.get("/search/stops", params={"q": "lancaster", "limit": 3})

        data = response.json()
        assert data == locations
        geocode_mock.assert_called_once_with("lancaster", 3)

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

    def test_search_mixed_stops_and_locations(self, client: TestClient):
        """Stops and geocoded locations should be merged in the response."""
        mock_loader = MagicMock()
        mock_loader.search_stops.side_effect = _mock_search
        api_module._base_cache = {"loader": mock_loader}

        fake_locations = [
            {
                "id": "loc:0",
                "name": "Lancaster Town Hall",
                "lat": 54.048,
                "lon": -2.799,
                "atco_code": None,
                "type": "location",
            }
        ]
        with patch("api.geocode_locations", return_value=fake_locations):
            response = client.get(
                "/search/stops", params={"q": "lancaster", "limit": 10}
            )

        data = response.json()
        stop_items = [d for d in data if d.get("type") == "stop"]
        loc_items = [d for d in data if d.get("type") == "location"]
        assert len(stop_items) == 2  # Lancaster Bus Station + Lancaster University
        assert len(loc_items) == 1
        assert loc_items[0]["name"] == "Lancaster Town Hall"

    def test_search_geocode_failure_returns_stops_only(self, client: TestClient):
        """If geocoding raises an error, stops should still be returned."""
        mock_loader = MagicMock()
        mock_loader.search_stops.side_effect = _mock_search
        api_module._base_cache = {"loader": mock_loader}

        with patch("api.geocode_locations", side_effect=Exception("timeout")):
            response = client.get(
                "/search/stops", params={"q": "central", "limit": 5}
            )

        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
        assert all(d.get("type") == "stop" for d in data)

    def test_search_all_results_have_type_field(self, client: TestClient):
        """Every result must include a 'type' field ('stop' or 'location')."""
        mock_loader = MagicMock()
        mock_loader.search_stops.side_effect = _mock_search
        api_module._base_cache = {"loader": mock_loader}

        fake_locs = [
            {
                "id": "loc:0",
                "name": "Somewhere",
                "lat": 54.0,
                "lon": -2.0,
                "atco_code": None,
                "type": "location",
            }
        ]
        with patch("api.geocode_locations", return_value=fake_locs):
            response = client.get(
                "/search/stops", params={"q": "cent", "limit": 10}
            )

        data = response.json()
        assert len(data) >= 1
        for item in data:
            assert "type" in item
            assert item["type"] in ("stop", "location")


# ── geocode_locations unit tests ──────────────────────────────────────────────


def _make_nominatim_response(items):
    """Create a mock urlopen context-manager returning Nominatim-style JSON."""
    body = json.dumps(items).encode("utf-8")
    buf = io.BytesIO(body)
    buf.read_orig = buf.read

    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=buf)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


class TestGeocodeLocations:
    """Unit tests for the geocode_locations helper function."""

    @patch("api.urlopen")
    def test_returns_locations_from_nominatim(self, mock_urlopen):
        """Nominatim results are converted to {id, name, lat, lon, atco_code, type}."""
        nominatim_data = [
            {"lat": "54.047", "lon": "-2.801", "display_name": "Lancaster, UK"},
            {"lat": "51.509", "lon": "-0.118", "display_name": "London, UK"},
        ]
        mock_urlopen.return_value = _make_nominatim_response(nominatim_data)

        results = api_module.geocode_locations("Lancaster", limit=5)

        assert len(results) == 2
        assert results[0]["name"] == "Lancaster, UK"
        assert results[0]["lat"] == 54.047
        assert results[0]["lon"] == -2.801
        assert results[0]["type"] == "location"
        assert results[0]["atco_code"] is None
        assert results[0]["id"] == "loc:0"
        assert results[1]["id"] == "loc:1"

    @patch("api.urlopen")
    def test_empty_query_returns_empty(self, mock_urlopen):
        """An empty query string should return [] without calling the API."""
        results = api_module.geocode_locations("", limit=5)
        assert results == []
        mock_urlopen.assert_not_called()

    @patch("api.urlopen")
    def test_zero_limit_returns_empty(self, mock_urlopen):
        """limit=0 should return [] without calling the API."""
        results = api_module.geocode_locations("test", limit=0)
        assert results == []
        mock_urlopen.assert_not_called()

    @patch("api.urlopen")
    def test_negative_limit_returns_empty(self, mock_urlopen):
        """Negative limit should return [] without calling the API."""
        results = api_module.geocode_locations("test", limit=-1)
        assert results == []
        mock_urlopen.assert_not_called()

    @patch("api.urlopen")
    def test_skips_entries_with_invalid_coords(self, mock_urlopen):
        """Entries with non-numeric lat/lon should be skipped."""
        nominatim_data = [
            {"lat": "not-a-number", "lon": "-2.0", "display_name": "Bad"},
            {"lat": "54.0", "lon": "-2.0", "display_name": "Good"},
        ]
        mock_urlopen.return_value = _make_nominatim_response(nominatim_data)

        results = api_module.geocode_locations("test", limit=5)
        assert len(results) == 1
        assert results[0]["name"] == "Good"

    @patch("api.urlopen")
    def test_falls_back_to_query_when_no_name(self, mock_urlopen):
        """If Nominatim provides no display_name/name, use the query string."""
        nominatim_data = [{"lat": "54.0", "lon": "-2.0"}]
        mock_urlopen.return_value = _make_nominatim_response(nominatim_data)

        results = api_module.geocode_locations("my query", limit=5)
        assert len(results) == 1
        assert results[0]["name"] == "my query"

    @patch("api.urlopen")
    def test_uses_name_field_when_display_name_missing(self, mock_urlopen):
        """fallback to 'name' field if 'display_name' is absent."""
        nominatim_data = [
            {"lat": "54.0", "lon": "-2.0", "name": "Short Name"}
        ]
        mock_urlopen.return_value = _make_nominatim_response(nominatim_data)

        results = api_module.geocode_locations("q", limit=5)
        assert results[0]["name"] == "Short Name"

    @patch("api.urlopen")
    def test_api_timeout_propagates(self, mock_urlopen):
        """Network errors should propagate to the caller."""
        mock_urlopen.side_effect = Exception("Connection timed out")

        with pytest.raises(Exception, match="Connection timed out"):
            api_module.geocode_locations("Lancaster", limit=5)

    @patch("api.urlopen")
    def test_respects_limit_in_url(self, mock_urlopen):
        """The limit parameter should be passed to the Nominatim URL."""
        mock_urlopen.return_value = _make_nominatim_response([])

        api_module.geocode_locations("test", limit=3)

        call_args = mock_urlopen.call_args
        request_obj = call_args[0][0]
        assert "limit=3" in request_obj.full_url

    @patch("api.urlopen")
    def test_user_agent_header_set(self, mock_urlopen):
        """Request must include a User-Agent header (Nominatim requirement)."""
        mock_urlopen.return_value = _make_nominatim_response([])

        api_module.geocode_locations("test", limit=3)

        call_args = mock_urlopen.call_args
        request_obj = call_args[0][0]
        assert "User-agent" in request_obj.headers

    @patch("api.urlopen")
    def test_returns_empty_for_empty_api_response(self, mock_urlopen):
        """Empty Nominatim response should return []."""
        mock_urlopen.return_value = _make_nominatim_response([])

        results = api_module.geocode_locations("nowhere", limit=5)
        assert results == []
