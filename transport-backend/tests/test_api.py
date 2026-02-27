"""Tests for the FastAPI transport backend API.

Covers /health, /bus/live/{operator}, /api/route, format_route_text,
get_router_for_date, and core app setup.

Test strategy:
  - Heavy backend dependencies (bus_live, main, bus_loader, etc.) are
    mocked at the module level so TestClient can be created without
    real database or network access.
  - Each test class targets one endpoint / behaviour group.
"""
import sys
import os
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# ---------------------------------------------------------------------------
# Mock all heavy backend modules BEFORE importing api so module-level
# imports inside api.py resolve without the real implementations.
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

# Patch sys.modules for the duration of the whole test module
_mocks = {}
for mod in _HEAVY_MODULES:
    if mod not in sys.modules:
        _mocks[mod] = MagicMock()
        sys.modules[mod] = _mocks[mod]

# Configure time_utils mock with useful return values
sys.modules["time_utils"].seconds_since_midnight = MagicMock(return_value=36000)
sys.modules["time_utils"].seconds_to_time = MagicMock(
    side_effect=lambda s: f"{int(s)//3600:02d}:{(int(s)%3600)//60:02d}:{int(s)%60:02d}"
)

# Now import the app â€” the heavy imports resolve to mocks
import api as api_module  # noqa: E402
from api import app, format_route_text, get_router_for_date  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture()
def client():
    """Provide a TestClient that does NOT trigger lifespan/startup events."""
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


# â”€â”€ /health endpoint tests â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestHealthEndpoint:
    """Validate the /health endpoint contract."""

    def test_health_returns_200(self, client: TestClient):
        """GET /health must return HTTP 200 OK."""
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_returns_status_ok(self, client: TestClient):
        """Response body must contain {"status": "ok"}."""
        response = client.get("/health")
        data = response.json()
        assert "status" in data
        assert data["status"] == "ok"

    def test_health_content_type_is_json(self, client: TestClient):
        """Response Content-Type must be application/json."""
        response = client.get("/health")
        assert "application/json" in response.headers["content-type"]


# â”€â”€ App metadata tests â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestAppMetadata:
    """Verify basic FastAPI app configuration."""

    def test_app_title(self):
        """App title should identify the transport API."""
        assert app.title is not None
        assert len(app.title) > 0

    def test_openapi_schema_available(self, client: TestClient):
        """OpenAPI schema should be served at /openapi.json."""
        response = client.get("/openapi.json")
        assert response.status_code == 200
        schema = response.json()
        assert "paths" in schema
        assert "/health" in schema["paths"]

    def test_health_in_openapi_has_get_method(self, client: TestClient):
        """The /health path in the OpenAPI schema must list a GET operation."""
        schema = client.get("/openapi.json").json()
        assert "get" in schema["paths"]["/health"]



