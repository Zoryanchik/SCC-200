import math
from typing import Dict, Tuple

from cache import STOP_CACHE
from db import get_connection


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371000
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * radius * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _load_stop_coords() -> Dict[str, Tuple[float, float]]:
    cached = STOP_CACHE.get("coords")
    if cached is not None:
        return cached

    conn = get_connection()
    coords: Dict[str, Tuple[float, float]] = {}
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT atco_code, latitude, longitude
                FROM atco_stops
                WHERE latitude IS NOT NULL AND longitude IS NOT NULL
                """
            )
            for code, lat, lon in cur.fetchall():
                coords[code] = (float(lat), float(lon))
    finally:
        conn.close()

    STOP_CACHE["coords"] = coords
    return coords


def find_nearby_stops(lat: float, lon: float, radius_m: float) -> Dict[int, float]:
    coords = _load_stop_coords()
    nearby: Dict[int, float] = {}
    for code, (s_lat, s_lon) in coords.items():
        dist = _haversine(lat, lon, s_lat, s_lon)
        if dist <= radius_m:
            # simple walking speed: 1.4 m/s
            nearby[code] = dist / 1.4
    return nearby


def resolve_location(location: str) -> Tuple[float, float]:
    coords = _load_stop_coords()
    if location in coords:
        return coords[location]
    raise ValueError("Unknown location")
