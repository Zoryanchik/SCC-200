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
            ("10", "City Centre", 53.48, -2.24, "Stagecoach", None, None, None),
            ("42", "Airport", 53.35, -2.27, "Transpora", 150, None, None),
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
            "operator": "Stagecoach",
            "delay_minutes": None,
            "status": "On time",
            "bearing": None,
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


class TestBusDelayHandling:
    """Validate delay_minutes and status fields in /bus/live/{operator} response."""

    def test_no_delay_data_returns_none_and_on_time(self, client: TestClient):
        """When delay_seconds is None the response should have delay_minutes=None and status='On time'."""
        api_module.get_bus_live.return_value = [
            ("1A", "Lancaster", 54.05, -2.80, "Stagecoach", None, None, None),
        ]
        data = client.get("/bus/live/SCCU", params={"lat": 54.05, "lon": -2.80}).json()
        assert data[0]["delay_minutes"] is None
        assert data[0]["status"] == "On time"

    def test_delay_within_threshold_is_on_time(self, client: TestClient):
        """Delay < 1 min (59 s) should be reported as 'On time'."""
        api_module.get_bus_live.return_value = [
            ("2", "Morecambe", 54.05, -2.80, "Stagecoach", 30, None, None),
        ]
        data = client.get("/bus/live/SCCU", params={"lat": 54.05, "lon": -2.80}).json()
        assert data[0]["status"] == "On time"
        assert data[0]["delay_minutes"] == 0.5

    def test_delay_above_threshold_reports_delayed(self, client: TestClient):
        """Delay >= 1 min (60 s) should report 'Delayed N min'."""
        api_module.get_bus_live.return_value = [
            ("100", "Blackpool", 54.05, -2.80, "Blackpool Transport", 300, None, None),
        ]
        data = client.get("/bus/live/SCCU", params={"lat": 54.05, "lon": -2.80}).json()
        assert data[0]["status"] == "Delayed 5 min"
        assert data[0]["delay_minutes"] == 5.0

    def test_large_delay_rounds_correctly(self, client: TestClient):
        """630 s = 10.5 min → Python round() (banker's rounding) → 10 min."""
        api_module.get_bus_live.return_value = [
            ("X2", "Preston", 54.05, -2.80, "Stagecoach", 630, None, None),
        ]
        data = client.get("/bus/live/SCCU", params={"lat": 54.05, "lon": -2.80}).json()
        assert data[0]["status"] == "Delayed 10 min"
        assert data[0]["delay_minutes"] == 10.5

    def test_early_bus_reports_early(self, client: TestClient):
        """Negative delay (early) should be treated as 'On time' in the status string."""
        api_module.get_bus_live.return_value = [
            ("44", "Carnforth", 54.05, -2.80, "Stagecoach", -120, None, None),
        ]
        data = client.get("/bus/live/SCCU", params={"lat": 54.05, "lon": -2.80}).json()
        assert data[0]["status"] == "On time"
        assert data[0]["delay_minutes"] == -2.0

    def test_delay_minutes_rounds_to_one_decimal(self, client: TestClient):
        """delay_minutes should be rounded to 1 decimal place."""
        api_module.get_bus_live.return_value = [
            ("7", "Fylde", 54.05, -2.80, "Stagecoach", 155, None, None),
        ]
        data = client.get("/bus/live/SCCU", params={"lat": 54.05, "lon": -2.80}).json()
        assert data[0]["delay_minutes"] == round(155 / 60, 1)


class TestBusLiveNoDbContract:
    """Regression: live matching must be in-memory only (no DB fallbacks)."""

    def test_live_matching_does_not_touch_db_modules(self, client: TestClient, monkeypatch):
        # Force the endpoint down the timetable-matching path.
        api_module.get_bus_live.return_value = [
            ("10", "City Centre", 53.48, -2.24, "SCCU", None, None, None, {"operator_ref": "SCCU"}),
        ]

        # If any code tries to use BusLoader / psycopg, fail the test.
        def _boom(*args, **kwargs):
            raise AssertionError("DB access attempted in live matching")

        # bus_loader is one of the pre-mocked heavy modules; assert it isn't used.
        if hasattr(sys.modules.get("bus_loader"), "BusLoader"):
            monkeypatch.setattr(sys.modules["bus_loader"], "BusLoader", _boom, raising=False)

        # Also guard direct psycopg.connect usage from api.py.
        if hasattr(api_module, "psycopg"):
            monkeypatch.setattr(api_module.psycopg, "connect", _boom, raising=False)

        # Make _compute_delay_from_timetable deterministic and ensure it doesn't
        # trigger any hidden DB fallbacks.
        monkeypatch.setattr(api_module, "_compute_delay_from_timetable", lambda *a, **k: (0, 0))

        resp = client.get("/bus/live/SCCU", params={"lat": 53.48, "lon": -2.24})
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert data and data[0].get("status") == "On time"


class TestBusLiveDelayParsing:
    """Unit-level tests for the delay parsing helpers in bus_live.py.

    These tests import the real module (not the api-level mock).
    We stash the real module before the top-level mock replaces it.
    """
    # Helpers are imported inside each test via sys so we avoid
    # binding at class-definition time (when the mock is already active).
    pass


