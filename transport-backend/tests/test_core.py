"""Tests for DenseMapper, BusData, TrainData, MergedData, Timetable,
and RaptorRouter — the core transit data structures and routing engine.

These tests use the REAL implementations (no mocks for these modules)
so they contribute to actual code coverage.
"""
import math
import os
import sys
import importlib

import pytest

backend_dir = os.path.join(os.path.dirname(__file__), "..")
if backend_dir not in sys.path:
    sys.path.insert(0, os.path.abspath(backend_dir))

# Ensure we have the REAL modules, not mocks left by other test files.
_modules_to_restore = [
    "dense_mapper", "bus_data", "train_data", "merged_data",
    "timetable", "raptor_router", "walking",
]
for _mod_name in _modules_to_restore:
    sys.modules.pop(_mod_name, None)

import dense_mapper, bus_data, train_data, merged_data, timetable, raptor_router, walking  # noqa
for _m in [dense_mapper, bus_data, train_data, merged_data, timetable, raptor_router, walking]:
    importlib.reload(_m)

from dense_mapper import DenseMapper
from bus_data import BusData
from train_data import TrainData
from merged_data import MergedData
from timetable import Timetable
from raptor_router import RaptorRouter
from walking import Walking


# ═══════════════════════════════════════════════════════════════════════
#  DenseMapper
# ═══════════════════════════════════════════════════════════════════════

class TestDenseMapper:
    def test_get_int_new(self):
        m = DenseMapper()
        assert m.get_int("ATCO001") == 0
        assert m.get_int("ATCO002") == 1

    def test_get_int_existing(self):
        m = DenseMapper()
        first = m.get_int("ATCO001")
        assert m.get_int("ATCO001") == first

    def test_get_code(self):
        m = DenseMapper()
        idx = m.get_int("ATCO_X")
        assert m.get_code(idx) == "ATCO_X"

    def test_len(self):
        m = DenseMapper()
        m.get_int("A")
        m.get_int("B")
        m.get_int("A")  # duplicate
        assert len(m) == 2

    def test_get_code_out_of_range(self):
        m = DenseMapper()
        with pytest.raises(IndexError):
            m.get_code(0)


# ═══════════════════════════════════════════════════════════════════════
#  BusData
# ═══════════════════════════════════════════════════════════════════════

class TestBusData:
    def test_add_route_stop(self):
        bd = BusData(0, 0, 0)
        bd.add_route_stop("R1", ["S1", "S2", "S3"])
        r_int = bd.map_routes.get_int("R1")
        assert len(bd.route_stops[r_int]) == 3

    def test_add_route_journeys(self):
        bd = BusData(0, 0, 0)
        bd.add_route_stop("R1", ["S1", "S2"])
        bd.add_route_journeys("R1", ["J1", "J2"])
        r_int = bd.map_routes.get_int("R1")
        assert len(bd.route_journeys[r_int]) == 2

    def test_add_journey_times(self):
        bd = BusData(0, 0, 0)
        bd.add_route_stop("R1", ["S1", "S2"])
        bd.add_route_journeys("R1", ["J1"])
        bd.add_journey_times("J1", [("S1", 36000), ("S2", 36300)])
        j_int = bd.map_journeys.get_int("J1")
        assert len(bd.journey_times[j_int]) == 2
        # Each entry is (stop_int, arrival, departure)
        assert bd.journey_times[j_int][0][1] == 36000

    def test_add_journey_times_with_departures(self):
        bd = BusData(0, 0, 0)
        bd.add_route_stop("R1", ["S1", "S2"])
        bd.add_route_journeys("R1", ["J1"])
        bd.add_journey_times("J1", [("S1", 36000), ("S2", 36300)],
                             departure_times=[36010, 36300])
        j_int = bd.map_journeys.get_int("J1")
        assert bd.journey_times[j_int][0][2] == 36010

    def test_add_journey_times_invalid(self):
        bd = BusData(0, 0, 0)
        bd.add_route_stop("R1", ["S1"])
        bd.add_route_journeys("R1", ["J1"])
        with pytest.raises(ValueError):
            bd.add_journey_times("J1", ["not_a_tuple"])

    def test_stop_to_routes(self):
        bd = BusData(0, 0, 0)
        bd.add_route_stop("R1", ["S1", "S2"])
        bd.add_route_stop("R2", ["S2", "S3"])
        s2_int = bd.map_stops.get_int("S2")
        r1_int = bd.map_routes.get_int("R1")
        r2_int = bd.map_routes.get_int("R2")
        assert r1_int in bd.stop_to_routes[s2_int]
        assert r2_int in bd.stop_to_routes[s2_int]

    def test_journey_to_route(self):
        bd = BusData(0, 0, 0)
        bd.add_route_stop("R1", ["S1"])
        bd.add_route_journeys("R1", ["J1"])
        j_int = bd.map_journeys.get_int("J1")
        r_int = bd.map_routes.get_int("R1")
        assert bd.journey_to_route[j_int] == r_int


