"""Tests for the /bus/live/{operator} endpoint.

Mocks bus_live.get_bus_live to avoid network requests and
validates response shape and query parameter handling.
"""
import sys
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Mock heavy backend modules BEFORE importing api so module-level
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

_mocks = {}
for mod in _HEAVY_MODULES:
    if mod not in sys.modules:
        _mocks[mod] = MagicMock()
        sys.modules[mod] = _mocks[mod]

sys.modules["time_utils"].seconds_since_midnight = MagicMock(return_value=36000)
sys.modules["time_utils"].seconds_to_time = MagicMock(
    side_effect=lambda s: f"{int(s)//3600:02d}:{(int(s)%3600)//60:02d}:{int(s)%60:02d}"
)

# Configure bus_live mock with get_bus_live function
sys.modules["bus_live"].get_bus_live = MagicMock()
sys.modules["bus_live"].BusLive = MagicMock()

import api as api_module  # noqa: E402
from api import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture()
def client():
    """Provide a TestClient that does NOT trigger lifespan/startup events."""
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c



@pytest.fixture(autouse=True)
def reset_get_bus_live():
    """Reset get_bus_live mock between tests."""
    api_module.get_bus_live.reset_mock()
    api_module.get_bus_live.return_value = []
    yield

class TestBusLiveOperatorEndpoint:
    """Validate GET /bus/live/{operator} behaviour."""

    def test_requires_lat_lon(self, client: TestClient):
        response = client.get("/bus/live/ARCT")
        assert response.status_code == 400

    def test_returns_expected_shape(self, client: TestClient):
        api_module.get_bus_live.return_value = [
            ("10", "City Centre", 53.48, -2.24, "Stagecoach"),
            ("42", "Airport", 53.35, -2.27, "Transpora"),
        ]

        response = client.get(
            "/bus/live/ARCT",
            params={"lat": 53.48, "lon": -2.24},
        )
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert data[0] == {
            "line": "10",
            "destination": "City Centre",
            "lat": 53.48,
            "lon": -2.24,
        }

    def test_operator_builds_url(self, client: TestClient):
        api_module.get_bus_live.return_value = []

        client.get(
            "/bus/live/ARCT",
            params={"lat": 53.48, "lon": -2.24},
        )

        api_module.get_bus_live.assert_called_once_with(
            53.48,
            -2.24,
            urls=["https://transport.scc.lancs.ac.uk/bus/live/ARCT"],
            lat_tol=0.0003,
            lon_tol=0.0003,
        )

    def test_all_operator_uses_default_urls(self, client: TestClient):
        api_module.get_bus_live.return_value = []

        client.get(
            "/bus/live/all",
            params={"lat": 53.48, "lon": -2.24},
        )

        api_module.get_bus_live.assert_called_once_with(
            53.48,
            -2.24,
            urls=None,
            lat_tol=0.0003,
            lon_tol=0.0003,
        )

    def test_passes_tolerances(self, client: TestClient):
        api_module.get_bus_live.return_value = []

        client.get(
            "/bus/live/SCCU",
            params={"lat": 53.48, "lon": -2.24, "latTol": 0.01, "lonTol": 0.02},
        )

        api_module.get_bus_live.assert_called_once_with(
            53.48,
            -2.24,
            urls=["https://transport.scc.lancs.ac.uk/bus/live/SCCU"],
            lat_tol=0.01,
            lon_tol=0.02,
        )

    def test_empty_results_return_empty_list(self, client: TestClient):
        api_module.get_bus_live.return_value = []

        response = client.get(
            "/bus/live/ARCT",
            params={"lat": 53.48, "lon": -2.24},
        )
        assert response.status_code == 200
        assert response.json() == []

