from db import get_connection


def ensure_schema() -> None:
    ddl = [
        """
        CREATE TABLE IF NOT EXISTS routes (
            id SERIAL PRIMARY KEY,
            route_code TEXT NOT NULL,
            mode TEXT NOT NULL,
            operator_code TEXT,
            line_name TEXT,
            direction TEXT,
            description TEXT,
            UNIQUE (route_code, mode)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS route_stops (
            id SERIAL PRIMARY KEY,
            route_id INTEGER NOT NULL REFERENCES routes(id) ON DELETE CASCADE,
            stop_code TEXT NOT NULL,
            stop_sequence INTEGER NOT NULL,
            UNIQUE (route_id, stop_sequence)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS journeys (
            id SERIAL PRIMARY KEY,
            route_id INTEGER NOT NULL REFERENCES routes(id) ON DELETE CASCADE,
            journey_code TEXT NOT NULL UNIQUE,
            departure_time INTEGER,
            operating_date DATE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS journey_times (
            id SERIAL PRIMARY KEY,
            journey_id INTEGER NOT NULL REFERENCES journeys(id) ON DELETE CASCADE,
            stop_code TEXT NOT NULL,
            stop_sequence INTEGER NOT NULL,
            arrival_time INTEGER NOT NULL,
            UNIQUE (journey_id, stop_sequence)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_route_stops_stop_code ON route_stops(stop_code)",
        "CREATE INDEX IF NOT EXISTS idx_journey_times_stop_code ON journey_times(stop_code)",
    ]

    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                for statement in ddl:
                    cur.execute(statement)
    finally:
        conn.close()
