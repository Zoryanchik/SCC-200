import json
import math
import urllib.request


class Walking:
    """Walking transfers powered by OSRM and a precomputed transfer table.

    Parameters
    ----------
    inter_walk_table : dict
        {stop_int: {stop_int: walk_seconds}} — precomputed from
        BusLoader.get_walking_transfers(), remapped to merged-data
        stop integers.
    stop_coords : dict
        {stop_int: (lat, lon)} — coordinates for every stop in
        merged-data space.
    osrm_base : str
        Base URL of a running OSRM foot-profile server.
    max_walk_seconds : int
        Maximum walk duration to consider (default 600 = 10 min).
    """

    def __init__(self, inter_walk_table, stop_coords,
                 osrm_base="http://localhost:5001",
                 max_walk_seconds=600):
        self._inter = inter_walk_table          # {stop_int: {stop_int: secs}}
        self._coords = stop_coords              # {stop_int: (lat, lon)}
        self._osrm = osrm_base
        self._max = max_walk_seconds

    # ── transfers between transit stops (precomputed) ────────────

    def inter_walk(self, stop_int):
        """Return {nearby_stop_int: walk_seconds} from precomputed table."""
        return self._inter.get(stop_int, {})

    # ── reachable stops from an arbitrary (lat, lon) ─────────────

    def reachable_stops(self, location):
        """Return {stop_int: walk_seconds} for stops walkable from *location*.

        *location* is a (lat, lon) tuple representing an arbitrary point
        (the user's start or end position).
        """
        lat, lon = location

        # 1. Candidate stops within bounding box (~1.3 km)
        margin = 0.012
        candidates = []
        exact_matches = {}  # {stop_int: walk_seconds} for exact coordinate matches
        for stop_int, (slat, slon) in self._coords.items():
            if abs(slat - lat) <= margin and abs(slon - lon) <= margin:
                candidates.append(stop_int)
                # Check for exact coordinate match (within ~1 meter precision)
                if abs(slat - lat) < 1e-5 and abs(slon - lon) < 1e-5:
                    exact_matches[stop_int] = 0

        if not candidates:
            return {}

        # 2. Build OSRM /table request: source = user location,
        #    destinations = candidate stops.
        #
        #    IMPORTANT — Coordinate order difference:
        #      • Internal data uses (lat, lon) tuples throughout.
        #      • OSRM expects "lon,lat" in its URL (GeoJSON / WGS-84 order).
        #      • routeGeometries returned by api.py use [lat, lon] (Leaflet).
        #    We reverse the order here for the OSRM request only.

        coords_parts = [f"{lon},{lat}"]           # OSRM: lon,lat
        for s in candidates:
            slat, slon = self._coords[s]
            coords_parts.append(f"{slon},{slat}")  # OSRM: lon,lat
        coord_str = ";".join(coords_parts)

        url = (
            f"{self._osrm}/table/v1/foot/{coord_str}"
            f"?sources=0&annotations=duration"
        )
        try:
            resp = urllib.request.urlopen(url, timeout=10)
            data = json.loads(resp.read())
            resp.close()
        except Exception:
            # OSRM not available, fall back to distance-based estimate
            result = {}
            for stop_int in candidates:
                slat, slon = self._coords[stop_int]
                # Rough walking time estimate: 1 m/s = 60 seconds per 60 meters
                dist_m = math.sqrt((slat - lat)**2 + (slon - lon)**2) * 111000
                walk_time = int(dist_m / 1.0)  # 1 m/s walking speed
                if walk_time <= self._max:
                    result[stop_int] = walk_time
            sorted_result = {k: v for k, v in sorted(result.items(), key=lambda item: item[1])}
            # Include exact matches (stops at the user's exact location)
            sorted_result.update(exact_matches)
            return sorted_result

        if data.get("code") != "Ok":
            return dict(sorted(exact_matches.items()))

        durations = data["durations"][0]        # single-source row
        result = {}
        for i, dur in enumerate(durations):
            if i == 0:
                continue                        # skip self (user location)
            if dur is not None and dur <= self._max:
                result[candidates[i - 1]] = int(dur)

        # Sort by walk_seconds ascending
        sorted_result = {k: v for k, v in sorted(result.items(), key=lambda item: item[1])}
        # Include exact matches (stops at the user's exact location)
        sorted_result.update(exact_matches)
        return sorted_result

    def walking_time_between(self, point_a, point_b):
        """
        Compute walking time (seconds) between two arbitrary (lat, lon) points.
        Uses OSRM if available, otherwise falls back to haversine estimate.
        """
        lat1, lon1 = point_a
        lat2, lon2 = point_b
        url = f"{self._osrm}/route/v1/foot/{lon1},{lat1};{lon2},{lat2}?overview=false&steps=false&annotations=duration"
        try:
            resp = urllib.request.urlopen(url, timeout=10)
            data = json.loads(resp.read())
            resp.close()
            if data.get("code") == "Ok" and data["routes"]:
                return int(data["routes"][0]["duration"])
        except Exception:
            pass
        # Fallback: haversine distance, 1 m/s walking speed
        dist_m = math.sqrt((lat1 - lat2)**2 + (lon1 - lon2)**2) * 111000
        return int(dist_m / 1.0)
