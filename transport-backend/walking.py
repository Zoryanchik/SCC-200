import json
import math
import urllib.request
import os
import time
import shutil
import subprocess
import ssl
import platform
from urllib.parse import urlparse


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


def ensure_osrm_dataset(cache_dir, source_url=None, timeout=30):
    """Ensure OSRM dataset exists in cache_dir/osrm.

    - Downloads the OSM PBF from source_url (defaults to GB extract) into
      cache_dir/osrm/ and builds .osrm files using the official
      osrm/osrm-backend image (requires docker or podman available).
    - Uses a small meta file to avoid rebuilding unless the remote source
      reports a changed Last-Modified header or the built files are missing.

    Returns the path to the built .osrm prefix (e.g. /.../osrm/great-britain-latest)
    or None if build was skipped/failed.
    """
    # Default to Geofabrik Great Britain PBF (changeable by caller)
    if source_url is None:
        source_url = "https://download.geofabrik.de/europe/great-britain-latest.osm.pbf"

    osrm_dir = os.path.join(cache_dir, "osrm")
    os.makedirs(osrm_dir, exist_ok=True)

    parsed = urlparse(source_url)
    pbf_name = os.path.basename(parsed.path)
    if not pbf_name:
        raise ValueError("Invalid source_url for OSRM dataset")
    pbf_path = os.path.join(osrm_dir, pbf_name)

    base_name = os.path.splitext(pbf_name)[0]
    osrm_prefix = os.path.join(osrm_dir, base_name)

    meta_path = os.path.join(osrm_dir, "meta.json")
    stored_mod = None
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf8") as fh:
                m = json.load(fh)
                stored_mod = m.get("modified")
        except Exception:
            stored_mod = None

    # Probe remote Last-Modified header (HEAD preferred). Some hosts may
    # present certificates that our environment can't verify; mirror bus_loader
    # which disables verification for these toolkit downloads.
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        req = urllib.request.Request(source_url, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            remote_mod = resp.headers.get("Last-Modified") or resp.headers.get("ETag")
    except Exception:
        # If HEAD fails, try GET headers only
        try:
            with urllib.request.urlopen(source_url, timeout=timeout, context=ctx) as resp:
                remote_mod = resp.headers.get("Last-Modified") or resp.headers.get("ETag")
        except Exception:
            remote_mod = None

    need_download = False
    if not os.path.exists(osrm_prefix + ".osrm"):
        need_download = True
    elif remote_mod and stored_mod != remote_mod:
        need_download = True

    if not need_download:
        return osrm_prefix

    print("  OSRM dataset missing or updated — (re)building OSRM data...")

    # Download PBF
    try:
        print(f"    Downloading {source_url} -> {pbf_path}")
        # Stream download with the same insecure SSL context as above
        req = urllib.request.Request(source_url)
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp, open(pbf_path, "wb") as out:
            shutil.copyfileobj(resp, out)
    except Exception as e:
        print(f"    ✗ Failed to download OSRM PBF: {e}")
        return None

    # Find container runtime
    runtime = None
    for r in ("podman", "docker"):
        if shutil.which(r):
            runtime = r
            break
    if runtime is None:
        print("    ✗ Neither podman nor docker is available to build OSRM files.")
        return None

    # Ensure we have a foot profile available in the cache and mount it
    # into the container at /data/foot.lua. This avoids relying on the
    # image providing a profile at /opt/profiles/foot.lua which is not
    # consistent across image builds/architectures.
    profile_local = os.path.join(osrm_dir, "foot.lua")
    if not os.path.exists(profile_local):
        print("    Downloading OSRM foot profile into cache...")
        try:
            profile_url = os.environ.get(
                "OSRM_PROFILE_URL",
                "https://raw.githubusercontent.com/Project-OSRM/osrm-backend/master/profiles/foot.lua",
            )
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            req = urllib.request.Request(profile_url)
            with urllib.request.urlopen(req, timeout=30, context=ctx) as resp, open(profile_local, "wb") as out:
                shutil.copyfileobj(resp, out)
        except Exception as e:
            print(f"    ✗ Failed to download foot profile: {e}")
            # Continue — the build will likely fail, but we don't want to
            # raise here and stop the whole startup sequence.

    # Run osrm-extract, osrm-partition, osrm-customize in container
    # Mount cache_dir as /data so we can reference the PBF and profile
    vol = f"{osrm_dir}:/data"
    profile_path = "/data/foot.lua"

    # Detect host architecture; on arm64 hosts pull/run the linux/amd64 image
    # where possible (Docker/Podman support image emulation). This avoids
    # "image platform does not match the expected platform" errors on Apple
    # Silicon or other arm64 hosts by explicitly requesting the amd64 image.
    arch = platform.machine().lower()
    PLATFORM_ARGS = []
    if arch in ("arm64", "aarch64"):
        # Use the --platform flag supported by Docker and recent Podman
        PLATFORM_ARGS = ["--platform", "linux/amd64"]
    commands = [
        [runtime, "run", *PLATFORM_ARGS, "--rm", "-v", vol, "osrm/osrm-backend", "osrm-extract", "-p", profile_path, f"/data/{pbf_name}"],
        [runtime, "run", *PLATFORM_ARGS, "--rm", "-v", vol, "osrm/osrm-backend", "osrm-partition", f"/data/{base_name}.osrm"],
        [runtime, "run", *PLATFORM_ARGS, "--rm", "-v", vol, "osrm/osrm-backend", "osrm-customize", f"/data/{base_name}.osrm"],
    ]

    for cmd in commands:
        print("    "+" ".join(cmd))
        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError as e:
            print(f"    ✗ OSRM build step failed: {e}")
            return None

    # Update meta
    try:
        meta = {"source_url": source_url, "modified": remote_mod, "built": int(time.time())}
        with open(meta_path, "w", encoding="utf8") as fh:
            json.dump(meta, fh)
    except Exception:
        pass

    print("    ✓ OSRM build complete")
    return osrm_prefix

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
        #    destinations = candidate stops

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
