"""Unit tests for MergedData lazy route_link_tracks.

These tests intentionally do NOT import `api` to avoid cross-test monkeypatching
that can replace symbols with mocks.
"""

import pytest


def test_merged_data_get_route_link_tracks_uses_bus_loader_when_slot_empty():
    import merged_data as md
    # If another test suite/component replaced merged_data.MergedData with a mock,
    # this unit test can't validate behavior reliably.
    if md.MergedData.__class__.__name__ != "type":
        pytest.skip("MergedData is mocked in this test environment")
    class _Loader:
        def get_route_link_tracks_for_route(self, route_id: str):
            assert route_id == "RID"
            return {
                ("A", "B"): [(54.00, -2.80), (54.01, -2.80)],
                ("B", "C"): [(54.01, -2.80), (54.02, -2.80)],
            }

    class _Merged:
        route_stops = [[0, 1, 2]]
        route_link_tracks = [{}]  # placeholder slot => must trigger lazy build
        route_metadata = [{"route_id": "RID"}]
        bus_loader = _Loader()

        import threading

        _route_link_tracks_lock = threading.Lock()

        def get_atco_code(self, merged_stop_int):
            return {0: "A", 1: "B", 2: "C"}.get(merged_stop_int)

    merged = _Merged()
    link_map = md.MergedData.get_route_link_tracks(merged, 0)
    assert isinstance(link_map, dict)
    assert link_map.get((0, 1)) == [(54.00, -2.80), (54.01, -2.80)]
    assert link_map.get((1, 2)) == [(54.01, -2.80), (54.02, -2.80)]
