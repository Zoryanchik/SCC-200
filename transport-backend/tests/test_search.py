"""Tests for the /search/stops endpoint and geocode_locations function.

Validates stop search with mocked BusLoader data and geocoding
with mocked urlopen — no real filesystem, database, or network
access required.
"""
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


@pytest.fixture()
def client():
    """TestClient that does NOT trigger lifespan/startup events."""
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


#  /search/stops endpoint tests 


class TestSearchStopsEndpoint:
    """Validate the GET /search/stops endpoint contract.

    The default search (no classification filter) returns only geocoded
    locations from Nominatim, not bus-stop DB results.
    """

    def test_search_returns_200(self, client: TestClient):
        """GET /search/stops?q=lancaster returns HTTP 200."""
        mock_loader = MagicMock()
        api_module._base_cache = {"loader": mock_loader}

        locations = [{"id": "loc:0", "name": "Lancaster, UK",
                      "lat": 54.047, "lon": -2.801,
                      "atco_code": None, "type": "location"}]
        with patch("api.geocode_locations", return_value=locations):
            response = client.get("/search/stops", params={"q": "lancaster"})
        assert response.status_code == 200

    def test_search_returns_geocoded_locations(self, client: TestClient):
        """Default search returns geocoded locations, not bus stops."""
        mock_loader = MagicMock()
        api_module._base_cache = {"loader": mock_loader}

        locations = [
            {"id": "loc:0", "name": "Lancaster, Lancashire, UK",
             "lat": 54.047, "lon": -2.801,
             "atco_code": None, "type": "location"},
            {"id": "loc:1", "name": "Lancaster University, Lancashire, UK",
             "lat": 54.010, "lon": -2.785,
             "atco_code": None, "type": "location"},
        ]
        with patch("api.geocode_locations", return_value=locations):
            response = client.get("/search/stops", params={"q": "lancaster"})

        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 2
        assert all(d["type"] == "location" for d in data)
        names = {s["name"] for s in data}
        assert "Lancaster, Lancashire, UK" in names

    def test_search_returns_correct_shape(self, client: TestClient):
        """Each result must contain id, name, atco_code, lat, lon, type."""
        mock_loader = MagicMock()
        api_module._base_cache = {"loader": mock_loader}

        locations = [{"id": "loc:0", "name": "Central Lancaster",
                      "lat": 54.05, "lon": -2.80,
                      "atco_code": None, "type": "location"}]
        with patch("api.geocode_locations", return_value=locations):
            response = client.get("/search/stops", params={"q": "central"})
        data = response.json()
        assert len(data) >= 1
        item = data[0]
        assert "id" in item
        assert "name" in item
        assert "lat" in item
        assert "lon" in item
        assert "type" in item
        assert item["type"] == "location"

    def test_search_includes_location_results(self, client: TestClient):
        """Geocoded locations should be returned with type=location."""
        mock_loader = MagicMock()
        api_module._base_cache = {"loader": mock_loader}

        locations = [
            {"id": "loc:0", "name": "Lancaster, UK",
             "atco_code": None, "lat": 54.047, "lon": -2.801,
             "type": "location"},
        ]

        with patch("api.geocode_locations", return_value=locations) as geocode_mock:
            response = client.get("/search/stops", params={"q": "lancaster", "limit": 3})

        data = response.json()
        assert data == locations
        geocode_mock.assert_called_once_with("lancaster", 3)

    def test_search_respects_limit(self, client: TestClient):
        """Limit parameter is passed to geocode_locations."""
        mock_loader = MagicMock()
        api_module._base_cache = {"loader": mock_loader}

        with patch("api.geocode_locations", return_value=[]) as geo_mock:
            client.get("/search/stops", params={"q": "cent", "limit": 1})
        geo_mock.assert_called_once_with("cent", 1)

    def test_search_empty_query_returns_empty(self, client: TestClient):
        """Empty query string returns an empty list."""
        mock_loader = MagicMock()
        api_module._base_cache = {"loader": mock_loader}

        response = client.get("/search/stops", params={"q": ""})
        data = response.json()
        assert data == []

    def test_search_no_match_returns_empty(self, client: TestClient):
        """Query that matches nothing returns an empty list."""
        mock_loader = MagicMock()
        api_module._base_cache = {"loader": mock_loader}

        with patch("api.geocode_locations", return_value=[]):
            response = client.get("/search/stops", params={"q": "zzzzz"})
        data = response.json()
        assert data == []

    def test_search_default_limit_is_10(self, client: TestClient):
        """When limit is not provided, default should be 10."""
        mock_loader = MagicMock()
        api_module._base_cache = {"loader": mock_loader}

        with patch("api.geocode_locations", return_value=[]) as geo_mock:
            client.get("/search/stops", params={"q": "cent"})
        geo_mock.assert_called_once_with("cent", 10)

    def test_search_content_type_is_json(self, client: TestClient):
        """Response content-type must be application/json."""
        mock_loader = MagicMock()
        api_module._base_cache = {"loader": mock_loader}

        with patch("api.geocode_locations", return_value=[]):
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
        api_module._base_cache = {"loader": mock_loader}

        locations = [{"id": "loc:0", "name": "Somewhere",
                      "lat": 54.0, "lon": -2.0,
                      "atco_code": None, "type": "location"}]
        with patch("api.geocode_locations", return_value=locations):
            response = client.get("/search/stops", params={"q": "some"})
        data = response.json()
        for item in data:
            assert isinstance(item["lat"], (int, float)) or item["lat"] is None
            assert isinstance(item["lon"], (int, float)) or item["lon"] is None

    def test_search_in_openapi_schema(self, client: TestClient):
        """The /search/stops path should appear in the OpenAPI schema."""
        response = client.get("/openapi.json")
        schema = response.json()
        assert "/search/stops" in schema["paths"]

    def test_search_geocode_failure_returns_empty(self, client: TestClient):
        """If geocoding raises an error, endpoint returns empty list."""
        mock_loader = MagicMock()
        api_module._base_cache = {"loader": mock_loader}

        with patch("api.geocode_locations", side_effect=Exception("timeout")):
            response = client.get(
                "/search/stops", params={"q": "central", "limit": 5}
            )

        assert response.status_code == 200
        data = response.json()
        assert data == []

    def test_search_all_results_have_type_field(self, client: TestClient):
        """Every result must include a 'type' field set to 'location'."""
        mock_loader = MagicMock()
        api_module._base_cache = {"loader": mock_loader}

        fake_locs = [
            {"id": "loc:0", "name": "Somewhere", "lat": 54.0, "lon": -2.0,
             "atco_code": None, "type": "location"},
            {"id": "loc:1", "name": "Elsewhere", "lat": 54.1, "lon": -2.1,
             "atco_code": None, "type": "location"},
        ]
        with patch("api.geocode_locations", return_value=fake_locs):
            response = client.get(
                "/search/stops", params={"q": "place", "limit": 10}
            )

        data = response.json()
        assert len(data) == 2
        for item in data:
            assert "type" in item
            assert item["type"] == "location"


