"""Tests for off-track gating exception: between two far-apart stops.

We test api._compute_delay_from_timetable at a unit level by stubbing
get_router_for_date and providing a minimal merged/router/walking.

New rule being tested:
- If the vehicle is further than MATCH_MAX_TRACK_DIST_M from the track, the
  candidate is normally rejected as off-track.
- Exception: if the vehicle lies plausibly between two consecutive stops that
  are far apart (gap >= MATCH_BETWEEN_STOPS_MIN_GAP_M) and its perpendicular
  distance to that stop-to-stop segment is small (<= MATCH_BETWEEN_STOPS_MAX_PERP_M),
  do NOT reject it as off-track.

We keep the geometry simple and deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import os
import sys
from types import SimpleNamespace

# Ensure we import the transport-backend's local `api.py` module, not an unrelated
# top-level `api` package that might be installed in the environment.
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import api as api_module


@dataclass
class _DummyWalking:
    stop_coords: dict[int, tuple[float, float]]

    def get_loc_coords(self, stop_int: int):
        return self.stop_coords.get(stop_int)

    def reachable_stops(self, latlon):
        # Return all stops — tests are about time/offtrack gates, and we want
        # to avoid early aborts due to reachability heuristics.
        try:
            return [(int(sid), 0) for sid in self.stop_coords.keys()]
        except Exception:
            return [(0, 0)]


@dataclass
class _DummyMerged:
    journey_metadata: list[dict]
    journey_times: list[list[tuple[int, int | None, int | None]]]
    journey_to_route: list[int]
    journey_stop_index: list[dict]
    legacy_full_route_polyline: list[list[tuple[float, float]]]
    route_stops: list[list[int]]
    stop_metadata: list[str]

    route_tracks = [[]]

    def get_atco_code(self, stop_int: int):
        return None


def _install_fixed_time(monkeypatch):
    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            # 10:00:00
            return datetime(2026, 3, 22, 10, 0, 0)

    monkeypatch.setattr(api_module, "datetime", _FixedDatetime)


def test_offtrack_rejected_when_far_from_track(monkeypatch):
    _install_fixed_time(monkeypatch)

    merged = _DummyMerged(
        journey_metadata=[{"line_name": "10", "operator_national_code": "SCCU"}],
        # Two stops close together -> no far-stop exception possible.
        journey_times=[[(0, 36000, 36000), (1, 36600, 36600)]],
        journey_to_route=[0],
        journey_stop_index=[{0: 0, 1: 1}],
        legacy_full_route_polyline=[[(54.0, -2.8), (54.0001, -2.8001)]],
        route_stops=[[0, 1]],
        stop_metadata=["A", "B"],
    )

    walking = _DummyWalking(
        stop_coords={
            0: (54.0000, -2.8000),
            1: (54.0001, -2.8001),
        }
    )

    def _fake_get_router_for_date(_today, start_time=None, apply_delay=False):
        return merged, SimpleNamespace(), walking

    monkeypatch.setattr(api_module, "get_router_for_date", _fake_get_router_for_date)
    # `_journeys_for_line_short` is a closure defined inside `_compute_delay_from_timetable`,
    # so patch the cache it uses instead.
    monkeypatch.setattr(api_module, "_LIVE_MATCH_JOURNEYS_BY_LINE", {id(merged): {"10": [0]}}, raising=False)

    # Make gating strict.
    monkeypatch.setenv("MATCH_MAX_TRACK_DIST_M", "50")
    monkeypatch.setenv("MATCH_BETWEEN_STOPS_MIN_GAP_M", "800")
    monkeypatch.setenv("MATCH_BETWEEN_STOPS_MAX_PERP_M", "400")

    # Point far away -> should be rejected.
    res = api_module._compute_delay_from_timetable(
        "10",
        dest="",
        lat_v=54.0100,
        lon_v=-2.8100,
        return_jid=True,
    )
    assert res is None or res == (None, None)


def test_offtrack_allowed_when_between_two_far_stops(monkeypatch):
    _install_fixed_time(monkeypatch)

    # Two stops far apart (~1.11km apart in latitude).
    merged = _DummyMerged(
        journey_metadata=[{"line_name": "10", "operator_national_code": "SCCU"}],
        journey_times=[[(0, 36000, 36000), (1, 37200, 37200)]],
        journey_to_route=[0],
        journey_stop_index=[{0: 0, 1: 1}],
        legacy_full_route_polyline=[[(54.0, -2.8), (54.01, -2.8)]],
        route_stops=[[0, 1]],
        # Provide metadata list large enough for stop indices 0 and 1.
        stop_metadata=["A", "B"],
    )

    walking = _DummyWalking(
        stop_coords={
            0: (54.0000, -2.8000),
            1: (54.0100, -2.8000),
        }
    )

    def _fake_get_router_for_date(_today, start_time=None, apply_delay=False):
        return merged, SimpleNamespace(), walking

    monkeypatch.setattr(api_module, "get_router_for_date", _fake_get_router_for_date)
    monkeypatch.setattr(api_module, "_LIVE_MATCH_JOURNEYS_BY_LINE", {id(merged): {"10": [0]}}, raising=False)

    # IMPORTANT: the matcher uses MATCH_MAX_TRACK_DIST_M in an *earlier*
    # prefilter (route stop proximity). Keep that permissive so we can isolate
    # the scoring-stage off-track logic.
    monkeypatch.setenv("MATCH_MAX_TRACK_DIST_M", "5000")
    # Force the scoring-stage off-track threshold to be strict.
    monkeypatch.setenv("MATCH_SCORE_MAX_TRACK_DIST_M", "50")
    # But allow between-stops exception for segments >= 800m.
    monkeypatch.setenv("MATCH_BETWEEN_STOPS_MIN_GAP_M", "800")
    monkeypatch.setenv("MATCH_BETWEEN_STOPS_MAX_PERP_M", "400")
    # Disable the new past-start guard for this geometry-only test.
    monkeypatch.setenv("MATCH_REJECT_PAST_START_S", "200000")

    # Vehicle near the middle of the segment, but shifted ~150-200m east
    # (longitude) so projection distance is likely > 50m.
    res = api_module._compute_delay_from_timetable(
        "10",
        dest="",
        lat_v=54.0050,
        lon_v=-2.7975,
        return_jid=True,
    )

    # Should match (i.e., not be treated as off-track). In this unit-level stub
    # we don't require `route_int` to be populated.
    assert res is not None
    delay, route_int = res
    assert delay is None or isinstance(delay, int)


def test_reject_when_origin_start_more_than_4h_in_past(monkeypatch):
    _install_fixed_time(monkeypatch)

    # now = 10:00:00 (36000). Use *yesterday* encoding (negative seconds)
    # to satisfy the matcher's day-guard while still being >4h in the past.
    #
    # start -7200 => yesterday 22:00 (12h ago) -> should be rejected by 4h gate
    # start -3600 => yesterday 23:00 (11h ago) -> should be rejected by 4h gate
    merged_stale = _DummyMerged(
        journey_metadata=[
            {"line_name": "10", "operator_national_code": "SCCU"},
            {"line_name": "10", "operator_national_code": "SCCU"},
        ],
        journey_times=[
            [(0, -7200, -7200), (1, -6600, -6600)],
            [(0, -3600, -3600), (1, -3000, -3000)],
        ],
        journey_to_route=[0, 1],
        journey_stop_index=[{0: 0, 1: 1}, {0: 0, 1: 1}],
        legacy_full_route_polyline=[
            [(54.0, -2.8), (54.01, -2.8)],
            [(54.0, -2.8), (54.01, -2.8)],
        ],
        route_stops=[[0, 1], [0, 1]],
        stop_metadata=["A", "B"],
    )


    walking = _DummyWalking(
        stop_coords={
            0: (54.0000, -2.8000),
            1: (54.0100, -2.8000),
        }
    )

    def _fake_get_router_for_date_stale(_today, start_time=None, apply_delay=False):
        return merged_stale, SimpleNamespace(), walking

    monkeypatch.setattr(api_module, "get_router_for_date", _fake_get_router_for_date_stale)
    monkeypatch.setattr(
        api_module,
        "_LIVE_MATCH_JOURNEYS_BY_LINE",
        {id(merged_stale): {"10": [0, 1]}},
        raising=False,
    )

    # Make spatial gates permissive.
    monkeypatch.setenv("MATCH_MAX_TRACK_DIST_M", "5000")
    monkeypatch.setenv("MATCH_SCORE_MAX_TRACK_DIST_M", "5000")

    # Use default 4h (14400s). Vehicle location on the route.
    res = api_module._compute_delay_from_timetable(
        "10",
        dest="",
        lat_v=54.0050,
        lon_v=-2.8000,
        return_jid=True,
    )

    # With only stale journeys available, the matcher should reject all.
    assert res is None or res == (None, None)
