"""Focused tests for DB-backed live bus row reads in api.py."""

import sys
from unittest.mock import MagicMock


# Mock heavy backend modules BEFORE importing api
_HEAVY_MODULES = [
    "bus_live", "bus_loader", "bus_data", "main", "time_utils",
    "timetable", "walking", "merged_data", "raptor_router",
    "dense_mapper", "train_data",
]
for mod in _HEAVY_MODULES:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

import api as api_module  # noqa: E402


class _FakeCursor:
    def __init__(self, rows, calls):
        self._rows = rows
        self._calls = calls

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False

    def execute(self, query, params):
        self._calls.append((query, params))

    def fetchall(self):
        return list(self._rows)


class _FakeConn:
    def __init__(self, rows, calls):
        self._rows = rows
        self._calls = calls

    def cursor(self):
        return _FakeCursor(self._rows, self._calls)

    def close(self):
        return None


def test_read_live_bus_rows_apply_bbox_false_bypasses_geofence(monkeypatch):
    calls = []
    rows = [
        (
            "SCC:1",
            "City Centre",
            55.0,
            -3.0,
            "SCC",
            90,
            36000,
            180.0,
            '{"vehicle_ref":"veh-1"}',
        )
    ]

    monkeypatch.setattr(api_module, "_live_bus_db_dsn", lambda: "postgres://fake")
    monkeypatch.setattr(api_module.psycopg, "connect", lambda _dsn: _FakeConn(rows, calls))

    out = api_module._read_live_bus_rows(
        operator="all",
        lat=54.0,
        lon=-2.8,
        lat_tol=0.01,
        lon_tol=0.01,
        keep_vehicle_id=None,
        max_age_s=120,
        apply_bbox=False,
    )

    assert len(out) == 1
    assert out[0][0] == "SCC:1"
    assert out[0][-1]["vehicle_ref"] == "veh-1"

    assert len(calls) == 1
    _query, params = calls[0]
    # Parameter inserted for `%s = FALSE` guard in SQL clause.
    assert params[3] is False


def test_read_live_bus_rows_default_keeps_bbox_filter(monkeypatch):
    calls = []
    rows = []

    monkeypatch.setattr(api_module, "_live_bus_db_dsn", lambda: "postgres://fake")
    monkeypatch.setattr(api_module.psycopg, "connect", lambda _dsn: _FakeConn(rows, calls))

    api_module._read_live_bus_rows(
        operator="SCC",
        lat=54.0,
        lon=-2.8,
        lat_tol=0.5,
        lon_tol=0.5,
        keep_vehicle_id=None,
        max_age_s=120,
    )

    assert len(calls) == 1
    _query, params = calls[0]
    assert params[3] is True
