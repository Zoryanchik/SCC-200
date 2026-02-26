"""Tests for bus_loader.py — schema creation, walking_transfers
resilience, stop searching, and basic DB operations.

Uses a temporary SQLite database per test to avoid interfering with
the real cache.
"""
import os
import sys
import sqlite3
import tempfile
import importlib

import pytest

backend_dir = os.path.join(os.path.dirname(__file__), "..")
if backend_dir not in sys.path:
    sys.path.insert(0, os.path.abspath(backend_dir))

# Ensure we have the REAL bus_loader, not a mock
_stale = sys.modules.pop("bus_loader", None)
# bus_loader imports bus_data which may also be mocked
for _dep in ["bus_data", "dense_mapper", "bus_loader"]:
    sys.modules.pop(_dep, None)
import bus_loader as _real_bl  # noqa
importlib.reload(_real_bl)
sys.modules["bus_loader"] = _real_bl

from bus_loader import BusLoader


@pytest.fixture()
def tmp_db(tmp_path):
    """Return a path to a fresh temporary SQLite database."""
    return str(tmp_path / "test_bus.db")


@pytest.fixture()
def loader(tmp_db):
    """BusLoader with fresh temporary database + schema."""
    bl = BusLoader(tmp_db)
    bl.ensure_db()
    bl.create_schema()
    return bl


# ═══════════════════════════════════════════════════════════════════════
#  Schema creation
# ═══════════════════════════════════════════════════════════════════════

class TestSchema:
    def test_ensure_db_creates_file(self, tmp_db):
        bl = BusLoader(tmp_db)
        bl.ensure_db()
        assert os.path.exists(tmp_db)

    def test_create_schema_creates_tables(self, loader, tmp_db):
        conn = sqlite3.connect(tmp_db)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cur.fetchall()}
        conn.close()
        expected = {
            "route_stops", "journey_routes", "journey_times",
            "stop_names", "stop_coords", "walking_transfers",
            "dataset_meta", "service_operating_period",
            "serviced_org_working_days", "journey_operating_profile",
        }
        assert expected.issubset(tables)

    def test_create_schema_idempotent(self, loader):
        """Calling create_schema twice doesn't fail."""
        loader.create_schema()  # second call


# ═══════════════════════════════════════════════════════════════════════
#  Walking transfers — the key bug fix
# ═══════════════════════════════════════════════════════════════════════

class TestWalkingTransfers:
    def test_get_walking_transfers_empty(self, loader):
        result = loader.get_walking_transfers()
        assert result == {}

    def test_get_walking_transfers_with_data(self, loader, tmp_db):
        conn = sqlite3.connect(tmp_db)
        conn.execute(
            "INSERT INTO walking_transfers (from_atco, to_atco, walk_seconds) VALUES (?, ?, ?)",
            ("ATCO001", "ATCO002", 120),
        )
        conn.execute(
            "INSERT INTO walking_transfers (from_atco, to_atco, walk_seconds) VALUES (?, ?, ?)",
            ("ATCO001", "ATCO003", 300),
        )
        conn.commit()
        conn.close()

        result = loader.get_walking_transfers()
        assert "ATCO001" in result
        assert result["ATCO001"]["ATCO002"] == 120
        assert result["ATCO001"]["ATCO003"] == 300

    def test_get_walking_transfers_no_table(self, tmp_db):
        """get_walking_transfers on DB without schema should not crash."""
        bl = BusLoader(tmp_db)
        bl.ensure_db()
        # Intentionally do NOT call create_schema
        result = bl.get_walking_transfers()
        assert result == {}

    def test_clear_walking_transfers(self, loader, tmp_db):
        conn = sqlite3.connect(tmp_db)
        conn.execute(
            "INSERT INTO walking_transfers VALUES (?, ?, ?)",
            ("A", "B", 100),
        )
        conn.execute(
            "INSERT INTO stop_coords VALUES (?, ?, ?)",
            ("A", 54.0, -2.8),
        )
        conn.commit()
        conn.close()

        loader.clear_walking_transfers()
        assert loader.get_walking_transfers() == {}
        assert loader.get_all_stop_coords() == {}

    def test_clear_walking_transfers_no_table(self, tmp_db):
        """clear_walking_transfers on DB without schema should not crash."""
        bl = BusLoader(tmp_db)
        bl.ensure_db()
        # Intentionally do NOT call create_schema
        bl.clear_walking_transfers()  # Should not raise


# ═══════════════════════════════════════════════════════════════════════
#  Stop names & coords
# ═══════════════════════════════════════════════════════════════════════