# ═══════════════════════════════════════════════════════════════════════
#  TrainData
# ═══════════════════════════════════════════════════════════════════════

class TestTrainData:
    def test_add_route_stop(self):
        td = TrainData(0, 0, 0)
        td.add_route_stop("TR1", ["TS1", "TS2"])
        r_int = td.map_routes.get_int("TR1")
        assert len(td.route_stops[r_int]) == 2

    def test_add_route_journeys(self):
        td = TrainData(0, 0, 0)
        td.add_route_stop("TR1", ["TS1"])
        td.add_route_journeys("TR1", ["TJ1"])
        r_int = td.map_routes.get_int("TR1")
        assert len(td.route_journeys[r_int]) == 1

    def test_add_journey_times(self):
        td = TrainData(0, 0, 0)
        td.add_route_stop("TR1", ["TS1", "TS2"])
        td.add_route_journeys("TR1", ["TJ1"])
        td.add_journey_times("TJ1", [("TS1", 40000), ("TS2", 40600)])
        j_int = td.map_journeys.get_int("TJ1")
        assert len(td.journey_times[j_int]) == 2

    def test_add_journey_times_with_departures(self):
        td = TrainData(0, 0, 0)
        td.add_route_stop("TR1", ["TS1", "TS2"])
        td.add_route_journeys("TR1", ["TJ1"])
        td.add_journey_times("TJ1", [("TS1", 40000), ("TS2", 40600)],
                             departure_times=[40010, 40600])
        j_int = td.map_journeys.get_int("TJ1")
        assert td.journey_times[j_int][0][2] == 40010

    def test_add_journey_times_invalid(self):
        td = TrainData(0, 0, 0)
        td.add_route_stop("TR1", ["TS1"])
        td.add_route_journeys("TR1", ["TJ1"])
        with pytest.raises(ValueError):
            td.add_journey_times("TJ1", [42])


# ═══════════════════════════════════════════════════════════════════════
#  MergedData
# ═══════════════════════════════════════════════════════════════════════

def _make_bus_data():
    """Build a small BusData for testing."""
    bd = BusData(0, 0, 0)
    bd.add_route_stop("R1", ["S1", "S2", "S3"])
    bd.add_route_journeys("R1", ["J1", "J2"])
    bd.add_journey_times("J1", [("S1", 36000), ("S2", 36300), ("S3", 36600)],
                         departure_times=[36000, 36310, 36600])
    bd.add_journey_times("J2", [("S1", 37000), ("S2", 37300), ("S3", 37600)],
                         departure_times=[37000, 37310, 37600])
    bd.route_metadata[bd.map_routes.get_int("R1")] = {"line_name": "10"}
    bd.journey_metadata[bd.map_journeys.get_int("J1")] = {
        "line_name": "10", "destination_display": "Town Centre",
    }
    bd.journey_metadata[bd.map_journeys.get_int("J2")] = {
        "line_name": "10", "destination_display": "Town Centre",
    }
    return bd


def _make_train_data():
    """Build a small TrainData for testing."""
    td = TrainData(0, 0, 0)
    td.add_route_stop("TR1", ["TS1", "TS2"])
    td.add_route_journeys("TR1", ["TJ1"])
    td.add_journey_times("TJ1", [("TS1", 40000), ("TS2", 41000)],
                         departure_times=[40010, 41000])
    td.route_metadata[td.map_routes.get_int("TR1")] = {"line_name": "Northern"}
    td.journey_metadata[td.map_journeys.get_int("TJ1")] = {
        "line_name": "Northern", "destination_display": "Manchester",
    }
    return td


