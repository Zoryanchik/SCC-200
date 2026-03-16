import os


def test_compute_delay_does_not_raise_progress_unbound(monkeypatch):
    """Regression test for UnboundLocalError: progress.

    The matcher projects the live vehicle onto a cached track. Previously,
    when the cache was cold, `progress` wasn't computed before being used.
    This test doesn't assert a particular match outcome; it only ensures
    the function runs without raising.
    """

    # Import inside the test so the module-level env reads happen after we
    # tweak the environment.
    import api as api

    # Keep the matcher permissive to avoid needing a very specific geometry.
    monkeypatch.setenv("MATCH_MAX_TRACK_DIST_M", "999999")
    monkeypatch.setenv("MATCH_SEGMENT_DIST_M", "999999")
    monkeypatch.setenv("BUS_LIVE_PROVENANCE", "1")

    # Minimal stub objects with just the attributes the matcher touches.
    class MergedStub:
        route_tracks = [[]]
        route_stops = [["STOP:A", "STOP:B"]]
        journey_metadata = {}

        # Journey-times table: matcher iterates these to build candidate lists.
        # We include exactly one journey on our test line/destination.
        journey_times = {
            "J1": [
                ("STOP:A", 0, 0),
                ("STOP:B", 600, 600),
            ]
        }

        # Journey-level metadata used for filtering.
        journey_lines = {"J1": "100"}
        journey_dests = {"J1": "Bus Station"}
        journey_routes = {"J1": 0}

    merged = MergedStub()

    # Provide coordinates for stops used to build a fallback track.
    coords = {
        "STOP:A": (54.0, -2.8),
        "STOP:B": (54.001, -2.801),
    }

    def fake_get_loc_coords(sid):
        return coords[sid]

    class WalkingStub:
        @staticmethod
        def get_loc_coords(sid):
            return fake_get_loc_coords(sid)

    walking = WalkingStub()

    # The matcher calls get_router_for_date() to fetch merged+walking.
    def fake_get_router_for_date(*args, **kwargs):
        return merged, None, walking

    monkeypatch.setattr(api, "get_router_for_date", fake_get_router_for_date)

    # The matcher expects an iterable of candidates; the internal structure
    # matches how api.py uses it.
    # Vehicle point near the segment.
    lat_v, lon_v = 54.0005, -2.8005

    # Call the function; it should not throw.
    api._compute_delay_from_timetable(
        "100",
        "Bus Station",
        lat_v,
        lon_v,
        origin_dep_secs=0,
        origin_tz_offset_secs=0,
        strict_tol=600,
        return_jid=True,
    )
