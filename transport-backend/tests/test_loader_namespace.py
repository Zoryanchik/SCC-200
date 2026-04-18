import os
import sys
import importlib
import pytest

# Compute the DSN directly instead of importing from main, because earlier
# test modules (test_bus_live) may replace sys.modules["main"] with a MagicMock.
_DEFAULT_PG = "postgresql://pguser:pgpass@127.0.0.1:5011/transport"
BUS_DB_PATH = os.environ.get("BUS_DB_DSN") or _DEFAULT_PG

# Ensure we have the *real* bus_loader module, not a MagicMock that
# test_bus_live may have injected into sys.modules.
from unittest.mock import MagicMock as _MagicMock
if "bus_loader" in sys.modules and isinstance(sys.modules["bus_loader"], _MagicMock):
    del sys.modules["bus_loader"]
import bus_loader as _bl_mod
if isinstance(_bl_mod, _MagicMock):
    importlib.invalidate_caches()
    del sys.modules["bus_loader"]
    _bl_mod = importlib.import_module("bus_loader")
BusLoader = _bl_mod.BusLoader


def _connect_or_skip(loader: BusLoader):
    """Return a DB connection, or skip this test module if DB isn't reachable."""
    try:
        return loader._connect()
    except Exception as e:
        pytest.skip(f"DB not reachable for integration-style loader test: {e}")


def _cleanup(conn, prefix):
    cur = conn.cursor()
    cur.execute("DELETE FROM bus_journey_operating_profile WHERE journey_id LIKE %s", (f"{prefix}%",))
    cur.execute("DELETE FROM bus_journey_times WHERE journey_id LIKE %s", (f"{prefix}%",))
    cur.execute("DELETE FROM bus_journey_routes WHERE journey_id LIKE %s", (f"{prefix}%",))
    cur.execute("DELETE FROM bus_route_stops WHERE route_id LIKE %s", (f"{prefix}%",))
    cur.execute("DELETE FROM bus_route_section_tracks WHERE route_id LIKE %s", (f"{prefix}%",))
    conn.commit()


def test_per_file_namespacing_keeps_variants(tmp_path):
    """Load two known-colliding XMLs under distinct per-file tags and
    assert both produce distinct namespaced route_ids and contiguous stop_order.
    """
    ld = BusLoader(BUS_DB_PATH)
    conn = _connect_or_skip(ld)

    # anomaly_sources are stored under transport-backend/transport-backend/tmp/anomaly_sources
    base = os.path.join(os.path.dirname(__file__), "..", "transport-backend", "tmp", "anomaly_sources")
    f1 = os.path.join(base, 'download__KE44-None--SCCU-NWKE-2026-03-02-Summer_2026_AMBLESIDE_CLOSED__SCCU_PC0002407_416_20260323-BODS_V1_1.xml')
    f2 = os.path.join(base, 'download__WC30-None--SCCU-NWLI-2025-09-01-LL_23_03_2026_Summer_2026__SCCU_PC0002407_109_20260323-BODS_V1_1.xml')

    # Use TEST prefixes to avoid colliding with existing data
    tag1 = 'TESTKE44'
    tag2 = 'TESTWC30'

    # Clean any previous test artifacts
    _cleanup(conn, tag1)
    _cleanup(conn, tag2)

    # If the test fixtures are missing in this environment, skip the test.
    if not (os.path.exists(f1) and os.path.exists(f2)):
        pytest.skip(f"Test fixtures missing: {f1} or {f2} not found")

    # Parse and insert each file under its own tag
    r1 = ld._parse_file(f1)
    r2 = ld._parse_file(f2)
    ld.populate(r1[0], r1[1], r1[2], r1[3], r1[4], r1[5], r1[6], r1[7], tag=tag1)
    ld.populate(r2[0], r2[1], r2[2], r2[3], r2[4], r2[5], r2[6], r2[7], tag=tag2)

    # Re-open connection so we see the committed data from populate()
    conn.close()
    conn = _connect_or_skip(ld)
    conn.autocommit = True
    cur = conn.cursor()
    # Verify distinct namespaced route_ids exist
    cur.execute("SELECT DISTINCT route_id FROM bus_route_stops WHERE route_id LIKE %s ORDER BY route_id", (f"{tag1}%",))
    rows1 = [r[0] for r in cur.fetchall()]
    cur.execute("SELECT DISTINCT route_id FROM bus_route_stops WHERE route_id LIKE %s ORDER BY route_id", (f"{tag2}%",))
    rows2 = [r[0] for r in cur.fetchall()]

    assert rows1, "No routes inserted for first file"
    assert rows2, "No routes inserted for second file"

    # For each inserted route, ensure stop_order is contiguous within that route
    for route_id in rows1 + rows2:
        cur.execute("SELECT COUNT(*) as cnt, MIN(stop_order), MAX(stop_order) FROM bus_route_stops WHERE route_id=%s", (route_id,))
        cnt, mn, mx = cur.fetchone()
        assert cnt == (mx - mn + 1), f"Non-contiguous stop_order in {route_id}"

    # Cleanup test data
    _cleanup(conn, tag1)
    _cleanup(conn, tag2)
    conn.close()


