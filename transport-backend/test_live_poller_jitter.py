"""Tests for live DB poller minute-schedule jitter behaviour."""

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


class _FakeStopEvent:
    def __init__(self, stop_after_waits: int = 2):
        self._set = False
        self._stop_after_waits = stop_after_waits
        self.wait_calls = []

    def is_set(self):
        return self._set

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        if len(self.wait_calls) >= self._stop_after_waits:
            self._set = True
        return self._set


def test_live_poller_adds_jitter_to_next_minute_schedule(monkeypatch):
    monkeypatch.setenv("BUS_LIVE_DB_STAGGER_S", "120")
    monkeypatch.setenv("BUS_LIVE_DB_OPERATOR_PERIOD_S", "60")
    monkeypatch.setenv("BUS_LIVE_DB_CYCLE_JITTER_S", "2")

    monkeypatch.setattr(api_module, "_live_endpoints_disabled", lambda: False)
    monkeypatch.setattr(api_module, "is_routing_active", lambda: False)
    monkeypatch.setattr(api_module, "_live_bus_operators_from_env", lambda: ["SCCU"])

    poll_calls = []

    def _fake_get_bus_live(*args, **kwargs):
        poll_calls.append((args, kwargs))
        return []

    monkeypatch.setattr(api_module, "get_bus_live", _fake_get_bus_live)
    monkeypatch.setattr(api_module, "_store_live_bus_rows", lambda _op, _raw: 0)
    monkeypatch.setattr(api_module.random, "uniform", lambda _a, _b: 0.4)

    # First loop polls immediately at t=100, then schedules next due for
    # t=101 + max(60,120) + 0.4 = 221.4; second loop at t=120 should sleep 101.4s.
    t_values = iter([100.0, 101.0, 120.0])
    monkeypatch.setattr(api_module.time, "time", lambda: next(t_values))

    stop_event = _FakeStopEvent(stop_after_waits=2)
    api_module._live_bus_db_poller_loop(stop_event)

    assert len(poll_calls) == 1
    # First wait is fixed stagger after the poll.
    assert stop_event.wait_calls[0] == 120.0
    # Second wait is computed from next_due and includes jitter.
    assert stop_event.wait_calls[1] == 101.4