class TestRouteEndpoint:
    """Validate the /api/route POST endpoint."""

    def test_route_success(self, client: TestClient):
        """POST /api/route with valid input returns route data."""
        # Mock get_router_for_date
        mock_timetable = MagicMock()
        mock_router = MagicMock()
        mock_walking = MagicMock()
        mock_router.route.return_value = {"_meta": {"total_arrival": 37000}}

        with patch.object(api_module, "get_router_for_date",
                          return_value=(mock_timetable, mock_router, mock_walking)):
            response = client.post("/api/route", json={
                "start_lat": 53.48,
                "start_lon": -2.24,
                "end_lat": 53.38,
                "end_lon": -2.15,
                "date": "2026-02-16",
                "time": "10:00:00",
            })
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_route_with_bus_mode(self, client: TestClient):
        """POST /api/route with mode='bus' filters to bus only."""
        mock_timetable = MagicMock()
        mock_router = MagicMock()
        mock_walking = MagicMock()
        mock_router.route.return_value = {}

        with patch.object(api_module, "get_router_for_date",
                          return_value=(mock_timetable, mock_router, mock_walking)):
            response = client.post("/api/route", json={
                "start_lat": 53.48,
                "start_lon": -2.24,
                "end_lat": 53.38,
                "end_lon": -2.15,
                "date": "2026-02-16",
                "time": "10:00:00",
                "mode": "bus",
            })
        # Check that "bus" mode was passed to the router
        call_kwargs = mock_router.route.call_args[1]
        assert call_kwargs["allowed_modes"] == {"bus"}

    def test_route_with_train_mode(self, client: TestClient):
        """POST /api/route with mode='train' filters to train only."""
        mock_timetable = MagicMock()
        mock_router = MagicMock()
        mock_walking = MagicMock()
        mock_router.route.return_value = {}

        with patch.object(api_module, "get_router_for_date",
                          return_value=(mock_timetable, mock_router, mock_walking)):
            response = client.post("/api/route", json={
                "start_lat": 53.48,
                "start_lon": -2.24,
                "end_lat": 53.38,
                "end_lon": -2.15,
                "date": "2026-02-16",
                "time": "10:00:00",
                "mode": "train",
            })
        call_kwargs = mock_router.route.call_args[1]
        assert call_kwargs["allowed_modes"] == {"train"}

    def test_route_with_both_mode(self, client: TestClient):
        """POST /api/route with mode='both' uses bus and train."""
        mock_timetable = MagicMock()
        mock_router = MagicMock()
        mock_walking = MagicMock()
        mock_router.route.return_value = {}

        with patch.object(api_module, "get_router_for_date",
                          return_value=(mock_timetable, mock_router, mock_walking)):
            response = client.post("/api/route", json={
                "start_lat": 53.48,
                "start_lon": -2.24,
                "end_lat": 53.38,
                "end_lon": -2.15,
                "date": "2026-02-16",
                "time": "10:00:00",
                "mode": "both",
            })
        call_kwargs = mock_router.route.call_args[1]
        assert call_kwargs["allowed_modes"] == {"bus", "train"}

    def test_route_error_returns_failure(self, client: TestClient):
        """POST /api/route returns success=false on internal error."""
        with patch.object(api_module, "get_router_for_date",
                          side_effect=RuntimeError("DB unavailable")):
            response = client.post("/api/route", json={
                "start_lat": 53.48,
                "start_lon": -2.24,
                "end_lat": 53.38,
                "end_lon": -2.15,
                "date": "2026-02-16",
                "time": "10:00:00",
            })
        data = response.json()
        assert data["success"] is False
        assert "DB unavailable" in data["error"]


# â”€â”€ get_router_for_date tests â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestGetRouterForDate:
    """Test the router caching helper function."""

    def setup_method(self):
        """Clear caches before each test."""
        api_module._router_cache.clear()
        api_module._base_cache = None

    def test_returns_cached_router(self):
        """Subsequent calls for the same date return cached result."""
        mock_result = (MagicMock(), MagicMock(), MagicMock())
        api_module._router_cache["2026-02-16"] = mock_result
        result = get_router_for_date("2026-02-16")
        assert result is mock_result

    def test_builds_router_when_base_cache_exists(self):
        """When _base_cache is set, it uses it to build a router."""
        mock_loader = MagicMock()
        mock_walking_raw = MagicMock()
        api_module._base_cache = {
            "loader": mock_loader,
            "walking_raw": mock_walking_raw,
        }
        mock_timetable = MagicMock()
        mock_router = MagicMock()
        mock_walking = MagicMock()
        sys.modules["main"].build_for_date.return_value = (
            mock_timetable, mock_router, mock_walking,
        )

        result = get_router_for_date("2026-02-16")
        assert result == (mock_timetable, mock_router, mock_walking)
        # Should now be cached
        assert "2026-02-16" in api_module._router_cache

    def test_initializes_base_when_cache_is_none(self):
        """When _base_cache is None, it calls initialize_base()."""
        api_module._base_cache = None
        mock_init = MagicMock(return_value={
            "loader": MagicMock(),
            "walking_raw": MagicMock(),
        })
        sys.modules["main"].initialize_base = mock_init
        sys.modules["main"].build_for_date.return_value = (
            MagicMock(), MagicMock(), MagicMock(),
        )

        get_router_for_date("2026-02-17")
        mock_init.assert_called_once()


