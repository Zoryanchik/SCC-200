"""Tests for time_utils module.

Covers seconds_since_midnight, seconds_to_time, parse_iso8601_duration,
add_delay, get_next_day_time, is_operational_day, three_day_window,
operates_in_3day_window, time_between_stops, calculate_travel_time,
format_duration, and TimeRange class.
"""
import os
import sys
import importlib
import pytest

backend_dir = os.path.join(os.path.dirname(__file__), "..")
if backend_dir not in sys.path:
    sys.path.insert(0, os.path.abspath(backend_dir))

# Ensure we have the REAL time_utils, not a mock left by other tests
_stale = sys.modules.pop("time_utils", None)
import time_utils as _real_time_utils  # noqa: E402
sys.modules["time_utils"] = _real_time_utils

from time_utils import (
    seconds_since_midnight,
    seconds_to_time,
    parse_iso8601_duration,
    add_delay,
    get_next_day_time,
    is_operational_day,
    three_day_window,
    operates_in_3day_window,
    time_between_stops,
    calculate_travel_time,
    format_duration,
    TimeRange,
)


# ── seconds_since_midnight ───────────────────────────────────────────

class TestSecondsSinceMidnight:
    def test_midnight(self):
        assert seconds_since_midnight("00:00:00") == 0

    def test_noon(self):
        assert seconds_since_midnight("12:00:00") == 43200

    def test_end_of_day(self):
        assert seconds_since_midnight("23:59:59") == 86399

    def test_arbitrary(self):
        assert seconds_since_midnight("12:30:45") == 45045

    def test_single_digit_hour(self):
        assert seconds_since_midnight("1:00:00") == 3600


# ── seconds_to_time ──────────────────────────────────────────────────

class TestSecondsToTime:
    def test_midnight(self):
        assert seconds_to_time(0) == "00:00:00"

    def test_noon(self):
        assert seconds_to_time(43200) == "12:00:00"

    def test_end_of_day(self):
        assert seconds_to_time(86399) == "23:59:59"

    def test_wraps_past_midnight(self):
        # 86400 + 3600 = 90000 → should wrap to 01:00:00
        assert seconds_to_time(90000) == "01:00:00"

    def test_arbitrary(self):
        assert seconds_to_time(45045) == "12:30:45"


# ── parse_iso8601_duration ───────────────────────────────────────────

class TestParseISO8601Duration:
    def test_full_duration(self):
        assert parse_iso8601_duration("PT1H30M45S") == 5445

    def test_seconds_only(self):
        assert parse_iso8601_duration("PT30S") == 30

    def test_hours_only(self):
        assert parse_iso8601_duration("PT2H") == 7200

    def test_minutes_only(self):
        assert parse_iso8601_duration("PT45M") == 2700

    def test_invalid_format(self):
        assert parse_iso8601_duration("INVALID") == 0

    def test_empty_string(self):
        assert parse_iso8601_duration("") == 0


# ── add_delay ────────────────────────────────────────────────────────

class TestAddDelay:
    def test_positive_delay(self):
        assert add_delay(43200, 300) == 43500

    def test_negative_delay(self):
        assert add_delay(43200, -300) == 42900

    def test_zero_delay(self):
        assert add_delay(43200, 0) == 43200


# ── get_next_day_time ────────────────────────────────────────────────

class TestGetNextDayTime:
    def test_within_day(self):
        assert get_next_day_time(43200) == (43200, 0)

    def test_exact_midnight(self):
        assert get_next_day_time(86400) == (0, 1)

    def test_past_midnight(self):
        assert get_next_day_time(90000) == (3600, 1)


# ── is_operational_day ───────────────────────────────────────────────

class TestIsOperationalDay:
    def test_saturday_in_weekday_plus_saturday(self):
        # 2026-01-31 is a Saturday
        assert is_operational_day("2026-01-31", "MTWRFX") is True

    def test_sunday_not_in_weekday_set(self):
        # 2026-02-01 is a Sunday
        assert is_operational_day("2026-02-01", "MTWRFX") is False

    def test_sunday_in_sunday_set(self):
        assert is_operational_day("2026-02-01", "S") is True

    def test_monday(self):
        # 2026-02-02 is a Monday
        assert is_operational_day("2026-02-02", "M") is True

    def test_empty_pattern(self):
        assert is_operational_day("2026-02-02", "") is False


# ── three_day_window ─────────────────────────────────────────────────

class TestThreeDayWindow:
    def test_center_mode(self):
        result = three_day_window("2026-02-05")
        assert result == ["2026-02-04", "2026-02-05", "2026-02-06"]

    def test_start_mode(self):
        result = three_day_window("2026-02-05", mode="start")
        assert result == ["2026-02-05", "2026-02-06", "2026-02-07"]

    def test_invalid_date_raises(self):
        with pytest.raises(ValueError):
            three_day_window("not-a-date")

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError):
            three_day_window("2026-02-05", mode="invalid")


# ── operates_in_3day_window ──────────────────────────────────────────

class TestOperatesIn3DayWindow:
    def test_operates_on_center_day(self):
        # 2026-02-05 is a Thursday (R)
        assert operates_in_3day_window("2026-02-05", "R") is True

    def test_does_not_operate(self):
        # If we check a window where none of the 3 days match
        assert operates_in_3day_window("2026-02-05", "S") is False


# ── time_between_stops ───────────────────────────────────────────────

class TestTimeBetweenStops:
    def test_normal(self):
        assert time_between_stops(43200, 43230) == 30

    def test_crosses_midnight(self):
        assert time_between_stops(86300, 100) == 200


# ── calculate_travel_time ────────────────────────────────────────────

class TestCalculateTravelTime:
    def test_normal(self):
        assert calculate_travel_time(43200, 43800) == 600

    def test_crosses_midnight(self):
        assert calculate_travel_time(82800, 3600) == 7200


# ── format_duration ──────────────────────────────────────────────────

class TestFormatDuration:
    def test_full(self):
        assert format_duration(5445) == "1h 30m 45s"

    def test_seconds_only(self):
        assert format_duration(45) == "45s"

    def test_hours_only(self):
        assert format_duration(3600) == "1h"

    def test_zero(self):
        assert format_duration(0) == "0s"

    def test_minutes_only(self):
        assert format_duration(120) == "2m"


# ── TimeRange ────────────────────────────────────────────────────────

class TestTimeRange:
    def test_contains_within(self):
        tr = TimeRange(36000, 43200)
        assert tr.contains(40000) is True

    def test_contains_outside(self):
        tr = TimeRange(36000, 43200)
        assert tr.contains(50000) is False

    def test_contains_wraps_midnight(self):
        tr = TimeRange(82800, 3600)  # 23:00 to 01:00
        assert tr.contains(0) is True
        assert tr.contains(86000) is True
        assert tr.contains(43200) is False

    def test_overlap_true(self):
        tr1 = TimeRange(36000, 43200)
        tr2 = TimeRange(40000, 50000)
        assert tr1.overlap(tr2) is True

    def test_overlap_false(self):
        tr1 = TimeRange(36000, 37000)
        tr2 = TimeRange(40000, 50000)
        assert tr1.overlap(tr2) is False

    def test_str(self):
        tr = TimeRange(0, 3600)
        assert "00:00:00" in str(tr)
        assert "01:00:00" in str(tr)
