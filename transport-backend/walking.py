import json
import math
import time
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
                 max_walk_seconds=800):
        self._inter = inter_walk_table          # {stop_int: {stop_int: secs}}
        self._coords = stop_coords              # {stop_int: (lat, lon)}
        self._osrm = osrm_base
        self._max = max_walk_seconds
        # Precompute a flat list of coordinates for faster iteration in
        # `reachable_stops` (avoids repeated dict lookups and tuple
        # allocations on every call). Also store the bbox margin here so
        # it doesn't need to be recomputed each call.
        self._bbox_margin = 0.02
        # list of (stop_int, slat, slon)
        self._coords_list = [(s, v[0], v[1]) for s, v in self._coords.items()]
        # Probe OSRM availability once at init and cache result to avoid
        # repeated timeouts during routing. We will re-check periodically.
        self._osrm_available = False
        self._last_osrm_check = 0.0
        try:
            # quick probe: attempt a short request to the base URL
            resp = urllib.request.urlopen(self._osrm, timeout=1)
            resp.close()
            self._osrm_available = True
        except Exception:
            self._osrm_available = False
        self._last_osrm_check = time.time()
        
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
        """Return a sorted list of (stop_int, walk_seconds) for stops walkable
        from *location*.

        *location* is a (lat, lon) tuple representing an arbitrary point
        (the user's start or end position).

        The returned list is sorted by walk_seconds ascending.
        """
        lat, lon = location

        # 1. Candidate stops within bounding box (~1.3 km)
        margin = self._bbox_margin
        candidates = []
        exact_matches = {}  # {stop_int: walk_seconds} for exact coordinate matches
        # iterate precomputed flat list for speed
        for stop_int, slat, slon in self._coords_list:
            if abs(slat - lat) <= margin and abs(slon - lon) <= margin:
                candidates.append(stop_int)
                # Check for exact coordinate match (within ~1 meter precision)
                if abs(slat - lat) < 1e-5 and abs(slon - lon) < 1e-5:
                    exact_matches[stop_int] = 0

        if not candidates:
            # No candidates inside the bbox — fall back to searching all coords
            result = {}
            for stop_int, slat, slon in self._coords_list:
                dist_m = self._haversine_m(lat, lon, slat, slon)
                walk_time = int(dist_m / 1.0)
                if walk_time <= self._max:
                    result[stop_int] = walk_time
            # Include exact matches (stops at the user's exact location)
            for s, t in exact_matches.items():
                result[s] = 0
            # Return flattened list sorted by seconds
            return sorted(result.items(), key=lambda item: item[1])

        # 2. Build OSRM /table request: source = user location,
        #    destinations = candidate stops

        coords_parts = [f"{lon},{lat}"]
        for s in candidates:
            slat, slon = self._coords[s]
            coords_parts.append(f"{slon},{slat}")
        coord_str = ";".join(coords_parts)

        url = f"{self._osrm}/table/v1/foot/{coord_str}?sources=0&annotations=duration"
        # Only attempt an OSRM call if we believe the server is available.
        # Re-check availability every 60s.
        now = time.time()
        if not self._osrm_available and (now - self._last_osrm_check) > 60:
            try:
                resp = urllib.request.urlopen(self._osrm, timeout=1)
                resp.close()
                self._osrm_available = True
            except Exception:
                self._osrm_available = False
            self._last_osrm_check = now

        if self._osrm_available:
            try:
                resp = urllib.request.urlopen(url, timeout=10)
                data = json.loads(resp.read())
                resp.close()
            except Exception:
                # OSRM failed during call — mark unavailable and fall back
                self._osrm_available = False
                self._last_osrm_check = now
                data = None
        else:
            data = None

        if data is None:
            # OSRM not available — fall back to haversine distance estimate
            result = {}
            for stop_int in candidates:
                # use precomputed coords mapping for direct lookup
                slat, slon = self._coords[stop_int]
                dist_m = self._haversine_m(lat, lon, slat, slon)
                walk_time = int(dist_m / 1.0)  # 1 m/s walking speed
                if walk_time <= self._max:
                    result[stop_int] = walk_time
            # Include exact matches (stops at the user's exact location)
            for s, t in exact_matches.items():
                result[s] = 0
            # Return flattened list sorted by seconds
            return sorted(result.items(), key=lambda item: item[1])
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
            return sorted(result.items(), key=lambda item: item[1])

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

        # Sort by walk_seconds ascending and return flattened list
        return sorted(result.items(), key=lambda item: item[1])

        

    def walking_time_between(self, point_a, point_b):
        """
        Compute walking time (seconds) between two arbitrary (lat, lon) points.
        Uses OSRM if available, otherwise falls back to haversine estimate.
        """
        lat1, lon1 = point_a
        lat2, lon2 = point_b
        url = f"{self._osrm}/route/v1/foot/{lon1},{lat1};{lon2},{lat2}?overview=false&steps=false&annotations=duration"
        # Only attempt OSRM if we think it's available; re-check periodically
        now = time.time()
        if not self._osrm_available and (now - self._last_osrm_check) > 60:
            try:
                resp = urllib.request.urlopen(self._osrm, timeout=1)
                resp.close()
                self._osrm_available = True
            except Exception:
                self._osrm_available = False
            self._last_osrm_check = now

        if self._osrm_available:
            try:
                resp = urllib.request.urlopen(url, timeout=10)
                data = json.loads(resp.read())
                resp.close()
                # Treat non-Ok codes as failures and fall back below
                if str(data.get("code", "")).lower() == "ok" and data.get("routes"):
                    return int(data["routes"][0]["duration"])
            except Exception:
                self._osrm_available = False
                self._last_osrm_check = now
        # Fallback: use accurate haversine distance, 1 m/s walking speed
        dist_m = self._haversine_m(lat1, lon1, lat2, lon2)
        return int(dist_m / 1.0)