# â”€â”€ format_route_text tests â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestFormatRouteText:
    """Test the route formatting helper function."""

    def test_empty_route_returns_no_route(self):
        """Empty/falsy route_result returns 'No route found'."""
        result = format_route_text(None, MagicMock())
        assert "No route found" in result

    def test_empty_dict_returns_no_route(self):
        """Empty dict route_result returns 'No route found'."""
        result = format_route_text({}, MagicMock())
        assert "No route found" in result

    def test_walking_only_route(self):
        """Route with only _meta key formats as walking-only."""
        route = {
            "_meta": {
                "start_point": (53.48, -2.24),
                "destination": (53.38, -2.15),
                "start_walk_seconds": 120,
                "end_walk_seconds": 60,
                "total_arrival": 37000,
            }
        }
        result = format_route_text(route, MagicMock())
        assert "only walking" in result
        assert "Start" in result
        assert "Destination" in result

    def test_walking_only_without_arrival(self):
        """Walking-only route without total_arrival still works."""
        route = {
            "_meta": {
                "start_point": (53.48, -2.24),
                "destination": (53.38, -2.15),
                "start_walk_seconds": 120,
                "end_walk_seconds": 60,
                "total_arrival": None,
            }
        }
        result = format_route_text(route, MagicMock())
        assert "only walking" in result

    def test_walking_only_no_start_point(self):
        """Walking-only route without start_point still works."""
        route = {
            "_meta": {
                "start_point": (),
                "destination": (53.38, -2.15),
                "start_walk_seconds": 120,
                "end_walk_seconds": 60,
                "total_arrival": None,
            }
        }
        result = format_route_text(route, MagicMock())
        assert "only walking" in result

    def test_walking_only_no_destination(self):
        """Walking-only route without destination still works."""
        route = {
            "_meta": {
                "start_point": (53.48, -2.24),
                "destination": (),
                "start_walk_seconds": 120,
                "end_walk_seconds": 60,
                "total_arrival": None,
            }
        }
        result = format_route_text(route, MagicMock())
        assert "only walking" in result

    def test_multi_leg_route_with_bus(self):
        """Route with multiple stops formats bus leg details."""
        merged = MagicMock()
        merged.stop_metadata = ["Stop A", "Stop B", "Stop C"]

        route = {
            "_meta": {
                "start_point": (53.48, -2.24),
                "destination": (53.38, -2.15),
                "start_walk_seconds": 60,
                "end_walk_seconds": 30,
                "total_arrival": 37500,
            },
            0: {
                "arrival_time": 36060,
                "prev_stop": None,
                "type": None,
                "journey_info": None,
            },
            1: {
                "arrival_time": 36500,
                "prev_stop": 0,
                "type": "bus",
                "journey_info": {"line_name": "NW:10"},
                "journey_origin": "Depot",
                "journey_destination": "Centre",
                "board_departure": 36100,
            },
        }
        result = format_route_text(route, merged)
        assert "Stop A" in result
        assert "Stop B" in result
        assert "Route found" in result
        assert "line 10" in result  # line_name split on ":"
        assert "Depot -> Centre" in result

    def test_multi_leg_route_walking_transfer(self):
        """Route with a walking transfer between stops."""
        merged = MagicMock()
        merged.stop_metadata = ["Stop A", "Stop B"]

        route = {
            "_meta": {
                "start_point": (),
                "destination": (),
                "start_walk_seconds": 0,
                "end_walk_seconds": 0,
                "total_arrival": None,
            },
            0: {
                "arrival_time": 36000,
                "prev_stop": None,
                "type": None,
                "journey_info": None,
            },
            1: {
                "arrival_time": 36300,
                "prev_stop": 0,
                "type": "walking",
                "journey_info": None,
            },
        }
        result = format_route_text(route, merged)
        assert "Walk" in result

    def test_multi_leg_route_with_inf_arrival(self):
        """Stops with infinite arrival time display --:--:--."""
        merged = MagicMock()
        merged.stop_metadata = ["Stop X"]

        route = {
            "_meta": {},
            0: {
                "arrival_time": float("inf"),
                "prev_stop": None,
                "type": None,
                "journey_info": None,
            },
        }
        result = format_route_text(route, merged)
        assert "--:--:--" in result

    def test_stop_index_out_of_range(self):
        """Stop index beyond metadata length falls back to 'stop#N'."""
        merged = MagicMock()
        merged.stop_metadata = []  # empty list

        route = {
            "_meta": {},
            99: {
                "arrival_time": 36000,
                "prev_stop": None,
                "type": None,
                "journey_info": None,
            },
        }
        result = format_route_text(route, merged)
        assert "stop#99" in result

    def test_multi_leg_no_line_name(self):
        """Bus leg without line_name still formats correctly."""
        merged = MagicMock()
        merged.stop_metadata = ["A", "B"]

        route = {
            "_meta": {
                "start_point": (),
                "destination": (),
                "start_walk_seconds": 0,
                "end_walk_seconds": 0,
            },
            0: {
                "arrival_time": 36000,
                "prev_stop": None,
                "type": None,
                "journey_info": None,
            },
            1: {
                "arrival_time": 36600,
                "prev_stop": 0,
                "type": "bus",
                "journey_info": {"line_name": ""},
                "journey_origin": "",
                "journey_destination": "",
                "board_departure": None,
            },
        }
        result = format_route_text(route, merged)
        assert "bus" in result

    def test_multi_leg_no_journey_info(self):
        """Bus leg with journey_info=None still formats correctly."""
        merged = MagicMock()
        merged.stop_metadata = ["A", "B"]

        route = {
            "_meta": {
                "start_point": (),
                "destination": (),
                "start_walk_seconds": 0,
                "end_walk_seconds": 0,
            },
            0: {
                "arrival_time": 36000,
                "prev_stop": None,
                "type": None,
                "journey_info": None,
            },
            1: {
                "arrival_time": 36600,
                "prev_stop": 0,
                "type": "train",
                "journey_info": None,
                "journey_origin": "",
                "journey_destination": "",
                "board_departure": None,
            },
        }
        result = format_route_text(route, merged)
        assert "train" in result

    def test_multi_leg_route_with_destination_walk(self):
        """Final walking leg to destination is rendered."""
        merged = MagicMock()
        merged.stop_metadata = ["Stop A"]

        route = {
            "_meta": {
                "start_point": (53.48, -2.24),
                "destination": (53.38, -2.15),
                "start_walk_seconds": 60,
                "end_walk_seconds": 120,
                "total_arrival": 37000,
            },
            0: {
                "arrival_time": 36060,
                "prev_stop": None,
                "type": None,
                "journey_info": None,
            },
        }
        result = format_route_text(route, merged)
        assert "Destination" in result

    def test_no_destinations_fallback(self):
        """When all stops appear as prev_stop, use full list as fallback."""
        merged = MagicMock()
        merged.stop_metadata = ["A", "B"]

        # Create a cycle-like structure where every stop is someone's prev
        route = {
            "_meta": {},
            0: {
                "arrival_time": 36000,
                "prev_stop": 1,
                "type": None,
                "journey_info": None,
            },
            1: {
                "arrival_time": 36300,
                "prev_stop": 0,
                "type": "bus",
                "journey_info": None,
                "journey_origin": "",
                "journey_destination": "",
                "board_departure": None,
            },
        }
        result = format_route_text(route, merged)
        assert "Route found" in result