# ── Fuzzy correction unit tests ──────────────────────────────────────────────


class TestFuzzyCorrectQuery:
    """Unit tests for _fuzzy_correct_query typo correction."""

    def test_corrects_single_word_typo(self):
        assert api_module._fuzzy_correct_query("Lancster") == "Lancaster"

    def test_corrects_blackpool_typo(self):
        assert api_module._fuzzy_correct_query("Blackpol") == "Blackpool"

    def test_corrects_brand_typo(self):
        assert api_module._fuzzy_correct_query("Sainsbry") == "Sainsbury"

    def test_corrects_word_in_multi_word_query(self):
        result = api_module._fuzzy_correct_query("Lancster University")
        assert "Lancaster" in result

    def test_exact_match_unchanged(self):
        assert api_module._fuzzy_correct_query("Lancaster") == "Lancaster"

    def test_unknown_query_unchanged(self):
        """Queries that don't match anything should pass through."""
        assert api_module._fuzzy_correct_query("xyzzyplugh") == "xyzzyplugh"

    def test_short_words_skipped(self):
        """Words shorter than 4 chars should not be fuzzy-matched."""
        result = api_module._fuzzy_correct_query("the bus")
        assert result == "the bus"

    def test_empty_string(self):
        assert api_module._fuzzy_correct_query("") == ""


# ── geocode_locations unit tests ──────────────────────────────────────────────


def _make_requests_response(items):
    """Create a mock requests.Response returning Nominatim-style JSON."""
    resp = MagicMock()
    resp.json.return_value = items
    resp.raise_for_status = MagicMock()
    return resp