def test_revision_aware_ingest_ignores_missing_revision():
    """When the DB already holds rows with a non-zero revision and an incoming
    file has no RevisionNumber (revision=None → 0), the incoming rows are
    ignored and the existing higher-revision data is preserved.

    When a higher revision arrives later it replaces the existing data.
    """
    ld = BusLoader(BUS_DB_PATH)

    # Skip early if DB isn't reachable.
    conn_probe = _connect_or_skip(ld)
    conn_probe.close()

    # Ensure schema has revision columns
    try:
        ld.create_schema()
    except Exception as e:
        pytest.skip(f"DB not reachable for integration-style loader test: {e}")

    tag = 'TESTREV'
    conn = _connect_or_skip(ld)
    conn.autocommit = True
    _cleanup(conn, tag)

    # ---- Step 1: Insert data with revision=100 ----
    route_stops = [('R1', 'STOP_A', 0), ('R1', 'STOP_B', 1)]
    journey_routes = [('J1', 'R1', 'Line1', 'DestA')]
    journey_times = [('J1', 'STOP_A', 28800), ('J1', 'STOP_B', 29100)]
    ld.populate(route_stops, journey_routes, journey_times,
                revision=100, tag=tag)

    cur = conn.cursor()
    cur.execute("SELECT revision FROM bus_journey_routes WHERE journey_id = %s", (f'{tag}::J1',))
    row = cur.fetchone()
    assert row is not None, "Journey J1 should have been inserted"
    assert row[0] == 100, f"Expected revision 100, got {row[0]}"

    # ---- Step 2: Insert same IDs with revision=None (missing) ----
    # This should be IGNORED because existing revision (100) > incoming (0)
    route_stops2 = [('R1', 'STOP_A', 0), ('R1', 'STOP_C', 1)]  # different stops
    journey_routes2 = [('J1', 'R1', 'Line_NEW', 'DestB')]
    journey_times2 = [('J1', 'STOP_A', 30000), ('J1', 'STOP_C', 30300)]
    ld.populate(route_stops2, journey_routes2, journey_times2,
                revision=None, tag=tag)

    # Re-open to see committed state
    conn.close()
    conn = _connect_or_skip(ld)
    conn.autocommit = True
    cur = conn.cursor()

    # Journey data should still be the original (revision=100)
    cur.execute("SELECT line_name, revision FROM bus_journey_routes WHERE journey_id = %s", (f'{tag}::J1',))
    row = cur.fetchone()
    assert row is not None, "Journey J1 should still exist"
    assert row[0] == 'Line1', f"line_name should still be 'Line1' but got '{row[0]}'"
    assert row[1] == 100, f"revision should still be 100 but got {row[1]}"

    # Route stops should still have STOP_B, not STOP_C
    cur.execute("SELECT atco_code FROM bus_route_stops WHERE route_id = %s ORDER BY stop_order", (f'{tag}::R1',))
    stops = [r[0] for r in cur.fetchall()]
    assert 'STOP_B' in stops, f"Expected STOP_B in stops but got {stops}"
    assert 'STOP_C' not in stops, f"STOP_C should NOT have been inserted but stops = {stops}"

    # ---- Step 3: Insert same IDs with revision=200 (higher) ----
    # This should REPLACE the existing revision=100 data
    route_stops3 = [('R1', 'STOP_A', 0), ('R1', 'STOP_D', 1)]
    journey_routes3 = [('J1', 'R1', 'Line_V2', 'DestC')]
    journey_times3 = [('J1', 'STOP_A', 31000), ('J1', 'STOP_D', 31300)]
    ld.populate(route_stops3, journey_routes3, journey_times3,
                revision=200, tag=tag)

    conn.close()
    conn = _connect_or_skip(ld)
    conn.autocommit = True
    cur = conn.cursor()

    cur.execute("SELECT line_name, revision FROM bus_journey_routes WHERE journey_id = %s", (f'{tag}::J1',))
    row = cur.fetchone()
    assert row is not None, "Journey J1 should still exist after rev=200 insert"
    assert row[0] == 'Line_V2', f"line_name should be 'Line_V2' but got '{row[0]}'"
    assert row[1] == 200, f"revision should be 200 but got {row[1]}"

    # Route stops should now have STOP_D
    cur.execute("SELECT atco_code FROM bus_route_stops WHERE route_id = %s ORDER BY stop_order", (f'{tag}::R1',))
    stops = [r[0] for r in cur.fetchall()]
    assert 'STOP_D' in stops, f"Expected STOP_D in stops but got {stops}"

    # Cleanup
    _cleanup(conn, tag)
    conn.close()


def test_bus_route_stops_primary_key_is_route_and_stop_order():
    """Schema contract: repeated ATCOs in loops require PK(route_id, stop_order)."""
    ld = BusLoader(BUS_DB_PATH)
    conn = _connect_or_skip(ld)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conrelid = 'bus_route_stops'::regclass
              AND contype = 'p'
            """
        )
        row = cur.fetchone()
        assert row is not None, "bus_route_stops primary key constraint missing"
        assert "PRIMARY KEY (route_id, stop_order)" in row[0]
    finally:
        conn.close()
