from typing import Any, Dict

from db import get_connection


def search_stops(query: str, limit: int = 20) -> Dict[str, Any]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT atco_code, name, stop_type, latitude, longitude, locality
                FROM atco_stops
                WHERE name ILIKE %s OR atco_code ILIKE %s
                ORDER BY name
                LIMIT %s
                """,
                (f"%{query}%", f"%{query}%", limit),
            )
            rows = cur.fetchall()
            results = []
            for row in rows:
                results.append(
                    {
                        "code": row[0],
                        "name": row[1],
                        "type": row[2],
                        "lat": float(row[3]) if row[3] is not None else None,
                        "lon": float(row[4]) if row[4] is not None else None,
                        "locality": row[5],
                    }
                )
            return {"results": results}
    finally:
        conn.close()
