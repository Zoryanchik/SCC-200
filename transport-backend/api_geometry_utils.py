"""Geometry and journey DB helpers used by API route geometry endpoints."""

import json
from urllib.error import URLError
from urllib.request import Request as UrllibRequest, urlopen

import psycopg


def _get_db_connection():
    from main import BUS_DB_PATH

    return psycopg.connect(BUS_DB_PATH)


def fetch_logged_journey_from_db(ljid: str):
    """Return the JSON object stored in bus_journeys for id=ljid, or None."""
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT journey FROM bus_journeys WHERE id = %s", (ljid,))
        row = cur.fetchone()
        conn.close()
        if not row:
            return None
        return row[0]
    except Exception:
        return None


def fetch_journey_times_external(journey_id: str):
    """Return ordered list of (atco_code, arrival_time) for external journey_id."""
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT atco_code, arrival_time FROM bus_journey_times WHERE journey_id = %s ORDER BY arrival_time",
            (journey_id,),
        )
        rows = cur.fetchall()
        conn.close()
        return [(row[0], row[1]) for row in rows]
    except Exception:
        return []


def query_osrm_for_coords(osrm_base: str, coords_lonlat: list):
    """Call OSRM driving route; return list of [lat, lon] or None on failure."""
    return query_osrm_for_coords_profile(osrm_base, coords_lonlat, profile="driving")


def query_osrm_for_coords_profile(osrm_base: str, coords_lonlat: list, profile: str = "driving"):
    """Call OSRM route for profile; return list of [lat, lon] or None on failure."""
    if not coords_lonlat:
        return None
    coords_str = ";".join(coords_lonlat)
    url = osrm_base.rstrip("/") + f"/route/v1/{profile}/{coords_str}?overview=full&geometries=geojson"
    try:
        req = UrllibRequest(url, headers={"User-Agent": "transport-backend"})
        with urlopen(req, timeout=10) as resp:
            data = json.load(resp)
        if data.get("code") != "Ok":
            return None
        routes = data.get("routes") or []
        if not routes:
            return None
        geom = routes[0].get("geometry")
        if not geom:
            return None
        return [[coord[1], coord[0]] for coord in geom.get("coordinates", [])]
    except URLError:
        return None
    except Exception:
        return None


def fetch_route_tracks(route_id: str):
    """Return route tracks as [lat, lon] list from bus_route_tracks; [] on failure."""
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT lat, lon FROM bus_route_tracks WHERE route_id = %s ORDER BY seq", (route_id,))
        rows = cur.fetchall()
        conn.close()
        return [[row[0], row[1]] for row in rows]
    except Exception:
        return []


def _haversine(lat1, lon1, lat2, lon2):
    import math

    r = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def subsegment_from_tracks(route_id: str, stop_atcos: list, walking_coords: dict):
    """Return route track subsegment that spans provided stop ATCO codes."""
    if not stop_atcos:
        return []
    tracks = fetch_route_tracks(route_id)
    if not tracks:
        return []

    indices = []
    for atco in stop_atcos:
        coord = walking_coords.get(atco) if walking_coords else None
        if not coord:
            continue
        lat_s, lon_s = coord[0], coord[1]
        best_i = None
        best_d = None
        for i, (tlat, tlon) in enumerate(tracks):
            d = _haversine(lat_s, lon_s, tlat, tlon)
            if best_d is None or d < best_d:
                best_d = d
                best_i = i
        if best_i is not None:
            indices.append(best_i)

    if not indices:
        return []
    start, end = min(indices), max(indices)
    if start <= end:
        return tracks[start : end + 1]
    return list(reversed(tracks[end : start + 1]))


def sample_coords_for_osrm(points, max_samples=20):
    """Return a list of 'lon,lat' strings sampled evenly from points."""
    if not points:
        return []
    n = len(points)
    if n <= max_samples:
        return [f"{point[1]},{point[0]}" for point in points]
    step = max(1, n // max_samples)
    sampled = [points[i] for i in range(0, n, step)]
    if sampled[-1] != points[-1]:
        sampled.append(points[-1])
    return [f"{point[1]},{point[0]}" for point in sampled]
