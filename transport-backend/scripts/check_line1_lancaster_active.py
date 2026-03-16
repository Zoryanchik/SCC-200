"""Regression check: Lancaster line 1 must not be filtered out by invalid service periods.

Why this exists
--------------
We discovered broken rows in `bus_service_operating_period` (e.g. empty end_date)
that could incorrectly exclude otherwise-valid journeys for a given date.

This script asserts that for a known date/service/line, BusLoader includes line 1.

Usage
-----
Run from transport-backend/:

    python3 scripts/check_line1_lancaster_active.py

It exits non-zero on failure.
"""

from __future__ import annotations

import os
import sys

# Allow running the script from either repo root or transport-backend/.
_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.dirname(_HERE)
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from bus_loader import BusLoader
from main import BUS_DB_PATH, WALK_DB_PATH


TARGET_DATE = "2026-03-16"
EXPECTED_SERVICE = "PC0002407:417"
EXPECTED_LINE_NAME = f"{EXPECTED_SERVICE}:1"


def main() -> int:
    loader = BusLoader(BUS_DB_PATH, walking_db_path=WALK_DB_PATH)
    bd = loader.load_busdata_for_date(TARGET_DATE)

    # route_metadata is indexed by route_int, entries look like:
    #   {"route_id": "...::PC0002407:417:RS8", "line_name": "PC0002407:417:1"}
    route_meta = getattr(bd, "route_metadata", None) or []

    line_routes = [
        m
        for m in route_meta
        if isinstance(m, dict)
        and (m.get("line_name") == EXPECTED_LINE_NAME)
        and (m.get("route_id") or "").find(EXPECTED_SERVICE) >= 0
    ]

    if not line_routes:
        # Provide a bit of debugging context without dumping the whole DB.
        seen = sorted(
            {
                m.get("line_name")
                for m in route_meta
                if isinstance(m, dict) and (m.get("line_name") or "").startswith(EXPECTED_SERVICE)
            }
        )
        raise SystemExit(
            "FAIL: No route_metadata rows for line 1. "
            f"date={TARGET_DATE} expected_line={EXPECTED_LINE_NAME}. "
            f"service lines seen={seen}"
        )

    print(
        "PASS:",
        f"Found {len(line_routes)} route(s) for {EXPECTED_LINE_NAME} on {TARGET_DATE}.",
    )
    for m in line_routes[:10]:
        print(" ", m.get("route_id"))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
