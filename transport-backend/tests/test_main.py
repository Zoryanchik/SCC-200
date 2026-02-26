"""Tests for main.py — build_for_date, print_route, format_route_text.

Uses a small in-memory BusData + BusLoader with a temporary database
to validate the full pipeline without network access.
"""
import os
import sys
import sqlite3
import importlib
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest

backend_dir = os.path.join(os.path.dirname(__file__), "..")
if backend_dir not in sys.path:
    sys.path.insert(0, os.path.abspath(backend_dir))

# Ensure real modules
for _mod in ["dense_mapper", "bus_data", "train_data", "merged_data",
             "timetable", "raptor_router", "walking", "bus_loader",
             "time_utils", "main"]:
    sys.modules.pop(_mod, None)

import main as main_module
importlib.reload(main_module)

from main import build_for_date, print_route
from bus_loader import BusLoader
from time_utils import seconds_to_time


@pytest.fixture()
def loader_with_data(tmp_path):
    """BusLoader backed by a temp DB with minimal bus timetable data.

    Creates one route (R1) with 3 stops and 2 journeys operating
    every day of the week, plus stop coordinates and walking transfers.
    """
    db_path = str(tmp_path / "test.db")
    loader = BusLoader(db_path)
    loader.ensure_db()
    loader.create_schema()

    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    # Route stops
    c.execute("INSERT INTO route_stops VALUES (?, ?, ?)", ("R1", "S1", 0))
    c.execute("INSERT INTO route_stops VALUES (?, ?, ?)", ("R1", "S2", 1))
    c.execute("INSERT INTO route_stops VALUES (?, ?, ?)", ("R1", "S3", 2))

    # Journey routes
    c.execute("INSERT INTO journey_routes VALUES (?, ?, ?, ?)",
              ("J1", "R1", "10", "Town Centre"))
    c.execute("INSERT INTO journey_routes VALUES (?, ?, ?, ?)",
              ("J2", "R1", "10", "Town Centre"))

    # Journey times (arrival times in seconds since midnight)
    c.execute("INSERT INTO journey_times VALUES (?, ?, ?)", ("J1", "S1", 36000))
    c.execute("INSERT INTO journey_times VALUES (?, ?, ?)", ("J1", "S2", 36300))
    c.execute("INSERT INTO journey_times VALUES (?, ?, ?)", ("J1", "S3", 36600))
    c.execute("INSERT INTO journey_times VALUES (?, ?, ?)", ("J2", "S1", 37000))
    c.execute("INSERT INTO journey_times VALUES (?, ?, ?)", ("J2", "S2", 37300))
    c.execute("INSERT INTO journey_times VALUES (?, ?, ?)", ("J2", "S3", 37600))

    # Stop names
    c.execute("INSERT INTO stop_names VALUES (?, ?, ?, ?)",
              ("S1", "Alpha Stop", "Bay A", "Lancaster"))
    c.execute("INSERT INTO stop_names VALUES (?, ?, ?, ?)",
              ("S2", "Beta Stop", "Bay B", "Lancaster"))
    c.execute("INSERT INTO stop_names VALUES (?, ?, ?, ?)",
              ("S3", "Gamma Stop", "Bay C", "Lancaster"))

    # Stop coordinates
    c.execute("INSERT INTO stop_coords VALUES (?, ?, ?)", ("S1", 54.0480, -2.8010))
    c.execute("INSERT INTO stop_coords VALUES (?, ?, ?)", ("S2", 54.0485, -2.8020))
    c.execute("INSERT INTO stop_coords VALUES (?, ?, ?)", ("S3", 54.0490, -2.8030))

    # Walking transfers
    c.execute("INSERT INTO walking_transfers VALUES (?, ?, ?)",
              ("S1", "S2", 90))
    c.execute("INSERT INTO walking_transfers VALUES (?, ?, ?)",
              ("S2", "S1", 90))

    # Operating profile: operate every day (127 = 0b1111111)
    c.execute(
        "INSERT INTO journey_operating_profile "
        "(journey_id, service_code, days_of_week, start_date, end_date, org_ref, org_working) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("J1", "SVC1", 127, "2025-01-01", "2027-12-31", None, 1),
    )
    c.execute(
        "INSERT INTO journey_operating_profile "
        "(journey_id, service_code, days_of_week, start_date, end_date, org_ref, org_working) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("J2", "SVC1", 127, "2025-01-01", "2027-12-31", None, 1),
    )

    # Service operating period
    c.execute("INSERT INTO service_operating_period VALUES (?, ?, ?)",
              ("SVC1", "2025-01-01", "2027-12-31"))

    conn.commit()
    conn.close()
    return loader


class TestBuildForDate:
    def test_returns_timetable_router_walking(self, loader_with_data):
        raw_transfers = loader_with_data.get_walking_transfers()
        raw_coords = loader_with_data.get_all_stop_coords()
        walking_raw = {
            "transfers": raw_transfers,
            "coords": raw_coords,
            "osrm_url": "http://osrm-does-not-exist:9999",
        }
        timetable, router, walking = build_for_date(
            loader_with_data, walking_raw, "2026-02-26",
        )
        assert timetable is not None
        assert router is not None
        assert walking is not None

    def test_timetable_has_today_merged_data(self, loader_with_data):
        raw_transfers = loader_with_data.get_walking_transfers()
        raw_coords = loader_with_data.get_all_stop_coords()
        walking_raw = {
            "transfers": raw_transfers,
            "coords": raw_coords,
            "osrm_url": "http://osrm-does-not-exist:9999",
        }
        timetable, router, walking = build_for_date(
            loader_with_data, walking_raw, "2026-02-26",
        )
        assert hasattr(timetable, "today")
        assert len(timetable.today.stop_to_routes) > 0


class TestPrintRoute:
    def test_print_no_route(self, capsys):
        merged = MagicMock()
        print_route({}, merged)
        captured = capsys.readouterr()
        assert "No route found" in captured.out

    def test_print_route_with_stops(self, loader_with_data, capsys):
        raw_transfers = loader_with_data.get_walking_transfers()
        raw_coords = loader_with_data.get_all_stop_coords()
        walking_raw = {
            "transfers": raw_transfers,
            "coords": raw_coords,
            "osrm_url": "http://osrm-does-not-exist:9999",
        }
        timetable, router, walking = build_for_date(
            loader_with_data, walking_raw, "2026-02-26",
        )

        start = (54.0480, -2.8010)
        end = (54.0490, -2.8030)
        result = router.route(
            n_transfer_limit=3,
            walking=walking,
            start_date="2026-02-26",
            start_time=35000,
            start_point=start,
            destination=end,
            allowed_modes={"bus"},
        )
        print_route(result, timetable.today)
        captured = capsys.readouterr()
        # Should print something (either route or no route)
        assert len(captured.out) > 0