class TestStopOperations:
    def test_get_stop_names_bulk_empty(self, loader):
        result = loader.get_stop_names_bulk(set())
        assert result == {}

    def test_get_stop_names_bulk_with_data(self, loader, tmp_db):
        conn = sqlite3.connect(tmp_db)
        conn.execute(
            "INSERT INTO stop_names (atco_code, common_name, indicator, locality) VALUES (?, ?, ?, ?)",
            ("ATCO001", "Central Station", "Stop A", "Lancaster"),
        )
        conn.commit()
        conn.close()

        result = loader.get_stop_names_bulk({"ATCO001"})
        assert result["ATCO001"] == "Central Station"

    def test_get_stop_names_bulk_missing(self, loader):
        result = loader.get_stop_names_bulk({"NONEXIST"})
        assert result == {}

    def test_search_stops_empty_query(self, loader):
        assert loader.search_stops("") == []

    def test_search_stops_with_data(self, loader, tmp_db):
        conn = sqlite3.connect(tmp_db)
        conn.execute(
            "INSERT INTO stop_names VALUES (?, ?, ?, ?)",
            ("ATCO001", "Lancaster Bus Station", "Bay 1", "Lancaster"),
        )
        conn.execute(
            "INSERT INTO stop_coords VALUES (?, ?, ?)",
            ("ATCO001", 54.0480, -2.8010),
        )
        conn.commit()
        conn.close()

        results = loader.search_stops("Lancaster")
        assert len(results) == 1
        assert results[0]["name"] == "Lancaster Bus Station"
        assert results[0]["lat"] == 54.0480

    def test_search_stops_limit(self, loader, tmp_db):
        conn = sqlite3.connect(tmp_db)
        for i in range(20):
            conn.execute(
                "INSERT INTO stop_names VALUES (?, ?, ?, ?)",
                (f"ATCO{i:03d}", f"Test Stop {i}", "", ""),
            )
        conn.commit()
        conn.close()

        results = loader.search_stops("Test", limit=5)
        assert len(results) == 5

    def test_get_all_stop_coords(self, loader, tmp_db):
        conn = sqlite3.connect(tmp_db)
        conn.execute("INSERT INTO stop_coords VALUES (?, ?, ?)",
                     ("A1", 54.0, -2.8))
        conn.execute("INSERT INTO stop_coords VALUES (?, ?, ?)",
                     ("A2", 54.1, -2.9))
        conn.commit()
        conn.close()

        result = loader.get_all_stop_coords()
        assert len(result) == 2
        assert result["A1"] == (54.0, -2.8)

    def test_get_stop_count(self, loader, tmp_db):
        conn = sqlite3.connect(tmp_db)
        conn.execute("INSERT INTO stop_names VALUES (?, ?, ?, ?)",
                     ("A1", "Stop 1", "", ""))
        conn.execute("INSERT INTO stop_names VALUES (?, ?, ?, ?)",
                     ("A2", "Stop 2", "", ""))
        conn.commit()
        conn.close()
        assert loader.get_stop_count() == 2


# ═══════════════════════════════════════════════════════════════════════
#  Duration / time parsing helpers
# ═══════════════════════════════════════════════════════════════════════

class TestBusLoaderHelpers:
    def test_parse_duration_full(self):
        assert BusLoader._parse_duration("PT1H30M45S") == 5445

    def test_parse_duration_minutes_only(self):
        assert BusLoader._parse_duration("PT5M") == 300

    def test_parse_duration_seconds_only(self):
        assert BusLoader._parse_duration("PT30S") == 30

    def test_parse_duration_none(self):
        assert BusLoader._parse_duration(None) == 0

    def test_parse_duration_invalid(self):
        assert BusLoader._parse_duration("INVALID") == 0

    def test_hms_to_seconds(self):
        assert BusLoader._hms_to_seconds("12:30:45") == 45045

    def test_hms_to_seconds_midnight(self):
        assert BusLoader._hms_to_seconds("00:00:00") == 0


# ═══════════════════════════════════════════════════════════════════════
#  Pickle cache
# ═══════════════════════════════════════════════════════════════════════

class TestPickleCache:
    def test_cache_path(self, loader, tmp_db):
        expected = tmp_db.replace(".db", ".cache")
        assert loader._cache_path == expected

    def test_save_and_load_cache(self, loader):
        import pickle
        fake_data = {"stops": [1, 2, 3]}
        loader.save_cache(fake_data)
        loaded = loader.load_cache()
        assert loaded is not None
        assert loaded["stops"] == [1, 2, 3]

    def test_load_cache_missing(self, loader):
        # No cache file saved yet
        result = loader.load_cache()
        assert result is None


# ═══════════════════════════════════════════════════════════════════════
#  Dataset meta / download URLs
# ═══════════════════════════════════════════════════════════════════════

class TestDatasetMeta:
    def test_get_download_urls_empty(self, loader):
        """With empty dataset_meta and no network, returns empty list."""
        from unittest.mock import patch
        with patch.object(loader, '_fetch_dataset_info', return_value=[]):
            result = loader.get_download_urls()
        assert result == []

    def test_get_download_urls_filters_existing(self, loader, tmp_db):
        """URLs already in dataset_meta are excluded."""
        from unittest.mock import patch
        conn = sqlite3.connect(tmp_db)
        conn.execute(
            "INSERT INTO dataset_meta VALUES (?, ?, ?)",
            ("http://src", "http://download/old", "2025-01-01"),
        )
        conn.commit()
        conn.close()

        datasets = [
            {"source_url": "http://src", "download_url": "http://download/old", "modified": "2025-01-01"},
            {"source_url": "http://src2", "download_url": "http://download/new", "modified": "2025-06-01"},
        ]
        with patch.object(loader, '_fetch_dataset_info', return_value=datasets):
            result = loader.get_download_urls()
        assert "http://download/new" in result
        assert "http://download/old" not in result
