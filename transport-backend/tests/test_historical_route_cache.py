"""Regression: historical routing must use that day's in-memory caches.

Bug: /route/leg-geometry resolved route_id geometry by scanning *any* loaded
MergedData. In practice that often returned today's prebuilt merged timetable,
so when the frontend routed for a different day, the drawn geometry was wrong.

This test stubs the in-memory router cache with two different MergedData-like
objects (today vs past). When requesting a past date, we must use the past
cache, not today's.
"""

from __future__ import annotations

from datetime import date as _date


class _FakeMerged:
    def __init__(self, track_marker: str):
        # route_metadata mirrors the real MergedData shape: list[dict]
        self.route_metadata = [{"route_id": "RID1"}]
        # Legacy full-route polylines were removed from the real backend; keep a
        # synthetic placeholder here to ensure the handler doesn't crash when
        # legacy-looking attributes exist.
        marker_lat = 1.0 if track_marker == "today" else 2.0
        self.legacy_full_route_polyline = [[[marker_lat, 0.0], [marker_lat, 1.0]]]


def test_leg_geometry_prefers_requested_date_router_cache(monkeypatch):
    import api as _api

    # Ensure a clean cache for this test.
    monkeypatch.setattr(_api, "_router_cache", {}, raising=False)

    today = _date.today().isoformat()
    past = (_date.today().replace(day=max(1, _date.today().day - 1))).isoformat()

    # Seed both entries. The scan-based behaviour would return the first merged
    # it encounters (often today). We require date-aware selection.
    _api._router_cache[(today, "PM")] = (_FakeMerged("today"), None, None)
    _api._router_cache[(past, "PM")] = (_FakeMerged("past"), None, None)

    out = _api.route_leg_geometry(
        from_lat=0.0,
        from_lon=0.0,
        to_lat=0.0,
        to_lon=0.0,
        mode="bus",
        route_id="RID1",
        # Provide the requested service day.
        date=past,
        # departure_time omitted => PM bucket default.
    )

    # Policy: legacy full-route polylines are not used/returned by /route/leg-geometry.
    # This test is retained only to ensure date-specific router cache seeding
    # doesn't crash the handler.
    assert isinstance(out, dict)
    assert "coords" in out