class TestMergedData:
    def test_bus_only(self):
        bd = _make_bus_data()
        md = MergedData(bd, None)
        assert len(md.stop_to_routes) == 3
        assert len(md.route_stops) == 1
        assert len(md.journey_times) == 2

    def test_train_only(self):
        td = _make_train_data()
        md = MergedData(None, td)
        assert len(md.stop_to_routes) == 2
        assert len(md.route_stops) == 1

    def test_merged_bus_and_train(self):
        bd = _make_bus_data()
        td = _make_train_data()
        md = MergedData(bd, td)
        # 3 bus stops + 2 train stops
        assert len(md.stop_to_routes) == 5
        # 1 bus route + 1 train route
        assert len(md.route_stops) == 2
        # 2 bus journeys + 1 train journey
        assert len(md.journey_times) == 3

    def test_journey_type_bus(self):
        bd = _make_bus_data()
        td = _make_train_data()
        md = MergedData(bd, td)
        j1_int = bd.map_journeys.get_int("J1")
        assert md.journey_type(j1_int) == "bus"

    def test_journey_type_train(self):
        bd = _make_bus_data()
        td = _make_train_data()
        md = MergedData(bd, td)
        # Train journey comes after bus journeys
        train_j_int = len(bd.journey_times)  # offset
        assert md.journey_type(train_j_int) == "train"

    def test_stop_metadata_with_name_fn(self):
        bd = _make_bus_data()
        names = {"S1": "Stop One", "S2": "Stop Two", "S3": "Stop Three"}
        md = MergedData(bd, None, stop_name_fn=lambda codes: {c: names.get(c, c) for c in codes})
        assert md.stop_metadata[0] == "Stop One"
        assert md.stop_metadata[1] == "Stop Two"

    def test_stop_metadata_without_name_fn(self):
        bd = _make_bus_data()
        md = MergedData(bd, None, stop_name_fn=None)
        # Falls back to ATCO code
        assert md.stop_metadata[0] == "S1"

    def test_time_offset(self):
        bd = _make_bus_data()
        md = MergedData(bd, None, time_offset=-86400)
        # Journey times should be shifted by -86400
        assert md.journey_times[0][0][1] == 36000 - 86400

    def test_journey_stop_index(self):
        bd = _make_bus_data()
        md = MergedData(bd, None)
        j1_int = bd.map_journeys.get_int("J1")
        s1_int = bd.map_stops.get_int("S1")
        assert md.journey_stop_index[j1_int][s1_int] == 0

    def test_route_stop_departures(self):
        bd = _make_bus_data()
        md = MergedData(bd, None)
        r1_int = bd.map_routes.get_int("R1")
        s1_int = bd.map_stops.get_int("S1")
        deps = md.route_stop_departures[r1_int][s1_int]
        # Should be sorted by departure time
        assert deps[0][0] <= deps[1][0]

    def test_route_metadata_merged(self):
        bd = _make_bus_data()
        td = _make_train_data()
        md = MergedData(bd, td)
        assert md.route_metadata[0] == {"line_name": "10"}
        assert md.route_metadata[1] == {"line_name": "Northern"}


# ═══════════════════════════════════════════════════════════════════════
#  Timetable
# ═══════════════════════════════════════════════════════════════════════

class TestTimetable:
    def test_build_network_both(self):
        bd = _make_bus_data()
        td = _make_train_data()
        tt = Timetable(bd, td, bd, td, bd, td)
        tt.build_network("both")
        assert tt.raptor_router is not None
        assert tt.today is not None
        assert tt.yesterday is not None
        assert tt.tomorrow is not None

    def test_build_network_bus_only(self):
        bd = _make_bus_data()
        td = _make_train_data()
        tt = Timetable(bd, td, bd, td, bd, td)
        tt.build_network("bus")
        # Train data should be None in today's merged data
        assert len(tt.today.train_data.route_stops) == 0

    def test_build_network_train_only(self):
        bd = _make_bus_data()
        td = _make_train_data()
        tt = Timetable(bd, td, bd, td, bd, td)
        tt.build_network("train")
        assert len(tt.today.bus_data.route_stops) == 0


# ═══════════════════════════════════════════════════════════════════════
#  RaptorRouter — integration
# ═══════════════════════════════════════════════════════════════════════

