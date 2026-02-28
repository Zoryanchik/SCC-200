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
                 osrm_base="http://localhost:5012",
                 max_walk_seconds=600):
        self._inter = inter_walk_table          # {stop_int: {stop_int: secs}}
        self._coords = stop_coords              # {stop_int: (lat, lon)}
        self._osrm = osrm_base
        self._max = max_walk_seconds
        
    def get_loc_coords(self, stop_int):
        """Return (lat, lon) for a given stop_int."""
        return self._coords[stop_int]

    def _haversine_m(self, lat1, lon1, lat2, lon2):
        """Return distance in meters between two lat/lon points using haversine."""
        R = 6371000.0
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lon2 - lon1)
        a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
        return 2 * R * math.asin(math.sqrt(a))

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
        margin = 0.02
        candidates = []
        exact_matches = {}  # {stop_int: walk_seconds} for exact coordinate matches
        for stop_int, (slat, slon) in self._coords.items():
            if abs(slat - lat) <= margin and abs(slon - lon) <= margin:
                candidates.append(stop_int)
                # Check for exact coordinate match (within ~1 meter precision)
                if abs(slat - lat) < 1e-5 and abs(slon - lon) < 1e-5:
                    exact_matches[stop_int] = 0

        if not candidates:
            # No candidates inside the bbox — fall back to searching all coords
            result = {}
            for stop_int, (slat, slon) in self._coords.items():
                dist_m = self._haversine_m(lat, lon, slat, slon)
                walk_time = int(dist_m / 1.0)
                if walk_time <= self._max:
                    result[stop_int] = walk_time
            # Include exact matches (stops at the user's exact location)
            for s, t in exact_matches.items():
                result[s] = 0
            sorted_result = {k: v for k, v in sorted(result.items(), key=lambda item: item[1])}
            return sorted_result

        # 2. Build OSRM /table request: source = user location,
        #    destinations = candidate stops

        coords_parts = [f"{lon},{lat}"]
        for s in candidates:
            slat, slon = self._coords[s]
            coords_parts.append(f"{slon},{slat}")
        coord_str = ";".join(coords_parts)

        url = (
            f"{self._osrm}/table/v1/foot/{coord_str}?sources=0&annotations=duration"
        )
        try:
            resp = urllib.request.urlopen(url, timeout=10)
            data = json.loads(resp.read())
            resp.close()
        except Exception:
            # OSRM not available — fall back to haversine distance estimate
            result = {}
            for stop_int in candidates:
                slat, slon = self._coords[stop_int]
                dist_m = self._haversine_m(lat, lon, slat, slon)
                walk_time = int(dist_m / 1.0)  # 1 m/s walking speed
                if walk_time <= self._max:
                    result[stop_int] = walk_time
            # Include exact matches (stops at the user's exact location)
            for s, t in exact_matches.items():
                result[s] = 0
            sorted_result = {k: v for k, v in sorted(result.items(), key=lambda item: item[1])}
            return sorted_result
        # Treat any non-Ok (case-insensitive) OSRM `code` as a failure and fall
        # back to haversine estimates. This guards against server errors that
        # still return JSON with a non-Ok status field.
        if str(data.get("code", "")).lower() != "ok":
            result = {}
            for stop_int in candidates:
                slat, slon = self._coords[stop_int]
                dist_m = self._haversine_m(lat, lon, slat, slon)
                walk_time = int(dist_m / 1.0)
                if walk_time <= self._max:
                    result[stop_int] = walk_time
            for s, t in exact_matches.items():
                result[s] = 0
            return {k: v for k, v in sorted(result.items(), key=lambda item: item[1])}

        durations = data["durations"][0]        # single-source row
        result = {}
        for i, dur in enumerate(durations):
            if i == 0:
                continue                        # skip self (user location)
            if dur is not None and dur <= self._max:
                result[candidates[i - 1]] = int(dur)

        # Include exact matches (stops at the user's exact location)
        for s, t in exact_matches.items():
            result[s] = 0

        # Sort by walk_seconds ascending
        sorted_result = {k: v for k, v in sorted(result.items(), key=lambda item: item[1])}
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
            # Treat non-Ok codes as failures and fall back below
            if str(data.get("code", "")).lower() == "ok" and data.get("routes"):
                return int(data["routes"][0]["duration"])
        except Exception:
            pass
        # Fallback: use accurate haversine distance, 1 m/s walking speed
        dist_m = self._haversine_m(lat1, lon1, lat2, lon2)
        return int(dist_m / 1.0)
