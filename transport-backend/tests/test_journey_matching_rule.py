"""Tests for the staged journey matching rule in api._compute_delay_from_timetable.

We keep this unit-level and avoid network/DB by stubbing get_router_for_date
and providing a minimal merged/router/walking triple.

Rule being tested (requested by user):
  1) line match
  2) latch via origin_atco + origin start time
  3) for remaining, latch via destination_atco
  4) within destination-latched, find origin_atco in those journeys and compare
     feed origin start time with ARRIVAL/DEPARTURE time at that stop.

We assert that the matcher returns a journey id (return_jid=True) corresponding
to the expected candidate.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace

import api as api_module


@dataclass
class _DummyWalking:
    stop_coords: dict[int, tuple[float, float]]

    def get_loc_coords(self, stop_int: int):
        return self.stop_coords.get(stop_int)

    def reachable_stops(self, latlon):
        # Return stop 0 as nearest to satisfy candidate filtering.
        # The matcher also checks that the nearest stop is served by the journey.
        return [(0, 0)]


@dataclass
class _DummyMerged:
    journey_metadata: list[dict]
    journey_times: list[list[tuple[int, int | None, int | None]]]
    journey_to_route: list[int]
    journey_stop_index: list[dict]
    route_tracks: list[list[tuple[float, float]]]
    route_stops: list[list[int]]
    stop_metadata: list[str]

    def get_atco_code(self, stop_int: int):
        # Map stop indices to ATCO-like strings.
        return {
            0: "ATCO_ORIGIN",
            1: "ATCO_MID",
            2: "ATCO_DEST_A",
            3: "ATCO_DEST_B",
        }.get(stop_int)


def test_staged_matching_prefers_origin_atco_time_latch(monkeypatch):
    # Fixed 'now' so delay computation is stable.
    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            # 10:00:00
            return datetime(2026, 3, 16, 10, 0, 0)

    # Two journeys share the same line. Only journey 1 matches origin_atco+time.
    # Journey 0 has destination atco match only.
    merged = _DummyMerged(
        journey_metadata=[
            {"line_name": "100", "operator_national_code": "SCCU", "journey_id": "J0"},
            {"line_name": "100", "operator_national_code": "SCCU", "journey_id": "J1"},
        ],
            journey_times=[
                # J0: does NOT start at the feed origin stop (stop 1 is first), destination ATCO_DEST_A
                [(1, 28800, 28800), (2, 31200, 31200)],
                # J1: starts at feed origin stop (stop 0), destination ATCO_DEST_B
                [(0, 29400, 29400), (1, 30600, 30600), (3, 31800, 31800)],
            ],
            journey_to_route=[0, 0],
            journey_stop_index=[{1: 0, 2: 1}, {0: 0, 1: 1, 3: 2}],
        route_tracks=[[(54.0, -2.8), (54.01, -2.79)]],
        route_stops=[[0, 1, 2, 3]],
        # stop_metadata length drives the matcher's spatial scan, so include
        # all stops we reference.
        stop_metadata=["Origin", "Mid", "DestA", "DestB"],
    )

    walking = _DummyWalking(
        stop_coords={
            # Keep everything within a few hundred metres so the stop-radius
            # prefilter always finds nearby stops.
            0: (54.0000, -2.8000),
            1: (54.0005, -2.7995),
            2: (54.0010, -2.7990),
            3: (54.0015, -2.7985),
        }
    )

    def _fake_get_router_for_date(_today, start_time=None, apply_delay=False):
        return merged, SimpleNamespace(), walking

    monkeypatch.setattr(api_module, "get_router_for_date", _fake_get_router_for_date)
    monkeypatch.setattr(api_module, "datetime", _FixedDatetime)

    latched = api_module._stage_filter_journeys_for_live_bus(
        merged,
        walking,
        line_ref="100",
        operator_ref="SCCU",
        origin_dep_secs=29400,
        strict_tol=600,
        feed_origin_atco="ATCO_ORIGIN",
        # Destination points at J0, but origin-atco+time should latch J1.
        feed_destination_atco="ATCO_DEST_A",
        origin_tz_offset_secs=0,
    )
    assert latched == [1]


def test_staged_matching_falls_back_to_destination_then_origin_stop_time(monkeypatch):
    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 3, 16, 10, 0, 0)

    merged = _DummyMerged(
        journey_metadata=[
            {"line_name": "100", "operator_national_code": "SCCU", "journey_id": "J0"},
            {"line_name": "100", "operator_national_code": "SCCU", "journey_id": "J1"},
        ],
        journey_times=[
            # J0: origin stop arrival 08:05, destination ATCO_DEST_A
            [(0, 29100, 29100), (2, 31200, 31200)],
            # J1: origin stop arrival 08:30, destination ATCO_DEST_A
            [(0, 30600, 30600), (2, 32400, 32400)],
        ],
        journey_to_route=[0, 0],
        journey_stop_index=[{0: 0, 2: 1}, {0: 0, 2: 1}],
        route_tracks=[[(54.0, -2.8), (54.01, -2.79)]],
        route_stops=[[0, 2]],
        stop_metadata=["Origin", "Mid", "DestA", "DestB"],
    )

    walking = _DummyWalking(
        stop_coords={
            0: (54.0000, -2.8000),
            1: (54.0005, -2.7995),
            2: (54.0010, -2.7990),
            3: (54.0015, -2.7985),
        }
    )

    def _fake_get_router_for_date(_today, start_time=None, apply_delay=False):
        return merged, SimpleNamespace(), walking

    monkeypatch.setattr(api_module, "get_router_for_date", _fake_get_router_for_date)
    monkeypatch.setattr(api_module, "datetime", _FixedDatetime)

    latched = api_module._stage_filter_journeys_for_live_bus(
        merged,
        walking,
        line_ref="100",
        operator_ref="SCCU",
        origin_dep_secs=29100,
        strict_tol=600,
        feed_origin_atco="ATCO_ORIGIN",
        feed_destination_atco="ATCO_DEST_A",
        origin_tz_offset_secs=0,
    )
    assert latched == [0]
