from datetime import datetime
from typing import Any, Dict, List

from cachetools import TTLCache

from bus_data import BusData
from merged_data import MergedData
from raptor_router import RaptorRouter
from walking import Walking
from db import get_connection
from time_utils import seconds_since_midnight


PLANNER_CACHE = TTLCache(maxsize=1, ttl=300)


def _load_bus_data() -> BusData:
    bus = BusData(0, 0, 0)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.route_code, rs.stop_code, rs.stop_sequence
                FROM route_stops rs
                JOIN routes r ON r.id = rs.route_id
                WHERE r.mode = 'bus'
                ORDER BY r.route_code, rs.stop_sequence
                """
            )
            rows = cur.fetchall()

            route_map: Dict[str, List[str]] = {}
            for route_code, stop_code, _seq in rows:
                route_map.setdefault(route_code, []).append(stop_code)

            for route_code, stops in route_map.items():
                bus.add_route(route_code, stops, None)

            cur.execute(
                """
                SELECT j.journey_code, r.route_code, jt.stop_code, jt.stop_sequence, jt.arrival_time
                FROM journeys j
                JOIN routes r ON r.id = j.route_id
                JOIN journey_times jt ON jt.journey_id = j.id
                WHERE r.mode = 'bus'
                ORDER BY j.journey_code, jt.stop_sequence
                """
            )
            rows = cur.fetchall()

            journey_map: Dict[str, Dict[str, Any]] = {}
            for journey_code, route_code, stop_code, _seq, arrival_time in rows:
                entry = journey_map.setdefault(journey_code, {"route": route_code, "times": []})
                entry["times"].append((stop_code, arrival_time))

            for journey_code, entry in journey_map.items():
                bus.add_journey(journey_code, entry["route"], entry["times"])

    finally:
        conn.close()

    return bus


def _get_cached_bus_data() -> BusData:
    cached = PLANNER_CACHE.get("bus")
    if cached is not None:
        return cached
    bus = _load_bus_data()
    PLANNER_CACHE["bus"] = bus
    return bus


def _parse_departure_time(value: str) -> int:
    try:
        dt = datetime.fromisoformat(value)
        return dt.hour * 3600 + dt.minute * 60 + dt.second
    except Exception:
        return seconds_since_midnight(value)


def plan_journey(from_stop: str, to_stop: str, departure_time: str, max_transfers: int = 3) -> Dict[str, Any]:
    bus = _get_cached_bus_data()
    network = MergedData(bus, None)
    router = RaptorRouter(network, network, network)
    walking = Walking(stop_mapper=bus.map_stops)

    start_time = _parse_departure_time(departure_time)
    route = router.route(
        n_transfer_limit=max_transfers,
        walking=walking,
        start_date="",
        start_time=start_time,
        start_point=from_stop,
        destination=to_stop,
    )

    # Order legs from origin to destination
    origin_id = None
    for stop_id, info in route.items():
        if info.get("is_origin"):
            origin_id = stop_id
            break

    ordered: List[Dict[str, Any]] = []
    track = origin_id
    visited = set()
    while track is not None and track in route and track not in visited:
        visited.add(track)
        info = route[track]
        ordered.append(
            {
                "stop_id": track,
                "stop_name": info.get("stop_name", ""),
                "arrival_time": info.get("arrival_time"),
                "mode": info.get("type"),
                "journey": info.get("journey"),
                "next_stop_id": None,
            }
        )
        # find next stop (where prev_stop == track)
        next_stop = None
        for candidate, data in route.items():
            if data.get("prev_stop") == track:
                next_stop = candidate
                break
        track = next_stop

    for idx in range(len(ordered) - 1):
        ordered[idx]["next_stop_id"] = ordered[idx + 1]["stop_id"]

    return {
        "from": from_stop,
        "to": to_stop,
        "departureTime": departure_time,
        "legs": ordered,
    }