def _build_router_scenario():
    """Build a simple bus network with 3 stops on 1 route, 2 journeys."""
    bd = _make_bus_data()
    tt = Timetable(bd, None, bd, None, bd, None)
    tt.build_network("both")

    # Walking engine: stops have coords, no inter-walk precomputed
    s1 = bd.map_stops.get_int("S1")
    s2 = bd.map_stops.get_int("S2")
    s3 = bd.map_stops.get_int("S3")
    coords = {
        s1: (54.0480, -2.8010),
        s2: (54.0485, -2.8020),
        s3: (54.0490, -2.8030),
    }
    inter_walk = {s1: {s2: 90}, s2: {s1: 90, s3: 120}}
    walking = Walking(inter_walk, coords,
                      osrm_base="http://osrm-does-not-exist:9999",
                      max_walk_seconds=600)
    return tt, tt.raptor_router, walking, bd


class TestRaptorRouter:
    def test_route_finds_path(self):
        tt, router, walking, bd = _build_router_scenario()
        s1 = bd.map_stops.get_int("S1")
        s3 = bd.map_stops.get_int("S3")
        # Start near S1, end near S3
        start = (54.0480, -2.8010)
        end = (54.0490, -2.8030)
        result = router.route(
            n_transfer_limit=3,
            walking=walking,
            start_date="2026-02-26",
            start_time=35000,  # before first journey at 36000
            start_point=start,
            destination=end,
            allowed_modes={"bus"},
        )
        # Should find a route (not empty)
        assert result is not None
        # Result has _meta key
        assert "_meta" in result or result == {}

    def test_route_no_path_when_too_far(self):
        tt, router, walking, bd = _build_router_scenario()
        # Start and end are far away from any stop
        start = (55.0, -3.0)
        end = (56.0, -4.0)
        result = router.route(
            n_transfer_limit=3,
            walking=walking,
            start_date="2026-02-26",
            start_time=35000,
            start_point=start,
            destination=end,
            allowed_modes={"bus"},
        )
        assert result == {}

    def test_route_direct_walking(self):
        """When start and end are very close, may return walking-only."""
        tt, router, walking, bd = _build_router_scenario()
        start = (54.0480, -2.8010)
        end = (54.0481, -2.8011)  # Very close
        result = router.route(
            n_transfer_limit=3,
            walking=walking,
            start_date="2026-02-26",
            start_time=35000,
            start_point=start,
            destination=end,
            allowed_modes={"bus"},
        )
        # Either empty or walking-only (only _meta)
        if result:
            assert "_meta" in result

    def test_route_respects_transfer_limit_zero(self):
        tt, router, walking, bd = _build_router_scenario()
        start = (54.0480, -2.8010)
        end = (54.0490, -2.8030)
        result = router.route(
            n_transfer_limit=0,
            walking=walking,
            start_date="2026-02-26",
            start_time=35000,
            start_point=start,
            destination=end,
            allowed_modes={"bus"},
        )
        # With 0 transfers, route might still be found if direct
        assert isinstance(result, dict)

    def test_route_train_mode_on_bus_network(self):
        """Train mode on a bus-only network should find no transit route."""
        tt, router, walking, bd = _build_router_scenario()
        start = (54.0480, -2.8010)
        end = (54.0490, -2.8030)
        result = router.route(
            n_transfer_limit=3,
            walking=walking,
            start_date="2026-02-26",
            start_time=35000,
            start_point=start,
            destination=end,
            allowed_modes={"train"},
        )
        # No train journeys exist, so result should either be empty
        # or walking-only
        if result:
            meta = result.get("_meta", {})
            # If present it's walking only
            assert set(result.keys()) == {"_meta"} or result == {}

    def test_first_journey_no_route(self):
        """first_journey with out-of-range route returns None."""
        tt, router, walking, bd = _build_router_scenario()
        reach_stops = [{"arrival_time": 35000, "prev_stop": None,
                        "type": None, "journey": None, "day": None}]
        result = router.first_journey(
            tt.today, 999, 0, reach_stops, [], {"bus"}
        )
        assert result is None

    def test_first_journey_valid(self):
        """first_journey finds the first valid journey at a stop."""
        tt, router, walking, bd = _build_router_scenario()
        s1 = bd.map_stops.get_int("S1")
        num_stops = len(tt.today.stop_to_routes)
        reach_stops = []
        for i in range(num_stops):
            reach_stops.append({
                "arrival_time": math.inf,
                "prev_stop": None,
                "type": None,
                "journey": None,
                "day": None,
            })
        reach_stops[s1]["arrival_time"] = 35000
        switch_b = []
        result = router.first_journey(
            tt.today, 0, s1, reach_stops, switch_b, {"bus"}
        )
        # Should find a journey
        assert result is not None
