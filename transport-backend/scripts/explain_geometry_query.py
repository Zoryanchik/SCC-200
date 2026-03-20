"""Explain/analyze the geometry fragment query used during bus day-load.

This targets the slowest phase we saw in DAY_LOAD_TIMING: phase_geometry
(bus_route_section_tracks JOIN _valid_routes ORDER BY ...).

It builds a representative _valid_routes temp table for a given date
(from bus_journey_operating_profile -> valid journeys -> valid routes)
then runs EXPLAIN (ANALYZE, BUFFERS) on the exact geometry query.

Usage:
  python3 scripts/explain_geometry_query.py 2026-03-30

Optional env:
  DB_URL=postgresql://postgres:postgres@127.0.0.1:5011/transport
"""

from __future__ import annotations

import os
import sys
from datetime import date as _date


def _connect():
    import psycopg

    db_url = os.environ.get(
        "DB_URL", "postgresql://pguser:pgpass@127.0.0.1:5011/transport"
    )
    return psycopg.connect(db_url)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: explain_geometry_query.py YYYY-MM-DD", file=sys.stderr)
        return 2

    day = argv[1]
    _date.fromisoformat(day)  # validate

    # We reuse the same day-validity logic embedded in BusLoader by importing it
    # and calling its method that computes valid journeys/routes.
    # To avoid invasive refactors, we just mirror the minimum SQL needed.

    conn = _connect()
    cur = conn.cursor()

    # Keep a single transaction open so our TEMP tables stay alive.
    # (They are defined ON COMMIT DROP.)
    conn.autocommit = False

    # Temp tables (isolated to this session).
    cur.execute("DROP TABLE IF EXISTS _valid_journeys")
    cur.execute("DROP TABLE IF EXISTS _valid_routes")
    cur.execute("CREATE TEMP TABLE _valid_journeys (journey_id TEXT PRIMARY KEY) ON COMMIT DROP")

    # NOTE: This is a simplified approximation: it selects journeys whose
    # start/end date range includes 'day' and whose DOW mask matches.
    # If your serviced-org logic matters for correctness of the explain plan,
    # we can port that too, but for query planning the route cardinality is
    # the important factor.
    qd = _date.fromisoformat(day)
    dow_bit = 1 << qd.weekday()

    cur.execute(
        """
        INSERT INTO _valid_journeys (journey_id)
        SELECT journey_id
        FROM bus_journey_operating_profile
        WHERE (start_date IS NULL OR start_date <= %s)
          AND (end_date   IS NULL OR end_date   >= %s)
          AND (days_of_week::int & %s) <> 0
        """,
        (day, day, dow_bit),
    )

    cur.execute("CREATE TEMP TABLE _valid_routes (route_id TEXT PRIMARY KEY) ON COMMIT DROP")
    cur.execute(
        """
        INSERT INTO _valid_routes (route_id)
        SELECT DISTINCT jr.route_id
        FROM bus_journey_routes jr
        JOIN _valid_journeys vj ON jr.journey_id = vj.journey_id
        """
    )

    cur.execute("SELECT COUNT(*) FROM _valid_routes")
    n_routes = cur.fetchone()[0]
    print(f"valid_routes={n_routes}")

    cur.execute(
        """
        EXPLAIN (ANALYZE, BUFFERS)
        SELECT st.route_id, st.section_id, st.from_atco, st.to_atco, st.seq, st.lat, st.lon
        FROM bus_route_section_tracks st
        JOIN _valid_routes vr ON st.route_id = vr.route_id
        ORDER BY st.route_id, st.section_id, st.from_atco, st.to_atco, st.seq
        """
    )

    for row in cur.fetchall():
        print(row[0])

    # End the transaction; temp tables are ON COMMIT DROP.
    conn.rollback()
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
