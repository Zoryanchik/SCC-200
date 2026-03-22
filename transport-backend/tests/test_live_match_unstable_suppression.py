"""Tests for live-match instability suppression.

Rule:
- Track how many *distinct* journey ids (j_id) a bus has been matched to.
- If > 3 distinct journeys within an in-memory TTL window, treat it as unmatched.

We test the helper `_live_vehicle_should_suppress_match` directly.
"""

import api


def test_unstable_suppression_kicks_in_after_more_than_three_distinct(monkeypatch):
    # Make TTL huge so history doesn't reset during the test.
    monkeypatch.setenv("BUS_LIVE_MATCH_HISTORY_TTL_S", "99999")
    monkeypatch.setenv("BUS_LIVE_MAX_DISTINCT_MATCHES", "3")

    vehicle_key = "veh-1"

    assert api._live_vehicle_should_suppress_match(vehicle_key, 1001) is False
    assert api._live_vehicle_should_suppress_match(vehicle_key, 1002) is False
    assert api._live_vehicle_should_suppress_match(vehicle_key, 1003) is False

    # 4th distinct match => suppress
    assert api._live_vehicle_should_suppress_match(vehicle_key, 1004) is True


def test_repeated_same_journey_does_not_trigger(monkeypatch):
    monkeypatch.setenv("BUS_LIVE_MATCH_HISTORY_TTL_S", "99999")
    monkeypatch.setenv("BUS_LIVE_MAX_DISTINCT_MATCHES", "3")

    vehicle_key = "veh-2"

    for _ in range(10):
        assert api._live_vehicle_should_suppress_match(vehicle_key, 2001) is False


def test_ttl_resets_history(monkeypatch):
    monkeypatch.setenv("BUS_LIVE_MATCH_HISTORY_TTL_S", "0")
    monkeypatch.setenv("BUS_LIVE_MAX_DISTINCT_MATCHES", "3")

    vehicle_key = "veh-3"

    # TTL=0 -> always resets -> never accumulates
    assert api._live_vehicle_should_suppress_match(vehicle_key, 3001) is False
    assert api._live_vehicle_should_suppress_match(vehicle_key, 3002) is False
    assert api._live_vehicle_should_suppress_match(vehicle_key, 3003) is False
    assert api._live_vehicle_should_suppress_match(vehicle_key, 3004) is False