class TestGeocodeLocations:
    """Unit tests for the geocode_locations helper function."""

    @patch("requests.get")
    def test_returns_locations_from_nominatim(self, mock_get):
        """Nominatim results are converted to {id, name, lat, lon, atco_code, type}."""
        nominatim_data = [
            {"lat": "54.047", "lon": "-2.801",
             "display_name": "Lancaster, Lancashire, UK",
             "address": {"city": "Lancaster", "county": "Lancashire"}},
            {"lat": "53.76", "lon": "-2.70",
             "display_name": "Preston, Lancashire, UK",
             "address": {"city": "Preston", "county": "Lancashire"}},
        ]
        mock_get.return_value = _make_requests_response(nominatim_data)

        results = api_module.geocode_locations("Lancaster", limit=5)

        assert len(results) == 2
        assert results[0]["name"] == "Lancaster, Lancashire, UK"
        assert results[0]["lat"] == 54.047
        assert results[0]["lon"] == -2.801
        assert results[0]["type"] == "location"
        assert results[0]["atco_code"] is None
        assert results[0]["id"] == "loc:0"
        assert results[1]["id"] == "loc:1"

    @patch("requests.get")
    def test_empty_query_returns_empty(self, mock_get):
        """An empty query string should return [] without calling the API."""
        results = api_module.geocode_locations("", limit=5)
        assert results == []
        mock_get.assert_not_called()

    @patch("requests.get")
    def test_zero_limit_returns_empty(self, mock_get):
        """limit=0 should return [] without calling the API."""
        results = api_module.geocode_locations("test", limit=0)
        assert results == []
        mock_get.assert_not_called()

    @patch("requests.get")
    def test_negative_limit_returns_empty(self, mock_get):
        """Negative limit should return [] without calling the API."""
        results = api_module.geocode_locations("test", limit=-1)
        assert results == []
        mock_get.assert_not_called()

    @patch("requests.get")
    def test_skips_entries_with_invalid_coords(self, mock_get):
        """Entries with non-numeric lat/lon should be skipped."""
        nominatim_data = [
            {"lat": "not-a-number", "lon": "-2.0", "display_name": "Bad, Lancashire",
             "address": {"county": "Lancashire"}},
            {"lat": "54.0", "lon": "-2.0", "display_name": "Good, Lancashire",
             "address": {"county": "Lancashire"}},
        ]
        mock_get.return_value = _make_requests_response(nominatim_data)

        results = api_module.geocode_locations("test", limit=5)
        assert len(results) == 1
        assert results[0]["name"] == "Good, Lancashire"

    @patch("requests.get")
    def test_falls_back_to_query_when_no_name(self, mock_get):
        """If Nominatim provides no display_name/name, use the query string."""
        nominatim_data = [{"lat": "54.0", "lon": "-2.0",
                           "address": {"county": "Lancashire"}}]
        mock_get.return_value = _make_requests_response(nominatim_data)

        results = api_module.geocode_locations("my query", limit=5)
        assert len(results) == 1
        assert results[0]["name"] == "my query"

    @patch("requests.get")
    def test_uses_name_field_when_display_name_missing(self, mock_get):
        """fallback to 'name' field if 'display_name' is absent."""
        nominatim_data = [
            {"lat": "54.0", "lon": "-2.0", "name": "Short Name",
             "address": {"county": "Lancashire"}}
        ]
        mock_get.return_value = _make_requests_response(nominatim_data)

        results = api_module.geocode_locations("q", limit=5)
        assert results[0]["name"] == "Short Name"

    @patch("requests.get")
    def test_api_timeout_propagates(self, mock_get):
        """Network errors should propagate to the caller."""
        mock_get.side_effect = Exception("Connection timed out")

        with pytest.raises(Exception, match="Connection timed out"):
            api_module.geocode_locations("Lancaster", limit=5)

    @patch("requests.get")
    def test_respects_limit_in_url(self, mock_get):
        """The over-fetched limit (limit*3) should appear in the Nominatim URL."""
        mock_get.return_value = _make_requests_response([])

        api_module.geocode_locations("test", limit=3)

        call_args = mock_get.call_args
        url = call_args[0][0]  # first positional arg is the URL
        # county default="Lancashire" → over-fetch is limit*3 = 9
        assert "limit=9" in url

    @patch("requests.get")
    def test_user_agent_header_set(self, mock_get):
        """Request must include a User-Agent header (Nominatim requirement)."""
        mock_get.return_value = _make_requests_response([])

        api_module.geocode_locations("test", limit=3)

        call_args = mock_get.call_args
        headers = call_args[1].get("headers", {})
        assert "User-Agent" in headers

    @patch("requests.get")
    def test_returns_empty_for_empty_api_response(self, mock_get):
        """Empty Nominatim response should return []."""
        mock_get.return_value = _make_requests_response([])

        results = api_module.geocode_locations("nowhere", limit=5)
        assert results == []
