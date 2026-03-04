"""Unit tests for bus delay-parsing helpers in bus_live.py.

These tests load bus_live directly by file path to avoid any sys.modules
mock that test_bus_live.py may have installed for API-level fixtures.
"""
import importlib.util
import os
import sys

# Load the real bus_live module from disk, independent of sys.modules cache
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BUS_LIVE_PATH = os.path.join(_BACKEND_DIR, "bus_live.py")

_spec = importlib.util.spec_from_file_location("_real_bus_live", _BUS_LIVE_PATH)
_real_bus_live = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_real_bus_live)

_parse_iso_duration = _real_bus_live._parse_iso_duration
_parse_iso_dt = _real_bus_live._parse_iso_dt


class TestParseIsoDuration:
    """Tests for _parse_iso_duration(s) → int | None."""

    def test_positive_minutes_seconds(self):
        assert _parse_iso_duration("PT2M30S") == 150

    def test_minutes_only(self):
        assert _parse_iso_duration("PT5M") == 300

    def test_seconds_only(self):
        assert _parse_iso_duration("PT90S") == 90

    def test_hours_minutes(self):
        assert _parse_iso_duration("PT1H30M") == 5400

    def test_hours_minutes_seconds(self):
        assert _parse_iso_duration("PT1H2M3S") == 3723

    def test_zero(self):
        assert _parse_iso_duration("PT0S") == 0

    def test_negative_minutes(self):
        assert _parse_iso_duration("-PT1M") == -60

    def test_negative_seconds(self):
        assert _parse_iso_duration("-PT30S") == -30

    def test_empty_string_returns_none(self):
        assert _parse_iso_duration("") is None

    def test_unparseable_returns_none(self):
        assert _parse_iso_duration("invalid") is None

    def test_fractional_seconds(self):
        # PT2.5S → 2 seconds (int cast)
        result = _parse_iso_duration("PT2.5S")
        assert result == 2


class TestParseIsoDt:
    """Tests for _parse_iso_dt(s) → datetime | None."""

    def test_utc_z_suffix(self):
        dt = _parse_iso_dt("2026-03-04T10:30:00Z")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 3
        assert dt.day == 4
        assert dt.hour == 10
        assert dt.minute == 30
        assert dt.tzinfo is not None

    def test_offset_aware(self):
        dt = _parse_iso_dt("2026-03-04T10:30:00+00:00")
        assert dt is not None
        assert dt.hour == 10

    def test_bst_offset(self):
        dt = _parse_iso_dt("2026-03-04T11:30:00+01:00")
        assert dt is not None
        assert dt.hour == 11

    def test_empty_returns_none(self):
        assert _parse_iso_dt("") is None

    def test_invalid_string_returns_none(self):
        assert _parse_iso_dt("not-a-date") is None

    def test_none_like_empty(self):
        assert _parse_iso_dt(None) is None
