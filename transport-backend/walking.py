import json
import math
import os
import urllib.request

# Default OSRM endpoint, configurable via the ``OSRM_URL`` environment
# variable.  Containers can set this to e.g. ``http://osrm:5000``; host
# development defaults to ``http://localhost:5012``.
_DEFAULT_OSRM_URL = os.environ.get("OSRM_URL", "http://localhost:5012")


class Walking:
    """Walking transfers powered by OSRM and a precomputed transfer table.

    When OSRM is unreachable the class falls back to:

    1. **Precomputed inter-walk table** — uses the nearest known stop
       as a proxy and adds the precomputed transfers from that stop
       (these were originally computed via OSRM during data loading).
    2. **Haversine distance estimate** — straight-line distance at
       ~1 m/s walking speed.

    Parameters
    ----------
    inter_walk_table : dict
        {stop_int: {stop_int: walk_seconds}} — precomputed from
        BusLoader.get_walking_transfers(), remapped to merged-data
        stop integers.
    stop_coords : dict
        {stop_int: (lat, lon)} — coordinates for every stop in
        merged-data space.
    osrm_base : str or None
        Base URL of a running OSRM foot-profile server.  Defaults to
    the ``OSRM_URL`` environment variable, falling back to
    ``http://localhost:5012``.
    max_walk_seconds : int
        Maximum walk duration to consider (default 600 = 10 min).
    """

    def __init__(self, inter_walk_table, stop_coords,
                 osrm_base=None,
                 max_walk_seconds=600):
        self._inter = inter_walk_table          # {stop_int: {stop_int: secs}}
        self._coords = stop_coords              # {stop_int: (lat, lon)}
        self._osrm = osrm_base if osrm_base is not None else _DEFAULT_OSRM_URL
        self._max = max_walk_seconds
        # Allow the environment to pre-declare OSRM reachability so we
        # don't rely on in-container network probes which can be flaky
        # in some host/container setups. OSRM_AVAILABLE=1 will set the
        # probe result to True immediately.
        env_flag = os.environ.get("OSRM_AVAILABLE")
        if env_flag is not None and env_flag.lower() in ("1", "true", "yes"):  # type: ignore[attr-defined]
            self._osrm_ok: bool | None = True
        else:
            self._osrm_ok: bool | None = None       # None = not probed yet

    # ── OSRM availability ────────────────────────────────────────

    @property
    def osrm_url(self) -> str:
        """Return the configured OSRM base URL."""
        return self._osrm

    @property
    def osrm_available(self) -> bool:
        """Probe OSRM once and cache the result."""
        if self._osrm_ok is None:
            self._osrm_ok = self._probe_osrm()
        return self._osrm_ok

    def _probe_osrm(self) -> bool:
        """Return True if the OSRM server is reachable.

        Previously this probed the /nearest/v1/foot endpoint with the
        coordinate 0,0 which can time out or behave inconsistently for
        servers that don't have global coverage. Probe the server root
        instead and treat any HTTP response (including HTTPError) as a
        positive reachability signal.
        """
        import urllib.error as _ue
        try:
            url = self._osrm.rstrip("/") + "/"
            resp = urllib.request.urlopen(url, timeout=3)
            resp.close()
            return True
        except _ue.HTTPError:
            # Server responded with an error status (400/404/etc.) — still
            # indicates the OSRM process is reachable, so treat as OK.
            return True
        except Exception:
            return False

    def reset_osrm_probe(self):
        """Clear the cached probe result so the next access re-checks."""
        self._osrm_ok = None

    def status(self) -> dict:
        """Return a summary of walking-engine state."""
        return {
            "osrm_url": self._osrm,
            "osrm_available": self.osrm_available,
            "max_walk_seconds": self._max,
            "precomputed_stops": len(self._inter),
            "stops_with_coords": len(self._coords),
        }

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
            # OSRM not available — deterministic fallback.
            #
            # Strategy:
            #   1. Haversine estimate for every candidate stop.
            #   2. Find the nearest candidate that is a KEY in the
            #      precomputed inter_walk table, then add its
            #      precomputed neighbours (these came from a prior OSRM
            #      run and are therefore more accurate than haversine).
            #   3. Merge, keeping the shorter time for any stop that
            #      appears in both sets.
            result = {}
            for stop_int in candidates:
                slat, slon = self._coords[stop_int]
                dist_m = _haversine_m(lat, lon, slat, slon)
                walk_time = int(dist_m / 1.0)  # 1 m/s walking speed
                if walk_time <= self._max:
                    result[stop_int] = walk_time

            # Augment with precomputed inter-walk table via nearest proxy
            nearest_stop, nearest_secs = self._nearest_precomputed(lat, lon)
            if nearest_stop is not None and nearest_secs <= self._max:
                # The nearest precomputed stop is walkable — include it
                result.setdefault(nearest_stop, nearest_secs)
                result[nearest_stop] = min(result[nearest_stop], nearest_secs)

                for nb_stop, nb_secs in self._inter.get(nearest_stop, {}).items():
                    total = nearest_secs + nb_secs
                    if total <= self._max:
                        if nb_stop not in result or total < result[nb_stop]:
                            result[nb_stop] = total

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
        dist_m = _haversine_m(lat1, lon1, lat2, lon2)
        return int(dist_m / 1.0)

    # ── private helpers ──────────────────────────────────────────

    def _nearest_precomputed(self, lat, lon):
        """Return (stop_int, walk_seconds) for the nearest precomputed stop.

        Only considers stops that are **keys** in the inter-walk table
        (i.e. stops that have outgoing precomputed transfers).
        Returns ``(None, None)`` if the table is empty or no stop is
        within ``max_walk_seconds``.
        """
        best_stop = None
        best_secs = None
        for stop_int in self._inter:
            c = self._coords.get(stop_int)
            if c is None:
                continue
            dist_m = _haversine_m(lat, lon, c[0], c[1])
            secs = int(dist_m / 1.0)  # 1 m/s
            if secs <= self._max and (best_secs is None or secs < best_secs):
                best_stop = stop_int
                best_secs = secs
        return best_stop, best_secs


# ── module-level utilities ───────────────────────────────────────────


def _haversine_m(lat1, lon1, lat2, lon2):
    """Return the great-circle distance in metres between two points."""
    R = 6_371_000  # Earth radius in metres
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
