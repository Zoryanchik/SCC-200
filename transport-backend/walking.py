import json
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
        for stop_int, (slat, slon) in self._coords.items():
            if abs(slat - lat) <= margin and abs(slon - lon) <= margin:
                candidates.append(stop_int)

        if not candidates:
            return {}

        # 2. Build OSRM /table request: source = user location,
        #    destinations = candidate stops
        coords_parts = [f"{lon},{lat}"]
        for s in candidates:
            slat, slon = self._coords[s]
            coords_parts.append(f"{slon},{slat}")
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
            return {}

        if data.get("code") != "Ok":
            return {}

        durations = data["durations"][0]        # single-source row
        result = {}
        for i, dur in enumerate(durations):
            if i == 0:
                continue                        # skip self (user location)
            if dur is not None and dur <= self._max:
                result[candidates[i - 1]] = int(dur)

        return result
    