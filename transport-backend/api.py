"""Transport Backend — FastAPI server.

Exposes transport functionality (health check, live buses, journey
planning, stop search, station classification) as a JSON API consumed
by the frontend.
"""

from contextlib import asynccontextmanager
import json
import logging
import math
import os
import time
import sys
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode
from urllib.request import Request as UrllibRequest, urlopen
from urllib.error import HTTPError, URLError
from xml.etree import ElementTree

# --- Live bus track precompute cache (Option A: embed coords in /bus/live) ---
#
# Goal: make click-to-show-track instant by doing geometry lookup during the
# periodic /bus/live refresh. The frontend can then render the polyline
# immediately with no extra /route/leg-geometry calls.
#
# Cache is bounded + TTL to avoid unbounded growth.
_BUS_TRACK_CACHE_LOCK = threading.Lock()
_BUS_TRACK_CACHE: dict[str, tuple[float, list[list[float]]]] = {}


def _bus_track_cache_limits() -> tuple[int, int]:
    """Return (max_entries, ttl_seconds) for the in-memory track cache."""
    try:
        max_entries = int(os.environ.get('BUS_TRACK_CACHE_MAX', '300'))
    except Exception:
        max_entries = 300
    try:
        ttl_s = int(os.environ.get('BUS_TRACK_CACHE_TTL_S', '90'))
    except Exception:
        ttl_s = 90
    # Safety clamps
    max_entries = max(0, min(max_entries, 5000))
    ttl_s = max(1, min(ttl_s, 3600))
    return max_entries, ttl_s


def _bus_track_cache_prune(now: float | None = None) -> None:
    now = time.time() if now is None else float(now)
    max_entries, ttl_s = _bus_track_cache_limits()
    if max_entries <= 0:
        # Disabled
        _BUS_TRACK_CACHE.clear()
        return
    # TTL prune
    cutoff = now - float(ttl_s)
    try:
        expired = [k for k, (ts, _coords) in _BUS_TRACK_CACHE.items() if ts < cutoff]
        for k in expired:
            _BUS_TRACK_CACHE.pop(k, None)
    except Exception:
        # Best-effort; never break API due to cache bookkeeping
        pass
    # Size prune (drop oldest)
    try:
        if len(_BUS_TRACK_CACHE) > max_entries:
            items = sorted(_BUS_TRACK_CACHE.items(), key=lambda kv: kv[1][0])
            for k, _v in items[: max(0, len(_BUS_TRACK_CACHE) - max_entries)]:
                _BUS_TRACK_CACHE.pop(k, None)
    except Exception:
                        pass


def _bus_track_cache_key(entry: dict) -> str | None:
    """Build a stable-ish cache key for a single vehicle from /bus/live entry."""
    try:
        op = (entry.get('operator_ref') or entry.get('operator') or '')
        line = (entry.get('line') or '')
        vref = entry.get('vehicle_ref') or entry.get('vehicle_journey_code') or entry.get('framed_journey_ref') or entry.get('dated_journey_ref')
        jid = entry.get('logged_journey_id')
        # Prefer vehicle_ref+logged_journey_id when present.
        parts = [str(op).strip(), str(line).strip()]
        if vref:
            parts.append(str(vref).strip())
        if jid:
            parts.append(str(jid).strip())
        # If we don't have *any* stable identifiers, don't cache.
        if len(parts) <= 2:
            return None
        return '|'.join(parts)
    except Exception:
        return None


def _normalize_latlon_coords(coords) -> list[list[float]]:
    """Normalize a polyline to [[lat, lon], ...] floats."""
    out: list[list[float]] = []
    if not isinstance(coords, list):
        return out
    for pt in coords:
        if not isinstance(pt, (list, tuple)) or len(pt) < 2:
            continue
        try:
            a = float(pt[0])
            b = float(pt[1])
        except Exception:
            continue
        if not (a == a and b == b):
            continue
        out.append([a, b])
    return out


def _get_cached_bus_track(entry: dict) -> list[list[float]] | None:
    key = _bus_track_cache_key(entry)
    if not key:
        return None
    now = time.time()
    max_entries, ttl_s = _bus_track_cache_limits()
    if max_entries <= 0:
        return None
    with _BUS_TRACK_CACHE_LOCK:
        _bus_track_cache_prune(now)
        hit = _BUS_TRACK_CACHE.get(key)
        if not hit:
            return None
        ts, coords = hit
        if (now - ts) > float(ttl_s):
            _BUS_TRACK_CACHE.pop(key, None)
            return None
        return coords



def _put_cached_bus_track(entry: dict, coords: list[list[float]]) -> None:
    key = _bus_track_cache_key(entry)
    if not key:
        return
    now = time.time()
    with _BUS_TRACK_CACHE_LOCK:
        max_entries, _ttl_s = _bus_track_cache_limits()
        if max_entries <= 0:
            return
        _BUS_TRACK_CACHE[key] = (now, coords)
        _bus_track_cache_prune(now)


def _compute_vehicle_track_coords_for_live(entry: dict) -> list[list[float]] | None:
    """Best-effort compute non-linear geometry for a live vehicle.

    This is intentionally conservative: if we can't confidently return a
    fragment-stitched polyline (from `route_link_tracks`), return None.
    """
    try:
        # For live vehicles we intentionally avoid returning full-route
        # polylines (these can be jumbled/branched and create "teleport" lines).
        # Instead we prefer stitching stop-to-stop fragment tracks from
        # route_link_tracks using origin/destination stop context when present.
        # If we can't confidently build a fragment-based geometry, return None.

        # Fast path: if the live matcher provided a merged route index,
        # attempt to stitch fragment geometry from merged.route_link_tracks.
        try:
            ri = entry.get('route_int')
            if ri is not None:
                ri = int(ri)
                merged = None
                try:
                    # Same merged discovery strategy as route_leg_geometry
                    if globals().get('_base_cache'):
                        prebuilt = _base_cache.get('prebuilt_cache')
                        if prebuilt:
                            for _k, v in prebuilt.items():
                                try:
                                    merged = v[0]
                                except Exception:
                                    merged = None
                                if merged:
                                    break
                    if merged is None:
                        rcache = globals().get('_router_cache')
                        rlock = globals().get('_router_cache_lock')
                        if rcache is not None:
                            if rlock:
                                with rlock:
                                    items = list(rcache.values())
                            else:
                                items = list(rcache.values())
                            for val in items:
                                try:
                                    merged = val[0]
                                except Exception:
                                    merged = None
                                if merged:
                                    break
                except Exception:
                    merged = None

                if merged is not None:
                    # Prefer route_link_tracks stitching when we have stop context.
                    from_atco = entry.get('origin_atco') or entry.get('from_stop_id') or entry.get('from_atco')
                    to_atco = entry.get('destination_atco') or entry.get('to_stop_id') or entry.get('to_atco')

                    # Access fragment map
                    link_map = None
                    try:
                        if hasattr(merged, 'get_route_link_tracks'):
                            link_map = merged.get_route_link_tracks(ri)
                        else:
                            links = getattr(merged, 'route_link_tracks', None)
                            link_map = links[ri] if (links and 0 <= ri < len(links)) else None
                    except Exception:
                        link_map = None

                    if link_map and from_atco and to_atco:
                        try:
                            route_stops = merged.route_stops[ri] if ri < len(getattr(merged, 'route_stops', []) or []) else []
                        except Exception:
                            route_stops = []

                        resolved = _resolve_route_stop_occurrences(
                            merged,
                            route_stops,
                            str(from_atco).strip() if from_atco else None,
                            str(to_atco).strip() if to_atco else None,
                        )
                        fs = resolved.get('from_stop_int')
                        ts = resolved.get('to_stop_int')
                        i = resolved.get('from_pos')
                        j = resolved.get('to_pos')

                        if fs is not None and ts is not None and fs != ts:
                            # Stitch consecutive pairs between fs and ts along route order.
                            if i is not None and j is not None:
                                step = 1 if j > i else -1
                                stitched_pts = []
                                ok = True
                                k = i
                                while k != j:
                                    a = route_stops[k]
                                    b = route_stops[k + step]
                                    seg2 = link_map.get((a, b))
                                    if not seg2:
                                        rev2 = link_map.get((b, a))
                                        if rev2:
                                            seg2 = list(reversed(rev2))
                                    if not seg2:
                                        ok = False
                                        break
                                    if stitched_pts and seg2 and stitched_pts[-1] == seg2[0]:
                                        stitched_pts.extend(seg2[1:])
                                    else:
                                        stitched_pts.extend(seg2)
                                    k += step

                                if ok:
                                    coords = _normalize_latlon_coords(stitched_pts)
                                    if len(coords) >= 2:
                                        _put_cached_bus_track(entry, coords)
                                        return coords
        except Exception:
            pass
        # If we already computed/cached it recently, reuse.
        cached = _get_cached_bus_track(entry)
        if cached and len(cached) >= 2:
            return cached


        # No fragment geometry available (missing stop context or fragments).
        # For live tracks we do NOT fall back to full-route polylines.
        return None

        lat_v = entry.get('lat')
        lon_v = entry.get('lon')
        if lat_v is None or lon_v is None:
            return None

        eps = 0.0001
        # (legacy path removed)
        if not isinstance(data, dict):
            return None
        # (legacy path removed)
    except Exception:
        return None


def _compute_vehicle_track_coords_with_source_for_live(entry: dict) -> tuple[list[list[float]] | None, str | None, bool]:
    """Compute vehicle track coords plus provenance.

    Returns (coords, source, cached) where:
            - source in {"cache", "leg_geometry", "none"}
      - cached indicates we served coords from the in-memory cache.
    """
    cached = _get_cached_bus_track(entry)
    if cached and len(cached) >= 2:
        return cached, 'cache', True

    coords = _compute_vehicle_track_coords_for_live(entry)
    if not coords or len(coords) < 2:
        return None, 'none', False

    return coords, 'leg_geometry', False

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from bus_live import BusLive, get_bus_live
from main import build_for_date
import asyncio
from contextlib import contextmanager
from time_utils import seconds_since_midnight, seconds_to_time
from ws_server import broker as ws_broker, websocket_endpoint as ws_live_endpoint
from station_classifier import classify_all, classify_to_lookup
from modes import name_to_int, all_transit_modes, BUS, TRAIN
import copy
import threading
import time
import psycopg
from urllib.error import URLError, HTTPError
import atexit
from fastapi.responses import JSONResponse
from urllib.parse import urlencode

logger = logging.getLogger(__name__)


def _live_endpoints_disabled() -> bool:
    """Return True when live endpoints should be hard-disabled.

    This is intended for routing/performance benchmarks where *no* live
    network fetches (bus live feeds, rail departures, delay recompute)
    should run and live endpoints should return empty results.

    Enable with: BUS_DISABLE_LIVE_ENDPOINTS=1
    """
    return False
    #str(os.environ.get('BUS_DISABLE_LIVE_ENDPOINTS') or '') == '1'

# Add the current directory to the path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
# Add minimal globals used by startup logic
_base_cache = None
_base_init_attempted = False

# Nominatim rate-limiting: ensure we do at most 1 request per second
NOMINATIM_LOCK = threading.Lock()
_NOMINATIM_LAST_CALL = 0.0
_NOMINATIM_MIN_INTERVAL = 1.0


# Ensure a clean logging/stdout shutdown to avoid interpreter-finalization
# races where background threads attempt to write to stdout while the
# interpreter is tearing down (causes "could not acquire lock for <_io.BufferedWriter>"
# fatal errors). This registers an atexit handler that flushes stdio and
# shuts down the logging subsystem.
def _graceful_shutdown():
    try:
        try:
            sys.stdout.flush()
        except Exception:
            pass
        try:
            sys.stderr.flush()
        except Exception:
            pass
        try:
            logging.shutdown()
        except Exception:
            pass
    except Exception:
        pass

atexit.register(_graceful_shutdown)


# Routing request/response models
class RouteRequest(BaseModel):
    start_lat: float
    start_lon: float
    end_lat: float
    end_lon: float
    date: str
    time: str  # HH:MM:SS
    max_transfers: int = 3
    mode: str = "both"  # "bus", "train", or "both"

class RouteResponse(BaseModel):
    success: bool
    route: Optional[dict] = None
    error: Optional[str] = None


class AddressRouteRequest(BaseModel):
    start: str
    end: str
    date: str
    time: str  # HH:MM:SS
    max_transfers: int = 3
    mode: str = "both"


# Journey plan request/response models
class StopLocation(BaseModel):
    lat: float
    lon: float

class JourneyPlanRequest(BaseModel):
    fromStop: StopLocation
    toStop: StopLocation
    departureTime: str     # HH:MM:SS
    date: str              # YYYY-MM-DD
    maxTransfers: int = 5
    mode: str = "both"     # bus | train | both
    includeGeometry: bool = False

# — Lifespan (startup / shutdown) ——————————————————————————


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise base transport data on server start.

    Errors are caught so the server can still serve /health even
    when the heavy backend is unavailable (e.g. during testing).
    """
    global _base_cache
    try:
        # Optionally auto-create the Postgres database and schema in dev
        # environments when BUS_AUTO_CREATE_DB=1. This keeps creation as an
        # explicit startup step instead of happening inside low-level
        # connection helpers.
        if os.environ.get('BUS_AUTO_CREATE_DB') == '1':
            try:
                from bus_loader import ensure_db_and_schema
                from main import BUS_DB_PATH as _bus_dsn
                ensure_db_and_schema(_bus_dsn)
            except Exception as _e:  # pragma: no cover
                logger.warning('BUS_AUTO_CREATE_DB requested but ensure_db_and_schema failed: %s', _e)

        from main import initialize_base
        # Allow skipping heavy initialization via env var for low-memory
        # or rapid dev workflows. When BUS_SKIP_INITIALIZE=1 the server
        # starts immediately and initialization is not performed.
        if os.environ.get('BUS_SKIP_INITIALIZE') == '1':
            logger.info('BUS_SKIP_INITIALIZE=1 set — skipping initialize_base()')
            globals()['_base_cache'] = None
            globals()['_base_init_attempted'] = True
        else:
            # Attempt one-time base initialization synchronously. This will
            # block the ASGI startup until initialization completes so the
            # server only reports "application startup complete" once all
            # base data is ready (NaPTAN download, walking precompute,
            # dataset loading).
            _base_cache = initialize_base()
            # If initialize_base() prebuilt AM/PM routers, eagerly seed the
            # request-time router cache now (during startup) so the first UI
            # request doesn't accidentally miss and trigger a rebuild.
            try:
                pre = _base_cache.get('prebuilt_cache') if _base_cache else None
                if isinstance(pre, dict) and pre:
                    seeded = 0
                    for k, v in pre.items():
                        if isinstance(k, tuple) and len(k) == 2:
                            try:
                                _set_router_cache((str(k[0]), str(k[1])), v)
                                seeded += 1
                            except Exception:
                                continue
                    logger.info('[router] seeded %d prebuilt routers into _router_cache', seeded)
            except Exception:
                logger.debug('[router] failed to seed prebuilt routers into _router_cache', exc_info=True)
    except Exception as exc:  # pragma: no cover
        logger.warning(
            "Backend initialisation failed  — endpoints requiring "
            "transport data will be unavailable: %s", exc,
        )
    finally:
        # Mark that we've attempted initialization once (success or fail).
        # Other code paths consult this flag to avoid re-running init on
        # every request which can cause noisy repeated logging.
        globals()['_base_init_attempted'] = True
        # Record in a local flag whether initialization failed so we can
        # decide afterwards (outside the finally block) whether to start
        # background services. Returning from inside a finally block is
        # disallowed by Python, so set a local variable instead.
        init_failed = (_base_cache is None)
    # If initialization failed, skip starting background services to
    # avoid noisy background loops. Yield so the server can still
    # respond to lightweight endpoints like /health.
    if init_failed:
        logger.warning('Backend initialisation failed — skipping background services')
        yield
        return
    # Configure and start the WebSocket/STOMP live-updates broker
    try:
        if os.environ.get('BUS_DISABLE_LIVE_POLLING') == '1':
            logger.info('BUS_DISABLE_LIVE_POLLING=1 set — not starting live polling')
        else:
            # Poll for live updates every 20s and use a 20s HTTP timeout for feed fetches
            ws_broker.configure(bus_live_factory=lambda: BusLive(timeout=20), poll_interval=20.0)
            await ws_broker.start_polling()
    except Exception as exc:  # pragma: no cover
        logger.warning("WebSocket broker startup failed: %s", exc)
    # Start background delay updater thread (today-only updates)
    if os.environ.get('BUS_DISABLE_DELAY_UPDATER') == '1':
        logger.info('BUS_DISABLE_DELAY_UPDATER=1 set — not starting delay updater thread')
    else:
        stop_event = threading.Event()
        delay_thread = threading.Thread(target=_delay_updater_loop, args=(stop_event,), daemon=True)
        delay_thread.start()
        # Expose so shutdown can stop it
        globals()['_delay_updater_stop_event'] = stop_event
        globals()['_delay_updater_thread'] = delay_thread
    yield  # — server is running
    # Shutdown: stop the live-updates poll loop
    try:
        await ws_broker.stop_polling()
    except Exception:  # pragma: no cover
        pass
    # Stop the delay updater thread
    try:
        ev = globals().get('_delay_updater_stop_event')
        th = globals().get('_delay_updater_thread')
        if ev:
            ev.set()
        if th and isinstance(th, threading.Thread):
            th.join(timeout=2.0)
    except Exception:
        pass
    

app = FastAPI(title="Transport API", lifespan=lifespan)

# --- Routing activity gate ---------------------------------------------------
# You asked for the opposite of request throttling: when routing is running,
# pause other CPU-heavy background work (live bus polling/STOMP, delay updates).
#
# We implement this as a process-wide counter that can be incremented from both
# async and threaded code paths.
_routing_active_lock = threading.Lock()
_routing_active_count = 0

# --- Live bus last-known snapshot --------------------------------------------
# When routing is active, returning an empty list from /bus/live makes the UI
# appear broken even if routing results are already visible. Instead, we keep a
# short-lived in-memory snapshot of the most recent successful /bus/live result
# per operator+query params and serve it while routing computations run.
_bus_live_snapshot_lock = threading.Lock()
_bus_live_snapshot: dict[tuple, dict] = {}


def _bus_live_snapshot_get(key: tuple, max_age_s: float) -> Optional[list]:
    try:
        now = time.time()
        with _bus_live_snapshot_lock:
            entry = _bus_live_snapshot.get(key)
        if not entry:
            return None
        ts = entry.get('ts')
        data = entry.get('data')
        if ts is None or data is None:
            return None
        if (now - float(ts)) > float(max_age_s):
            return None
        return data
    except Exception:
        return None


def _bus_live_snapshot_set(key: tuple, data: list, max_entries: int = 2000) -> None:
    try:
        with _bus_live_snapshot_lock:
            _bus_live_snapshot[key] = {'ts': time.time(), 'data': data}
            # crude bound to avoid unbounded growth
            if len(_bus_live_snapshot) > max_entries:
                # drop ~10% oldest by timestamp
                items = sorted(_bus_live_snapshot.items(), key=lambda kv: kv[1].get('ts', 0.0))
                drop_n = max(1, int(max_entries * 0.1))
                for k, _v in items[:drop_n]:
                    _bus_live_snapshot.pop(k, None)
    except Exception:
        pass


def is_routing_active() -> bool:
    """Return True when a routing computation is currently running."""
    with _routing_active_lock:
        return _routing_active_count > 0


@contextmanager
def routing_activity():
    """Context manager marking routing as active for its duration."""
    global _routing_active_count
    with _routing_active_lock:
        _routing_active_count += 1
    try:
        yield
    finally:
        with _routing_active_lock:
            _routing_active_count = max(0, _routing_active_count - 1)


def _suggest_similar_route_ids(route_id: str, limit: int = 10) -> list[str]:
    """Return a short list of route_ids that look similar to the provided one.

    This is best-effort and only inspects in-memory data (prebuilt merged caches).
    It's designed purely for debugging why a route_id isn't resolving to tracks.
    """
    if not route_id:
        return []

    suggestions: list[str] = []
    try:
        if globals().get('_base_cache'):
            prebuilt = _base_cache.get('prebuilt_cache') if _base_cache else None
            if prebuilt:
                # Build a few match keys (prefix before '::', suffix after '::')
                prefix = route_id.split('::', 1)[0] if '::' in route_id else route_id
                suffix = route_id.split('::', 1)[1] if '::' in route_id else None

                for _k, v in prebuilt.items():
                    try:
                        merged = v[0]
                    except Exception:
                        merged = None
                    if not merged:
                        continue
                    metas = getattr(merged, 'route_metadata', None) or []
                    for meta in metas:
                        if not isinstance(meta, dict):
                            continue
                        rid = meta.get('route_id')
                        if not rid:
                            continue
                        srid = str(rid)
                        if srid == route_id:
                            continue
                        if prefix and srid.startswith(prefix):
                            suggestions.append(srid)
                        elif suffix and srid.endswith(suffix):
                            suggestions.append(srid)
                        if len(suggestions) >= limit:
                            return suggestions[:limit]
    except Exception:
        # Suggestions are best-effort only.
        return suggestions[:limit]
    return suggestions[:limit]


@app.get('/debug/route-tracks/{route_id:path}')
def debug_route_tracks(route_id: str, sample: int = 5, suggest: int = 10):
    """Deprecated debug endpoint.

    Full-route polylines were removed from the backend, so this endpoint no
    longer returns geometry. It remains to avoid breaking old scripts/links.
    """
    raise HTTPException(
        status_code=410,
        detail='route_tracks_removed: use /debug/route-link-tracks/{route_id} instead',
    )


@app.get('/debug/route-link-tracks/{route_id:path}')
def debug_route_link_tracks(route_id: str, sample: int = 5, suggest: int = 10):
    """Debug helper: inspect whether stop-to-stop fragment tracks exist for `route_id`.

    Response:
      {
        "route_id": str,
        "found": bool,
        "link_count": int,
        "sample_links": [[from_atco,to_atco],...],
        "sample_fragment": [[lat,lon],...],
        "suggestions": [route_id,...]
      }

    This endpoint is for local debugging only.
    """
    if not route_id:
        raise HTTPException(status_code=400, detail='route_id is required')

    merged = None
    try:
        if globals().get('_base_cache'):
            prebuilt = _base_cache.get('prebuilt_cache') if _base_cache else None
            if prebuilt:
                for _k, v in prebuilt.items():
                    try:
                        merged = v[0]
                    except Exception:
                        merged = None
                    if merged:
                        break
    except Exception:
        merged = None

    # This debug endpoint historically took a timetable route_id. The frontend
    # sometimes hands us a numeric route_int instead. Accept either:
    # - If `route_id` matches a route_metadata.route_id, use its route_int.
    # - Else if `route_id` looks like an int, treat it as route_int directly.
    meta = None
    try:
        metas = getattr(merged, 'route_metadata', None) or []
        for m in metas:
            if isinstance(m, dict) and str(m.get('route_id') or '') == str(route_id):
                meta = m
                break
    except Exception:
        meta = None

    route_int = None
    try:
        if isinstance(meta, dict) and meta.get('route_int') is not None:
            route_int = int(meta.get('route_int'))
        else:
            route_int = int(str(route_id).strip())
    except Exception:
        route_int = None

    # Extra diagnostics: why would fragments be missing?
    diag: dict[str, Any] = {
        'input': route_id,
        'route_int': route_int,
    }
    try:
        diag['merged_route_count'] = len(getattr(merged, 'route_metadata', None) or [])
    except Exception:
        diag['merged_route_count'] = None
    try:
        if route_int is not None and merged is not None:
            if route_int < 0 or route_int >= len(getattr(merged, 'route_metadata', None) or []):
                diag['route_int_in_range'] = False
            else:
                diag['route_int_in_range'] = True
                m = (getattr(merged, 'route_metadata', None) or [None])[route_int]
                diag['meta_is_dict'] = isinstance(m, dict)
                if isinstance(m, dict):
                    diag['meta_route_id'] = m.get('route_id')
                    diag['meta_line'] = m.get('line') or m.get('line_name')
    except Exception:
        pass

    link_map = None
    try:
        if merged is not None and route_int is not None and hasattr(merged, 'get_route_link_tracks'):
            link_map = merged.get_route_link_tracks(route_int)
    except Exception:
        link_map = None

    try:
        diag['link_map_is_dict'] = isinstance(link_map, dict)
        diag['link_map_len'] = len(link_map) if isinstance(link_map, dict) else None
    except Exception:
        pass

    link_count = len(link_map) if isinstance(link_map, dict) else 0
    sample_links: list[list[str]] = []
    sample_fragment: list[list[float]] = []

    try:
        n = int(sample)
        if n < 0:
            n = 0
    except Exception:
        n = 5

    try:
        if isinstance(link_map, dict) and link_map:
            keys = list(link_map.keys())
            for k in keys[:n]:
                try:
                    a, b = k
                    sample_links.append([str(a), str(b)])
                except Exception:
                    continue
            # best-effort fragment sample
            if keys:
                frags = link_map.get(keys[0]) or []
                if frags and isinstance(frags[0], (list, tuple)):
                    sample_fragment = _normalize_latlon_coords(frags[0])[: max(0, n * 2)]
    except Exception:
        sample_links = []
        sample_fragment = []

    resp = {
        'route_id': route_id,
        'found': bool(link_count > 0),
        'link_count': int(link_count),
        'sample_links': sample_links,
        'sample_fragment': sample_fragment,
        'suggestions': [],
        'route_int': route_int,
        'diag': diag,
    }

    if not resp['found'] and int(suggest or 0) > 0:
        resp['suggestions'] = _suggest_similar_route_ids(route_id, limit=int(suggest))

    return resp

# -- WebSocket/STOMP live updates endpoint --------------------------------
app.add_api_websocket_route("/ws/live", ws_live_endpoint)

# -- CORS ------------------------------------------------------------------
# Read allowed origins from the CORS_ORIGINS env var (comma-separated).
# Falls back to localhost dev ports so local development works out of the box.
_default_origins = (
    # Legacy dev servers
    "http://localhost:3000,http://localhost:5075,http://localhost:5076,"
    "http://127.0.0.1:3000,http://127.0.0.1:5075,http://127.0.0.1:5076,"
    # Vite dev server (default + common fallback when 5173 is taken)
    "http://localhost:5173,http://localhost:5174,"
    "http://127.0.0.1:5173,http://127.0.0.1:5174"
)
_cors_origins = [
    o.strip()
    for o in os.getenv("CORS_ORIGINS", _default_origins).split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# — Health check ———————————————————————————————————————————


@app.get("/health")
async def health():
    """Liveness probe. Returns ``{"status": "ok"}`` when the server is up."""
    return {"status": "ok"}


# Cache alerts from stations (Generated by /rail/departures/{station} route)
rail_alerts_cache = { }

@app.get("/alerts")
async def get_alerts():
    """Return active service disruption alerts.

    Currently only returns status from rail stations queried via /rail/departures/{station}.

    A full implementation would query
    a live feed (e.g. Traveline or Bods Disruptions API) and cache the
    results.  The response shape is intentionally stable so the frontend
    ``fetchServiceAlerts()`` hook can use it without changes.
    """

    rail_alerts = [x for alerts in rail_alerts_cache.values() for x in alerts]
    return [*rail_alerts]


@app.get("/pricing")
async def get_pricing(
    fromLat: float,
    fromLon: float,
    toLat: float,
    toLon: float,
):
    """Estimate a single-journey public-transport fare.

    Uses Haversine distance between origin and destination with simple
    England fare bands as a stub.  Distance in km; fares in GBP.

    Fare bands (approximate, single ticket):
        < 3 km   →  £1.80  (short hop)
        3–10 km  →  £2.10  (standard single — England bus fare cap)
        10–30 km →  £3.50  (regional)
        ≥ 30 km  →  £5.00  (long-distance)
    """
    import math

    dlat = math.radians(toLat - fromLat)
    dlon = math.radians(toLon - fromLon)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(fromLat)) * math.cos(math.radians(toLat))
         * math.sin(dlon / 2) ** 2)
    km = 6371 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    if km < 3:
        price, band = 1.80, "short-hop"
    elif km < 10:
        price, band = 2.10, "standard"
    elif km < 30:
        price, band = 3.50, "regional"
    else:
        price, band = 5.00, "long-distance"

    return {
        "price": price,
        "currency": "GBP",
        "distanceKm": round(km, 2),
        "band": band,
        "fares": [
            {"type": "single", "price": price},
            {"type": "return", "price": round(price * 1.8, 2)},
            {"type": "day",    "price": round(price * 2.5, 2)},
        ],
    }


@app.get("/status")
async def status():
    """Return background precompute status (walking)."""
    from fastapi.responses import JSONResponse

    if _base_cache is None:
        return JSONResponse(status_code=503, content={"error": "Backend not initialized"})

    wl = _base_cache.get("walking_loader")
    if wl is None:
        return {"walking_precompute": None}

    return {
        "walking_precompute": {
            "running": bool(getattr(wl, "precomputing", False)),
            "use_fallback": bool(getattr(wl, "precompute_use_fallback", False)),
            "total": int(getattr(wl, "precompute_total", 0)),
            "processed": int(getattr(wl, "precompute_processed", 0)),
            "inserted": int(getattr(wl, "precompute_inserted", 0)),
        }
    }


# -- Stop search ---------------------------------------------------------------

# Known Lancashire place names, areas, and common POIs used for fuzzy
# correction when the user makes a typo.  Nominatim has no built-in
# fuzzy matching, so we correct the query first using difflib.
_LANCASHIRE_PLACES: List[str] = [
    # Major towns / cities
    "Lancaster", "Preston", "Blackpool", "Blackburn", "Burnley",
    "Accrington", "Morecambe", "Fleetwood", "Lytham", "Clitheroe",
    "Chorley", "Leyland", "Ormskirk", "Skelmersdale", "Colne",
    "Nelson", "Darwen", "Rawtenstall", "Bacup", "Haslingden",
    "Carnforth", "Garstang", "Poulton-le-Fylde", "Thornton-Cleveleys",
    "Cleveleys", "Kirkham", "Longridge", "Bamber Bridge", "Fulwood",
    "Ingleton", "Heysham", "Silverdale", "Bolton-le-Sands",
    "Galgate", "Cockerham", "Knott End", "Whalley", "Ribchester",
    "Oswaldtwistle", "Great Harwood", "Rishton", "Clayton-le-Moors",
    "Padiham", "Brierfield", "Barnoldswick", "Earby",
    "Penwortham", "Lostock Hall", "Walton-le-Dale", "Longton",
    "Freckleton", "Warton", "Wesham",
    # University / institutions
    "Lancaster University", "UCLan", "Edge Hill University",
    # Transport hubs
    "Lancaster Bus Station", "Preston Bus Station",
    "Blackpool North", "Blackpool South", "Blackpool Pleasure Beach",
    "Lancaster Railway Station", "Preston Railway Station",
    "Morecambe Railway Station", "Carnforth Railway Station",
    # Landmarks / attractions
    "Blackpool Tower", "Blackpool Zoo", "Williamson Park",
    "Beacon Fell", "Pendle Hill", "Forest of Bowland",
    "Ribble Valley", "Lune Valley", "Trough of Bowland",
    "Ashton Memorial", "Lancaster Castle", "Lancaster Priory",
    "Morecambe Bay", "Happy Mount Park", "Stanley Park",
    # Common POI / brand names people search for
    "Sainsbury", "Sainsburys", "Sainsbury's",
    "Tesco", "Asda", "Aldi", "Lidl", "Morrisons", "Morrison",
    "Nando's", "Nandos", "McDonald's", "McDonalds",
    "Costa", "Starbucks", "Greggs",
    "Hospital", "Royal Lancaster Infirmary", "Royal Preston Hospital",
    "Blackpool Victoria Hospital",
    "Arndale", "Fishergate", "St George's Shopping Centre",
    "Houndshill", "Market", "Library", "Cinema", "Park", "Beach",
]

# Lower-cased version for matching
_LANCASHIRE_PLACES_LOWER: List[str] = [p.lower() for p in _LANCASHIRE_PLACES]


def _fuzzy_correct_query(query: str, threshold: float = 0.6) -> str:
    """Return the best fuzzy match from the known-places list.

    If the query (or any individual word ≥ 4 chars) closely matches a
    known place/POI, return the corrected version. Otherwise return the
    original query unchanged.
    """
    from difflib import SequenceMatcher, get_close_matches

    q = query.strip()
    q_lower = q.lower()

    # 1) Try matching the full query against known places
    matches = get_close_matches(q_lower, _LANCASHIRE_PLACES_LOWER, n=1, cutoff=threshold)
    if matches:
        # Return the original-case version from the canonical list
        idx = _LANCASHIRE_PLACES_LOWER.index(matches[0])
        return _LANCASHIRE_PLACES[idx]

    # 2) Try matching individual words (for multi-word queries like
    #    "Lancster University" → correct "Lancster" → "Lancaster")
    words = q.split()
    corrected_words = []
    changed = False

    for w in words:
        # Only attempt fuzzy correction for reasonably long tokens
        if len(w) >= 4:
            matches = get_close_matches(w.lower(), _LANCASHIRE_PLACES_LOWER, n=1, cutoff=threshold)
            if matches:
                idx = _LANCASHIRE_PLACES_LOWER.index(matches[0])
                corrected_words.append(_LANCASHIRE_PLACES[idx])
                changed = True
                continue
        corrected_words.append(w)

    corrected = " ".join(corrected_words)
    if changed:
        return corrected
    return q


def _looks_like_street(s: str) -> bool:
    """Heuristic: return True if a string looks like a street/address token.

    Used to decide whether comma suffixes are towns ('Morrisons, Morecambe')
    vs street/address details.
    """
    if not s:
        return False
    s = str(s).strip().lower()
    import re
    # If it contains a number (house number, postcode fragment), treat as street/address
    if re.search(r"\d", s):
        return True

    street_tokens = {
        'street', 'st', 'road', 'rd', 'lane', 'ln', 'avenue', 'ave', 'drive', 'dr',
        'way', 'court', 'ct', 'crescent', 'close', 'terrace', 'gardens', 'place',
        'square', 'hill', 'park', 'boulevard', 'blvd', 'grove', 'row', 'alley', 'isle',
        'mount', 'mountain', 'walk', 'end'
    }
    words = re.split(r"[\s,]+", s)
    for w in words:
        if w in street_tokens:
            return True
        if w.rstrip('.') in street_tokens:
            return True

    # Very short tails (1-3 chars) are more likely postcode fragments or abbreviations
    if 0 < len(s) <= 3:
        return True

    return False


# --- Geometry assembly endpoint ----------------------------------------
@app.get("/route/geometry")
def route_geometry(route_id: str):
    """Return best-effort route geometry for an in-memory route_id.

    Policy:
            - Full-route polylines are removed.
      - This endpoint now attempts to return a *representative* polyline built
        from stitched stop-to-stop fragment tracks (`route_link_tracks`).

    Response:
      {"coords": [[lat, lon], ...], "source": "route_link_tracks"} or {"error": ...}
    """
    if not route_id:
        return {"error": "missing_route_id"}

    merged = None
    try:
        if globals().get('_base_cache'):
            prebuilt = _base_cache.get('prebuilt_cache') if _base_cache else None
            if prebuilt:
                for _k, v in prebuilt.items():
                    try:
                        merged = v[0]
                    except Exception:
                        merged = None
                    if merged:
                        break
    except Exception:
        merged = None

    if not merged:
        return {"error": "no_merged"}

    # Resolve route_int from metadata
    route_int = None
    try:
        metas = getattr(merged, 'route_metadata', None) or []
        for m in metas:
            if isinstance(m, dict) and str(m.get('route_id') or '') == str(route_id):
                if m.get('route_int') is not None:
                    route_int = int(m.get('route_int'))
                break
    except Exception:
        route_int = None

    if route_int is None:
        return {"error": "route_id_not_found"}

    try:
        # walking object isn't required for this helper if fragments exist; we
        # pass None and let the helper fall back safely.
        coords = _try_stitch_route_link_tracks_for_route_int(merged, route_int, None)  # type: ignore
    except Exception:
        coords = None

    coords = _normalize_latlon_coords(coords)
    if not coords or len(coords) < 2:
        return {"error": "route_link_tracks_not_found"}

    return {"coords": coords, "source": "route_link_tracks"}


# --- Geometry assembly endpoint helpers -------------------------------
# NOTE: Live matching and geometry resolution are intentionally in-memory only.
# DB helpers for fetching logged journeys / journey_times are removed to avoid
# accidental fallback to database lookups.


@app.get("/debug/leg-geometry-stitch")
def debug_leg_geometry_stitch(
    merged_key: str,
    route_int: int,
    from_stop_id: str,
    to_stop_id: str,
):
    """Debug helper: explain why route_link_tracks stitching succeeded/failed.

    Enable with ROUTE_GEOM_DIAG=1.

    Returns mapping info (ATCO->stop_int), route stop indices, and which
    fragment keys are missing along the stop-chain between from/to.
    """
    from fastapi import HTTPException

    if str(os.environ.get('ROUTE_GEOM_DIAG') or '').lower() not in ('1', 'true', 'yes'):
        raise HTTPException(status_code=404, detail='Not Found')

    try:
        merged, _router, _walking = _get_router_for_merged_key(merged_key, apply_delay=False)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f'bad_merged_key: {exc}')

    try:
        ri = int(route_int)
    except Exception:
        raise HTTPException(status_code=400, detail='invalid_route_int')

    # Bounds check
    try:
        meta_len = len(getattr(merged, 'route_metadata', None) or [])
    except Exception:
        meta_len = None
    if meta_len is not None and (ri < 0 or ri >= meta_len):
        return {
            'ok': False,
            'error': 'route_int_out_of_range',
            'route_int': ri,
            'merged_key': merged_key,
            'merged_route_count': meta_len,
        }

    # Get fragment map
    try:
        link_map = merged.get_route_link_tracks(ri) if hasattr(merged, 'get_route_link_tracks') else (getattr(merged, 'route_link_tracks', None) or [None])[ri]
    except Exception:
        link_map = None

    # Route stops and ATCO mapping
    try:
        route_stops = merged.route_stops[ri] if ri < len(getattr(merged, 'route_stops', []) or []) else []
    except Exception:
        route_stops = []

    atco_to_stop: dict[str, int] = {}
    try:
        for s in route_stops or []:
            try:
                c = merged.get_atco_code(s)
            except Exception:
                c = None
            if c:
                atco_to_stop.setdefault(str(c).strip(), s)
    except Exception:
        atco_to_stop = {}

    fs = atco_to_stop.get(str(from_stop_id).strip())
    ts = atco_to_stop.get(str(to_stop_id).strip())

    missing_keys: list[list[int]] = []
    checked_keys: list[list[int]] = []
    direction = None
    try:
        if fs is not None and ts is not None and fs in route_stops and ts in route_stops and fs != ts and isinstance(link_map, dict):
            i = route_stops.index(fs)
            j = route_stops.index(ts)
            step = 1 if j > i else -1
            direction = 'forward' if step == 1 else 'reverse'
            k = i
            while k != j:
                a = route_stops[k]
                b = route_stops[k + step]
                checked_keys.append([int(a), int(b)])
                seg = link_map.get((a, b))
                if not seg:
                    seg = link_map.get((b, a))
                if not seg:
                    missing_keys.append([int(a), int(b)])
                k += step
    except Exception:
        pass

    return {
        'ok': True,
        'merged_key': merged_key,
        'route_int': ri,
        'link_map_is_dict': isinstance(link_map, dict),
        'link_map_len': (len(link_map) if isinstance(link_map, dict) else None),
        'route_stops_len': (len(route_stops) if isinstance(route_stops, list) else None),
        'from_stop_id': str(from_stop_id).strip(),
        'to_stop_id': str(to_stop_id).strip(),
        'from_stop_mapped': fs is not None,
        'to_stop_mapped': ts is not None,
        'from_stop_int': fs,
        'to_stop_int': ts,
        'direction': direction,
        'checked_pairs': checked_keys[:200],
        'missing_pairs': missing_keys[:200],
    }


@app.get("/debug/match_explain")
async def debug_match_explain(
    line: str,
    dest: str,
    lat: float,
    lon: float,
    operator_ref: str = None,
    origin_dep_secs: int = None,
    origin_tz_offset_secs: int = 0,
    origin_atco: str = None,
    destination_atco: str = None,
    strict_tol: int = 600,
    max_candidates: int = 15,
):
    """Explain why strict live matching did (or didn't) match.

    This is a *debug/provenance* endpoint intended for local diagnosis.
    It mirrors the strict matcher's candidate filtering and then exposes
    the numeric checks used by the final spatial/temporal gate.

    Enable with BUS_LIVE_PROVENANCE=1.
    """
    from fastapi import HTTPException
    import math
    from datetime import datetime

    bus_provenance = str(os.environ.get('BUS_LIVE_PROVENANCE') or '').lower() in ('1', 'true', 'yes')
    if not bus_provenance:
        raise HTTPException(status_code=404, detail='Not Found')

    try:
        today = datetime.now().date().isoformat()
        now = datetime.now()
        now_seconds = now.hour * 3600 + now.minute * 60 + now.second
        merged, router, walking = get_router_for_date(today, start_time=now_seconds, apply_delay=False)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f'no_merged_data: {exc}')

    line_q = (line or '').strip()
    simple_line = line_q.split(':')[-1].strip()
    dest_q = (dest or '').strip().lower()

    feed_origin_atco_n = str(origin_atco).strip() if origin_atco else None
    feed_destination_atco_n = str(destination_atco).strip() if destination_atco else None

    # Helper: get journey stop atcos (first/last few)
    #
    # NOTE: some feeds shift the "effective" origin/destination by a couple
    # of stops (e.g. short turns / timing-point differences). For staged
    # latching/gating, allow a small stop-index shift window rather than
    # requiring an exact match strictly in the first/last 3.
    try:
        ENDPOINT_SHIFT_STOPS = int(os.environ.get('MATCH_ENDPOINT_SHIFT_STOPS', '2'))
    except Exception:
        ENDPOINT_SHIFT_STOPS = 2

    def _journey_origin_atcos(jt):
        out = []
        try:
            n = min(max(0, 3 + int(ENDPOINT_SHIFT_STOPS)), len(jt))
            for i in range(n):
                atco = merged.get_atco_code(jt[i][0])
                if atco:
                    out.append(str(atco).strip())
        except Exception:
            return []
        return out

    def _journey_dest_atcos(jt):
        out = []
        try:
            n = min(max(0, 3 + int(ENDPOINT_SHIFT_STOPS)), len(jt))
            for i in range(n):
                atco = merged.get_atco_code(jt[-1 - i][0])
                if atco:
                    out.append(str(atco).strip())
        except Exception:
            return []
        return out

    # Stage A: line candidates
    line_candidates = []
    for j_id, jm in enumerate(merged.journey_metadata):
        if not jm:
            continue
        ln = (jm.get('line_name') or '')
        if ln.split(':')[-1].strip() == simple_line:
            line_candidates.append(j_id)
    if not line_candidates:
        return {
            'input': {'line': line, 'dest': dest, 'lat': lat, 'lon': lon, 'operator_ref': operator_ref},
            'reasons': ['no_line_match'],
            'candidates': [],
        }

    # Stage B: operator filter (match strict matcher behaviour)
    if operator_ref:
        opn = str(operator_ref).strip()
        filtered = []
        for j_id in line_candidates:
            jm = merged.journey_metadata[j_id] or {}
            op_noc = (jm.get('operator_national_code') or '').strip()
            svc = (jm.get('service_code') or '').strip()
            if not svc:
                ln = (jm.get('line_name') or '')
                if ':' in ln:
                    svc = ln.split(':')[0].strip()
            if op_noc == opn or (svc and svc == opn):
                filtered.append(j_id)
        line_candidates = filtered
        if not line_candidates:
            return {
                'input': {'line': line, 'dest': dest, 'lat': lat, 'lon': lon, 'operator_ref': operator_ref},
                'reasons': ['operator_mismatch'],
                'candidates': [],
            }

    # Stage C: ATCO latch (similar to _stage_strict_match_candidates)
    staged = []
    for j_id in line_candidates:
        jm = merged.journey_metadata[j_id] or {}
        try:
            jt = merged.journey_times[j_id]
        except Exception:
            continue
        dest_atcos = _journey_dest_atcos(jt)
        origin_atcos = _journey_origin_atcos(jt)
        staged.append({
            'j_id': j_id,
            'journey_id': jm.get('journey_id'),
            'line_name': jm.get('line_name'),
            'service_code': jm.get('service_code'),
            'operator_national_code': jm.get('operator_national_code'),
            'origin_atcos': origin_atcos,
            'dest_atcos': dest_atcos,
        })

    latched_ids = set([x['j_id'] for x in staged])
    if feed_origin_atco_n:
        origin_only = set([x['j_id'] for x in staged if feed_origin_atco_n in (x.get('origin_atcos') or [])])
        if origin_only:
            latched_ids = origin_only
    if feed_destination_atco_n and (not feed_origin_atco_n or not latched_ids):
        dest_only = set([x['j_id'] for x in staged if feed_destination_atco_n in (x.get('dest_atcos') or [])])
        if dest_only:
            latched_ids = dest_only

    staged = [x for x in staged if x['j_id'] in latched_ids]
    if not staged:
        return {
            'input': {
                'line': line,
                'dest': dest,
                'lat': lat,
                'lon': lon,
                'operator_ref': operator_ref,
                'origin_atco': origin_atco,
                'destination_atco': destination_atco,
            },
            'reasons': ['destination_and_origin_atco_mismatch'],
            'candidates': [],
        }

    # Compute an adjusted origin_dep based on feed tz offset.
    od_adj = None
    if origin_dep_secs is not None:
        try:
            od_adj = int(origin_dep_secs) + int(origin_tz_offset_secs or 0)
        except Exception:
            try:
                od_adj = int(origin_dep_secs)
            except Exception:
                od_adj = None

    # Rank candidates by a fast proxy: distance from current point to any stop coords (min haversine).
    def _min_stop_dist_m(j_id: int) -> float:
        try:
            jt = merged.journey_times[j_id]
        except Exception:
            return float('inf')
        best = float('inf')
        for stop_int, _arr_t, _dep_t in jt:
            try:
                coords = walking.get_loc_coords(stop_int)
            except Exception:
                coords = None
            if not coords or len(coords) != 2:
                continue
            try:
                d = _haversine_m(lat, lon, coords[0], coords[1])
            except Exception:
                continue
            if d < best:
                best = d
        return best

    staged_scored = []
    for x in staged:
        j_id = x['j_id']
        dmin = _min_stop_dist_m(j_id)
        x2 = dict(x)
        x2['min_stop_dist_m'] = None if (not math.isfinite(dmin)) else float(dmin)
        staged_scored.append(x2)
    staged_scored.sort(key=lambda r: (r.get('min_stop_dist_m') if r.get('min_stop_dist_m') is not None else 1e18))
    staged_scored = staged_scored[:max(1, int(max_candidates or 15))]

    # For each candidate, compute the same key numbers used by the strict matcher gating:
    # - whether origin_dep aligns with candidate start dep
    # - a crude inferred progress using nearest stop index
    # - expected time at that progress (linear interp by stop times)
    # NOTE: This is intentionally an explanation tool; it doesn't need to reimplement
    # the full matcher exactly, but should highlight which numeric checks are violated.
    explained = []
    for x in staged_scored:
        j_id = x['j_id']
        jm = merged.journey_metadata[j_id] or {}
        try:
            jt = merged.journey_times[j_id]
        except Exception:
            continue

        # Determine candidate start_dep at the origin ATCO if provided.
        start_dep = None
        try:
            start_dep = jt[0][2]
        except Exception:
            start_dep = None
        origin_stop_time = None
        if feed_origin_atco_n:
            try:
                for stop_int, arr_t, dep_t in jt:
                    s_atco = merged.get_atco_code(stop_int)
                    if s_atco and str(s_atco).strip() == feed_origin_atco_n:
                        origin_stop_time = dep_t if dep_t is not None else arr_t
                        break
            except Exception:
                origin_stop_time = None
            if origin_stop_time is not None:
                start_dep = origin_stop_time

        origin_dep_aligned = None
        if od_adj is not None and start_dep is not None:
            try:
                origin_dep_aligned = abs(int(start_dep) - int(od_adj)) <= int(strict_tol)
            except Exception:
                origin_dep_aligned = None

        # Find nearest stop index and use it as a crude progress proxy.
        nearest = None
        nearest_i = None
        nearest_stop = None
        for i, (stop_int, arr_t, dep_t) in enumerate(jt):
            try:
                coords = walking.get_loc_coords(stop_int)
            except Exception:
                coords = None
            if not coords or len(coords) != 2:
                continue
            try:
                d = _haversine_m(lat, lon, coords[0], coords[1])
            except Exception:
                continue
            if nearest is None or d < nearest:
                nearest = d
                nearest_i = i
                nearest_stop = stop_int

        progress = None
        if nearest_i is not None and len(jt) > 1:
            try:
                progress = float(nearest_i) / float(max(1, (len(jt) - 1)))
            except Exception:
                progress = None

        # Expected time at this progress (simple stop-time interpolation).
        expected_time = None
        if nearest_i is not None:
            try:
                # pick dep if available else arr
                t_here = jt[nearest_i][2] if jt[nearest_i][2] is not None else jt[nearest_i][1]
                expected_time = t_here
            except Exception:
                expected_time = None

        temporal_delta_s = None
        if expected_time is not None:
            try:
                temporal_delta_s = int(now_seconds) - int(expected_time)
            except Exception:
                temporal_delta_s = None

        explained.append({
            'j_id': j_id,
            'journey_id': jm.get('journey_id'),
            'line_name': jm.get('line_name'),
            'service_code': jm.get('service_code'),
            'operator_national_code': jm.get('operator_national_code'),
            'min_stop_dist_m': x.get('min_stop_dist_m'),
            'nearest_stop_dist_m': None if nearest is None else float(nearest),
            'nearest_stop_index': nearest_i,
            'nearest_stop_atco': (merged.get_atco_code(nearest_stop) if nearest_stop is not None else None),
            'progress_proxy': progress,
            'start_dep_candidate': start_dep,
            'origin_dep_adj': od_adj,
            'origin_dep_aligned': origin_dep_aligned,
            'expected_time_at_nearest_stop': expected_time,
            'now_seconds': now_seconds,
            'temporal_delta_s_now_minus_expected': temporal_delta_s,
            'origin_atco_in_first3': (feed_origin_atco_n in (x.get('origin_atcos') or []) if feed_origin_atco_n else None),
            'dest_atco_in_last3': (feed_destination_atco_n in (x.get('dest_atcos') or []) if feed_destination_atco_n else None),
        })

    # Also call the existing coarse diagnosis and the actual matcher to show what it decided.
    coarse = _diagnose_match_failure(
        line,
        dest,
        lat,
        lon,
        origin_dep_secs=origin_dep_secs,
        operator_ref=operator_ref,
        strict_tol=strict_tol,
        feed_origin_atco=origin_atco,
        feed_destination_atco=destination_atco,
        origin_tz_offset_secs=origin_tz_offset_secs,
    )
    matched = None
    try:
        matched = _compute_delay_from_timetable(
            line,
            dest,
            lat,
            lon,
            return_jid=True,
            origin_dep_secs=origin_dep_secs,
            origin_tz_offset_secs=origin_tz_offset_secs,
            operator_ref=operator_ref,
            strict_tol=strict_tol,
            feed_origin_atco=origin_atco,
            feed_destination_atco=destination_atco,
        )
    except Exception as exc:
        matched = {'error': str(exc)}

    return {
        'input': {
            'line': line,
            'dest': dest,
            'lat': lat,
            'lon': lon,
            'operator_ref': operator_ref,
            'origin_dep_secs': origin_dep_secs,
            'origin_tz_offset_secs': origin_tz_offset_secs,
            'origin_atco': origin_atco,
            'destination_atco': destination_atco,
            'strict_tol': strict_tol,
            'max_candidates': max_candidates,
        },
        'coarse_reject_reasons': coarse,
        'matcher_return': repr(matched),
        'candidates': explained,
    }


def _query_osrm_for_coords(osrm_base: str, coords_lonlat: list):
    """Call OSRM route with a list of 'lon,lat' strings; return list of [lat,lon] or None on failure."""
    if not coords_lonlat:
        return None
    coords_str = ";".join(coords_lonlat)
    url = osrm_base.rstrip('/') + f"/route/v1/driving/{coords_str}?overview=full&geometries=geojson"
    try:
        req = UrllibRequest(url, headers={"User-Agent": "transport-backend"})
        with urlopen(req, timeout=10) as resp:
            data = json.load(resp)
        if data.get('code') != 'Ok':
            return None
        routes = data.get('routes') or []
        if not routes:
            return None
        geom = routes[0].get('geometry')
        if not geom:
            return None
        # geometry is GeoJSON LineString coords [[lon,lat],...]
        coords = [[c[1], c[0]] for c in geom.get('coordinates', [])]
        return coords
    except URLError:
        return None
    except Exception:
        return None


def _query_osrm_for_coords_profile(osrm_base: str, coords_lonlat: list, profile: str = 'driving'):
    """Call OSRM route with a list of 'lon,lat' strings and a profile; return list of [lat,lon] or None on failure."""
    if not coords_lonlat:
        return None
    coords_str = ";".join(coords_lonlat)
    url = osrm_base.rstrip('/') + f"/route/v1/{profile}/{coords_str}?overview=full&geometries=geojson"
    try:
        req = UrllibRequest(url, headers={"User-Agent": "transport-backend"})
        with urlopen(req, timeout=10) as resp:
            data = json.load(resp)
        if data.get('code') != 'Ok':
            return None
        routes = data.get('routes') or []
        if not routes:
            return None
        geom = routes[0].get('geometry')
        if not geom:
            return None
        coords = [[c[1], c[0]] for c in geom.get('coordinates', [])]
        return coords
    except URLError:
        return None
    except Exception:
        return None


@app.get("/route/walking")
def route_walking(from_lat: float, from_lon: float, to_lat: float, to_lon: float):
    """Return walking geometry between two points by proxying OSRM foot profile.

    Query params: from_lat, from_lon, to_lat, to_lon
    Response: {"coords": [[lat, lon], ...], "source": "osrm"} or {"error": "..."}
    """
    osrm_base = os.environ.get('OSRM_URL', 'http://localhost:5012')
    try:
        coords_lonlat = [f"{from_lon},{from_lat}", f"{to_lon},{to_lat}"]
        coords = _query_osrm_for_coords_profile(osrm_base, coords_lonlat, profile='foot')
        if coords and len(coords) >= 2:
            return {"coords": coords, "source": "osrm"}
        return {"error": "no_geometry_available"}
    except Exception:
        return {"error": "failed"}


@app.get("/osrm/route")
def osrm_route_proxy(
    coords: str,
    profile: str = "driving",
    overview: str = "full",
    geometries: str = "geojson",
):
    """Proxy OSRM /route via the backend.

    This avoids frontend DNS/CORS issues when OSRM is running inside Docker on a
    compose-only hostname.

    Query params:
      coords:   OSRM coordinate string "lon,lat;lon,lat;..." (at least 2 points)
      profile:  driving|foot|bike...
      overview: full|simplified|false
      geometries: geojson|polyline|polyline6

    Response: raw OSRM JSON on success; {"error": "..."} on failure.
    """
    osrm_base = os.environ.get('OSRM_URL', 'http://localhost:5012').rstrip('/')
    try:
        if not coords or ';' not in coords:
            return JSONResponse({"error": "invalid_coords"}, status_code=400)

        safe_profile = (profile or 'driving').strip().lower()
        # Keep this permissive; OSRM will validate values.
        qp = urlencode({
            'overview': overview,
            'geometries': geometries,
        })
        url = f"{osrm_base}/route/v1/{safe_profile}/{coords}?{qp}"

        req = UrllibRequest(url, headers={"User-Agent": "transport-backend"})
        with urlopen(req, timeout=10) as resp:
            data = json.load(resp)
        return data
    except HTTPError as e:
        # OSRM returns useful JSON error bodies (e.g. {code:"NoMatch"}) with
        # HTTP 4xx. Preserve those so the frontend can decide whether to fall
        # back to /route.
        try:
            body = e.read()
            data = json.loads(body.decode('utf-8')) if body else {"error": "osrm_http_error"}
        except Exception:
            data = {"error": "osrm_http_error"}
        return JSONResponse(data, status_code=getattr(e, 'code', 502) or 502)
    except URLError:
        return JSONResponse({"error": "osrm_unreachable"}, status_code=502)
    except Exception:
        return JSONResponse({"error": "osrm_proxy_failed"}, status_code=500)


@app.post("/osrm/match")
async def osrm_match_proxy(payload: dict):
    """Proxy OSRM /match via the backend using POST JSON.

    The stock OSRM API is GET-based, but using POST here avoids URL length
    limits when matching dense traces (hundreds of points).

    Expected JSON body:
      {
        "coords": [[lat, lon], ...]  OR  ["lon,lat", "lon,lat", ...],
        "profile": "driving"|"foot"|... (optional, default driving)
      }

    Response: raw OSRM JSON on success; {"error": "..."} on failure.
    """
    osrm_base = os.environ.get('OSRM_URL', 'http://localhost:5012').rstrip('/')
    try:
        if not isinstance(payload, dict):
            return JSONResponse({"error": "invalid_payload"}, status_code=400)

        coords_in = payload.get('coords')
        if not coords_in or not isinstance(coords_in, list) or len(coords_in) < 2:
            return JSONResponse({"error": "invalid_coords"}, status_code=400)

        coords_lonlat: list[str] = []
        for item in coords_in:
            if isinstance(item, str):
                # Expect "lon,lat"
                if ',' not in item:
                    return JSONResponse({"error": "invalid_coords"}, status_code=400)
                coords_lonlat.append(item)
            elif isinstance(item, (list, tuple)) and len(item) == 2:
                # Expect [lat, lon]
                lat, lon = item
                try:
                    latf = float(lat)
                    lonf = float(lon)
                except Exception:
                    return JSONResponse({"error": "invalid_coords"}, status_code=400)
                coords_lonlat.append(f"{lonf},{latf}")
            else:
                return JSONResponse({"error": "invalid_coords"}, status_code=400)

        safe_profile = (payload.get('profile') or 'driving').strip().lower()

        # Conservative defaults for matching traces.
        # (Frontend can add more knobs later if needed.)
        qp = urlencode({
            'overview': 'full',
            'geometries': 'geojson',
            'annotations': 'false',
            'steps': 'false',
            # Restrict snapping radius in meters; keeps OSRM from making wild jumps.
            'radiuses': ';'.join(['25'] * len(coords_lonlat)),
        })

        coords_str = ';'.join(coords_lonlat)
        url = f"{osrm_base}/match/v1/{safe_profile}/{coords_str}?{qp}"

        req = UrllibRequest(url, headers={"User-Agent": "transport-backend"})
        with urlopen(req, timeout=20) as resp:
            data = json.load(resp)
        return data
    except HTTPError as e:
        try:
            body = e.read()
            data = json.loads(body.decode('utf-8')) if body else {"error": "osrm_http_error"}
        except Exception:
            data = {"error": "osrm_http_error"}
        return JSONResponse(data, status_code=getattr(e, 'code', 502) or 502)
    except URLError:
        return JSONResponse({"error": "osrm_unreachable"}, status_code=502)
    except Exception:
        return JSONResponse({"error": "osrm_proxy_failed"}, status_code=500)


@app.get("/route/leg-geometry")
def route_leg_geometry(from_lat: float, from_lon: float,
                       to_lat: float, to_lon: float,
                       mode: str = "driving",
                       route_id: str | None = None,
                       route_int: int | str | None = None,
                       merged_key: str | None = None,
                       merged: Any | None = None,
                       from_stop_id: str | None = None,
                       to_stop_id: str | None = None,
                       stop_ids: str | None = None,
                       date: str | None = None,
                       departure_time: str | None = None):
    """Return geometry for a single journey leg.

    Query params:
      from_lat, from_lon – start point
      to_lat, to_lon     – end point
      mode               – 'walking' | 'bus' | 'train' | 'driving' (default)
            route_id           – optional canonical timetable route_id
            from_stop_id       – optional ATCO code for the leg start stop
            to_stop_id         – optional ATCO code for the leg end stop

    If route_id is provided and this is a bus leg, we will *prefer* the
    timetable's fragment-stitched geometry when available.

    Walking legs use the OSRM *foot* profile; all others use *driving*.
        Additional params:
            stop_ids – optional comma-separated ATCO codes for the full stop sequence
                                 of this leg (from..to). Used only as a fallback when we don't
                                 have stored fragment/track geometry.

        Response: {"coords": [[lat, lon], ...], "source": "route_link_tracks"|"stops"|"osrm"|"linear"} or {"error": "..."}
    """
    # If we're called internally with an explicit merged instance (e.g.
    # build_journey_plan_response), derive merged_key if it wasn't passed.
    # This keeps diagnostics self-contained and lets us detect merged mismatches.
    try:
        if merged_key is None and merged is not None:
            mk_date = None
            mk_bucket = None
            try:
                mk_date = getattr(getattr(merged, 'meta', None), 'date', None)
            except Exception:
                mk_date = None
            try:
                mk_bucket = getattr(merged, 'bucket', None)
            except Exception:
                mk_bucket = None

            # Fall back to request params if merged doesn't carry date/bucket.
            if not mk_date:
                mk_date = (date or '').strip() or None
            if not mk_bucket:
                # Try to infer AM/PM from departure_time when present.
                try:
                    if departure_time and isinstance(departure_time, str) and ':' in departure_time:
                        hh = int(departure_time.split(':', 1)[0])
                        mk_bucket = 'AM' if hh < 12 else 'PM'
                except Exception:
                    mk_bucket = None

            if mk_date and mk_bucket in ('AM', 'PM'):
                merged_key = f"{mk_date}|{mk_bucket}"
    except Exception:
        pass

    diag: dict[str, Any] = {
        'mode': (mode or '').strip().lower(),
        'route_id': route_id,
        'route_int_in': route_int,
        'merged_key': merged_key,
        'from_stop_id': from_stop_id,
        'to_stop_id': to_stop_id,
        'stop_ids_provided': bool(stop_ids),
    }
    # Prefer stored timetable tracks when we have enough context.
    #
    # IMPORTANT: route_int is only meaningful within a specific MergedData.
    # For live vehicle overlays we must always use *today's* MergedData so
    # the route_int index matches the live feed's mapping.
    #
    # IMPORTANT: The frontend often calls this endpoint with mode=driving
    # even for bus legs (it uses the driving OSRM profile for road snapping).
    # When a canonical route_id is provided, we still want to prefer the
    # timetable's own fragment geometry.
    trace = os.environ.get('ROUTE_GEOM_TRACE') == '1'
    try:
        if trace:
            print('[route_leg_geometry] start', {
                'mode': mode,
                'route_id': route_id,
                'from_stop_id': from_stop_id,
                'to_stop_id': to_stop_id,
                'from': (from_lat, from_lon),
                'to': (to_lat, to_lon),
            })

        # Normalize common frontend mode labels.
        norm_mode = (mode or '').strip().lower()
        if norm_mode == 'walk':
            norm_mode = 'walking'

        if norm_mode != 'walking':
            tracks = {}
            frag_seg = []

            # Resolve the merged instance used to interpret route_int.
            # NOTE: route_int is only meaningful within the exact merged build
            # that produced it (AM/PM noon split). Live overlays must therefore
            # pass `merged_key` from /bus/live so we use the same build.
            merged_today = None
            try:
                if merged is not None:
                    merged_today = merged
                elif merged_key:
                    merged_today, _router_today, _walking_today = _get_router_for_merged_key(merged_key, apply_delay=False)
                else:
                    today_str = datetime.now().date().isoformat()
                    merged_today, _router_today, _walking_today = get_router_for_date(
                        today_str,
                        start_time=None,
                        apply_delay=False,
                    )
            except Exception:
                merged_today = None
            diag['merged_resolved'] = bool(merged_today is not None)
            try:
                diag['merged_meta_date'] = getattr(getattr(merged_today, 'meta', None), 'date', None) if merged_today is not None else None
            except Exception:
                diag['merged_meta_date'] = None
            try:
                diag['merged_bucket'] = getattr(merged_today, 'bucket', None) if merged_today is not None else None
            except Exception:
                diag['merged_bucket'] = None

            # Optional stop-sequence fallback (ATCO codes) supplied by callers.
            # This is ONLY used when fragment stitching is unavailable.
            stop_fallback_coords = []
            try:
                parts: list[str]
                if isinstance(stop_ids, (list, tuple)):
                    parts = [str(p).strip() for p in stop_ids if str(p).strip()]
                else:
                    raw = (stop_ids or '').strip()
                    if raw:
                        parts = [p.strip() for p in raw.split(',') if p.strip()]
                    else:
                        parts = []
                if len(parts) >= 2:
                    diag['stop_ids_count'] = len(parts)
                    atco_coords_dict = {}
                    try:
                        if globals().get('_base_cache') and _base_cache.get('atco_loader'):
                            atco_coords_dict = _base_cache['atco_loader'].get_all_stop_coords() or {}
                    except Exception:
                        atco_coords_dict = {}

                    # IMPORTANT: stop-sequence fallback must work even when no
                    # merged/router cache is loaded (e.g. unit tests, minimal
                    # deployments). So we rely on atco_loader's coordinate map.
                    try:
                        for atco in parts:
                            coord = atco_coords_dict.get(atco)
                            if coord and len(coord) >= 2:
                                stop_fallback_coords.append([coord[0], coord[1]])
                    except Exception:
                        stop_fallback_coords = []
            except Exception:
                stop_fallback_coords = []
            try:
                diag['stop_fallback_coords_len'] = len(stop_fallback_coords) if isinstance(stop_fallback_coords, list) else None
            except Exception:
                pass
            # Prefer dense route_int lookup when provided.
            if route_int is not None:
                try:
                    try:
                        route_int = int(str(route_int).strip())
                    except Exception:
                        route_int = None
                    if route_int is None:
                        raise ValueError('invalid route_int')
                    merged = merged_today
                    if merged is None:
                        # Safety fallback only: if today's router wasn't available,
                        # fall back to whatever is in base cache.
                        if globals().get('_base_cache'):
                            try:
                                merged = _base_cache.get('merged')
                            except Exception:
                                merged = None
                    if merged is not None:
                        ri = int(route_int)

                        diag['route_int'] = ri
                        try:
                            diag['merged_route_count'] = len(getattr(merged, 'route_metadata', None) or [])
                        except Exception:
                            diag['merged_route_count'] = None

                        # Include the route_int -> (route_id/line_name) metadata for mismatch debugging.
                        try:
                            md = getattr(merged, 'route_metadata', None) or []
                            if 0 <= ri < len(md):
                                m0 = md[ri] or {}
                                if isinstance(m0, dict):
                                    diag['route_int_meta_route_id'] = m0.get('route_id')
                                    diag['route_int_meta_line_name'] = m0.get('line_name')
                        except Exception:
                            pass

                        # If route_int doesn't fit this merged, treat it as a
                        # mismatch (do NOT degrade to linear for bus geometry).
                        try:
                            meta_len = len(getattr(merged, 'route_metadata', None) or [])
                        except Exception:
                            meta_len = None
                        # If a caller provided an explicit merged instance (e.g. unit tests
                        # or internal callers), it may not carry full route_metadata.
                        # In that case, allow stop-sequence fallback to proceed.
                        if merged is None and meta_len is not None and (ri < 0 or ri >= meta_len):
                            return {
                                "error": "route_int_out_of_range",
                                "diag": {
                                    "route_int": ri,
                                    "merged_key": merged_key,
                                    "merged_route_count": meta_len,
                                },
                            }
                        # If caller didn't provide stop ids, derive endpoints from this
                        # route's stop list (first/last) so we stitch the *whole* route.
                        # This avoids returning raw full-route polyline geometry
                        # that may not be aligned to stops.
                        if merged is not None and (from_stop_id is None or to_stop_id is None):
                            try:
                                route_stops = merged.route_stops[ri] if ri < len(getattr(merged, 'route_stops', []) or []) else []
                            except Exception:
                                route_stops = []
                            if route_stops and len(route_stops) >= 2:
                                try:
                                    if from_stop_id is None:
                                        c0 = merged.get_atco_code(route_stops[0])
                                        if c0:
                                            from_stop_id = str(c0).strip()
                                    if to_stop_id is None:
                                        c1 = merged.get_atco_code(route_stops[-1])
                                        if c1:
                                            to_stop_id = str(c1).strip()
                                    if trace:
                                        print('[route_leg_geometry] derived_stop_ids', {
                                            'route_int': ri,
                                            'from_stop_id': from_stop_id,
                                            'to_stop_id': to_stop_id,
                                        })
                                except Exception:
                                    pass

                        # Prefer stitching from fragment index when we have stop context.
                        if merged is not None and from_stop_id and to_stop_id:
                            try:
                                # Prefer a lazy accessor when available; otherwise
                                # fall back to the raw attribute.
                                if hasattr(merged, 'get_route_link_tracks'):
                                    link_map = merged.get_route_link_tracks(ri)
                                else:
                                    links = getattr(merged, 'route_link_tracks', None)
                                    link_map = links[ri] if (links and 0 <= ri < len(links)) else None

                                if trace:
                                    try:
                                        print('[route_leg_geometry] link_map', {
                                            'route_int': ri,
                                            'link_map_type': type(link_map).__name__,
                                            'link_map_len': (len(link_map) if isinstance(link_map, dict) else None),
                                        })
                                    except Exception:
                                        pass

                                if link_map is not None:
                                    diag['link_map_is_none'] = False
                                    fs = None
                                    ts = None
                                    route_stops = []
                                    try:
                                        route_stops = merged.route_stops[ri] if ri < len(getattr(merged, 'route_stops', []) or []) else []
                                    except Exception:
                                        route_stops = []
                                    try:
                                        diag['route_stops_len'] = len(route_stops) if isinstance(route_stops, list) else None
                                    except Exception:
                                        pass
                                    i = None
                                    j = None
                                    try:
                                        if route_stops and (from_stop_id or to_stop_id):
                                            resolved = _resolve_route_stop_occurrences(
                                                merged,
                                                route_stops,
                                                str(from_stop_id).strip() if from_stop_id else None,
                                                str(to_stop_id).strip() if to_stop_id else None,
                                            )
                                            fs = resolved.get('from_stop_int')
                                            ts = resolved.get('to_stop_int')
                                            i = resolved.get('from_pos')
                                            j = resolved.get('to_pos')
                                    except Exception:
                                        fs = None
                                        ts = None
                                        i = None
                                        j = None

                                    diag['from_stop_mapped'] = bool(fs is not None)
                                    diag['to_stop_mapped'] = bool(ts is not None)

                                    if fs is not None and ts is not None:
                                        # route_stops already resolved above.

                                        frag = None
                                        # Primary: chain along route stop order.
                                        if route_stops and i is not None and j is not None and fs != ts:
                                            step = 1 if j > i else -1
                                            stitched = []
                                            ok = True
                                            k = i
                                            while k != j:
                                                a = route_stops[k]
                                                b = route_stops[k + step]
                                                seg2 = link_map.get((a, b))
                                                if not seg2:
                                                    rev2 = link_map.get((b, a))
                                                    if rev2:
                                                        seg2 = list(reversed(rev2))
                                                if not seg2:
                                                    ok = False
                                                    break
                                                if stitched and stitched[-1] == seg2[0]:
                                                    stitched.extend(seg2[1:])
                                                else:
                                                    stitched.extend(seg2)
                                                k += step
                                            if ok and len(stitched) >= 2:
                                                frag = stitched

                                        # Secondary: exact fragment (either direction)
                                        if not frag:
                                            frag = link_map.get((fs, ts))
                                            if not frag:
                                                rev = link_map.get((ts, fs))
                                                if rev:
                                                    frag = list(reversed(rev))

                                        if frag and len(frag) >= 2:
                                            frag_seg = [[t[0], t[1]] for t in frag]
                                            if trace:
                                                print('[route_leg_geometry] returning fragment-stitched subsegment', {
                                                    'route_int': ri,
                                                    'len': len(frag_seg),
                                                })
                                            diag['ok'] = True
                                            diag['stitch_kind'] = 'route_link_tracks'
                                            diag['coords_len'] = len(frag_seg)
                                            return {"coords": frag_seg, "source": "route_link_tracks", "diag": diag}
                                else:
                                    diag['link_map_is_none'] = True
                            except Exception:
                                frag_seg = []

            # IMPORTANT: do NOT return raw full-route polyline geometry when
                        # route_int is provided. If fragment stitching isn't available,
                        # we fall through to OSRM/linear fallback rather than emitting
                        # a potentially messy non-stop-stitched polyline.
                        tracks = []
                except Exception:
                    tracks = []

            # Policy: full-route polylines are not allowed as a fallback here.
            # If fragment stitching didn't succeed, fall through to OSRM/linear.
    except Exception:
        # Best-effort only; fall through to OSRM/linear.
        if trace:
            import traceback
            print('[route_leg_geometry] exception in legacy-polyline path')
            traceback.print_exc()
        pass

    # Fallback policy:
    # - walking legs may use OSRM foot routing
    # - if caller provided a full stop sequence (stop_ids) and we couldn't stitch
    #   tracks, return those stop coordinates unsmoothed.
    # - all other non-walking cases return a straight line between endpoints.
    if norm_mode == 'walking':
        osrm_base = os.environ.get('OSRM_URL', 'http://localhost:5012')
        try:
            coords_lonlat = [f"{from_lon},{from_lat}", f"{to_lon},{to_lat}"]
            coords = _query_osrm_for_coords_profile(osrm_base, coords_lonlat,
                                                     profile='foot')
            if coords and len(coords) >= 2:
                diag['ok'] = True
                diag['stitch_kind'] = 'osrm'
                diag['coords_len'] = len(coords)
                return {"coords": coords, "source": "osrm", "diag": diag}
        except Exception:
            pass

    try:
        # Only use the provided stop sequence if it looks valid.
        if 'stop_fallback_coords' in locals() and isinstance(stop_fallback_coords, list) and len(stop_fallback_coords) >= 2:
            diag['ok'] = True
            diag['stitch_kind'] = 'stops_fallback'
            diag['coords_len'] = len(stop_fallback_coords)
            return {"coords": stop_fallback_coords, "source": "stops", "diag": diag}
    except Exception:
        pass

    diag['ok'] = True
    diag['stitch_kind'] = 'linear_fallback'
    diag['coords_len'] = 2
    return {"coords": [[from_lat, from_lon], [to_lat, to_lon]],
            "source": "linear", "diag": diag}


def _fetch_legacy_route_polyline(route_id: str, *, date_str: str | None = None, bucket: str | None = None):
    """Deprecated: legacy full-route polylines are intentionally removed.

    Keep this stub to preserve older debug scripts/tests that monkeypatch
    this symbol, but the backend must never load/use legacy full-route polylines.
    """
    return []


def _find_prefixed_route_candidate(route_id: str):
    """Deprecated: DB-based prefix resolution removed (in-memory only)."""
    return None


def _haversine(lat1, lon1, lat2, lon2):
    import math
    r = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda/2)**2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1-a))


def _subsegment_from_coords(tracks: list, stop_atcos: list, walking_coords: dict):
    """Slice a subsegment from already-resolved track coords.

    This is the same algorithm as _subsegment_from_tracks, but avoids
    any route_id resolution so callers can use dense route_int lookups.
    """
    if not stop_atcos or not tracks:
        return []
    # Map each stop -> list of candidate indices on track.
    # We keep multiple candidates to handle loop routes where the track passes
    # near the same stop multiple times.
    stop_to_candidates: dict[str, list[tuple[float, int]]] = {}
    for atco in stop_atcos:
        coord = None
        if walking_coords:
            coord = walking_coords.get(atco)
        if not coord:
            # can't map this stop
            continue
        # Accept either (lat, lon) tuples or [lat, lon] lists.
        try:
            lat_s, lon_s = float(coord[0]), float(coord[1])
        except Exception:
            continue
        # collect k nearest track points (k small for speed)
        dists: list[tuple[float, int]] = []
        for i, (tlat, tlon) in enumerate(tracks):
            d = _haversine(lat_s, lon_s, tlat, tlon)
            dists.append((d, i))
        dists.sort(key=lambda x: x[0])

        # Keep a few nearest candidates, but also drop extremely-far matches
        # (prevents random snapping when stop coords are wrong).
        # Threshold is generous; most good snaps are < 100m.
        max_keep = 8
        max_dist_m = 500.0
        keep = [(d, i) for (d, i) in dists[:max_keep] if d <= max_dist_m]
        if keep:
            stop_to_candidates[atco] = keep

    if not stop_to_candidates:
        return []

    # If only one stop resolved, we can't slice meaningfully.
    if len(stop_to_candidates) < 2:
        return []

    # Prefer using the first and last stop in the requested order (common case).
    resolved = [s for s in stop_atcos if s in stop_to_candidates]
    if len(resolved) < 2:
        return []

    a = resolved[0]
    b = resolved[-1]

    # Choose the best (ia, ib) using multiple candidates.
    # Score = distance_to_a + distance_to_b + lambda * slice_len.
    # This biases toward (1) close snaps and (2) shorter plausible segments.
    #
    # IMPORTANT: When slicing between two stops *in a known order*, we must
    # avoid picking a pair of indices that implies travelling backwards along
    # the polyline (ib < ia). On looped / self-crossing tracks, the same stop
    # can have multiple equally-close snap candidates. If we allow backwards
    # index pairs, we can produce short-circuit "teleport" geometry.
    best = None  # (score, ia, ib, da, db)
    best_rev = None  # best reverse pair (fallback only)
    lam = 0.15  # penalty per point of segment length
    for da, ia in stop_to_candidates.get(a, []):
        for db, ib in stop_to_candidates.get(b, []):
            seg_len = abs(ib - ia) + 1
            score = float(da) + float(db) + lam * float(seg_len)
            if ib >= ia:
                if best is None or score < best[0]:
                    best = (score, ia, ib, da, db)
            else:
                if best_rev is None or score < best_rev[0]:
                    best_rev = (score, ia, ib, da, db)

    if best is None and best_rev is not None:
        # No forward slice was possible with the available candidates. This can
        # happen when the polyline direction is opposite our stop order.
        # Keep the previous behaviour as a fallback.
        best = best_rev

    if best is None:
        return []

    # Optional trace to help diagnose "teleporting" geometry where a stop snaps
    # to the wrong part of a looped polyline.
    if os.environ.get('ROUTE_GEOM_TRACE') == '1':
        try:
            score, ia, ib, da, db = best
            print('[subsegment] pick', {
                'a': a,
                'b': b,
                'a_coord': (walking_coords.get(a) if walking_coords else None),
                'b_coord': (walking_coords.get(b) if walking_coords else None),
                'best_score': float(score),
                'ia': int(ia),
                'ib': int(ib),
                'da_m': float(da),
                'db_m': float(db),
                'seg_len_pts': int(abs(ib - ia) + 1),
                'a_candidates': [(float(d), int(i)) for (d, i) in stop_to_candidates.get(a, [])],
                'b_candidates': [(float(d), int(i)) for (d, i) in stop_to_candidates.get(b, [])],
            })
        except Exception:
            pass

    _, ia, ib, _, _ = best
    if ia <= ib:
        return tracks[ia:ib + 1]
    return list(reversed(tracks[ib:ia + 1]))


def _sample_coords_for_osrm(points, max_samples=20):
    """Return a list of 'lon,lat' strings sampled evenly from points."""
    if not points:
        return []
    n = len(points)
    if n <= max_samples:
        return [f"{p[1]},{p[0]}" for p in points]
    step = max(1, n // max_samples)
    sampled = [points[i] for i in range(0, n, step)]
    # ensure last point included
    if sampled[-1] != points[-1]:
        sampled.append(points[-1])
    return [f"{p[1]},{p[0]}" for p in sampled]



def geocode_locations(query: str, limit: int = 5, county: str = "Lancashire") -> List[Dict[str, Any]]:
    """Query Nominatim and return candidates (optionally county-biased).

    Results are restricted to ``countrycodes=gb`` and filtered so that
    only items whose address contains the county string are returned.
    The county is also appended to the Nominatim query to bias results
    toward the correct region (helps for brand / POI searches).

    A fuzzy-correction step maps common typos (e.g. "Lancster",
    "Blackpol") to known Lancashire place names before sending the
    query to Nominatim.
    """
    if not query or limit <= 0:
        return []

    # Handle comma-suffix heuristics: if the user typed "Morrisons,Morecambe"
    # we should treat the text after the comma as a town/city filter and
    # bias Nominatim toward that place. If it looks like a street/address
    # (contains numbers or a street token) we keep the whole query intact.
    main_q = query
    town_hint = None
    if "," in query:
        first, tail = query.split(",", 1)
        first = first.strip()
        tail = tail.strip()
        if tail and not _looks_like_street(tail):
            main_q = first
            town_hint = tail

    # Fuzzy-correct the main query and the town hint separately against
    # the known Lancashire places / POIs so typos like 'Morrisions'
    # or 'Lancster' are corrected before hitting Nominatim.
    corrected = _fuzzy_correct_query(main_q)
    if town_hint:
        town_corrected = _fuzzy_correct_query(town_hint)
    else:
        town_corrected = None

    # When a county is provided, always append it to the query so
    # Nominatim returns geographically relevant results. This works
    # well for place names ("Lancaster Lancashire") and brands alike
    # ("Sainsbury Lancashire"). We ask Nominatim for extra results and
    # then do a lenient post-filter to trim any outliers.
    # Build the effective Nominatim query. Prefer: "<corrected main> <town> <county>"
    effective_query = corrected
    if town_corrected:
        effective_query = f"{corrected} {town_corrected}"
    if county:
        # Only append if the user hasn't already included the county or town
        lower_eff = effective_query.lower()
        if county.lower() not in lower_eff and (not town_corrected or county.lower() not in town_corrected.lower()):
            effective_query = f"{effective_query} {county}"

    nominatim_limit = limit * 3 if county else limit  # over-fetch for filtering
    params = {
        "format": "json",
        "q": effective_query,
        "limit": str(nominatim_limit),
        "addressdetails": "1",
    }

    if county:
        # Prefer UK results when a UK county is requested
        params["countrycodes"] = "gb"

    url = f"https://nominatim.openstreetmap.org/search?{urlencode(params)}"
    # Use requests (which bundles certifi) so TLS verification works in
    # virtualenvs and containers. Do not disable verification.
    import requests
    headers = {"User-Agent": "transport-backend/1.0"}

    # Rate-limit access to Nominatim to 1 request per second. We acquire
    # a module-level lock and sleep as necessary before making the call.
    # The HTTP request is performed while holding the lock so concurrent
    # callers are serialized and spacing between requests is preserved.
    global _NOMINATIM_LAST_CALL
    with NOMINATIM_LOCK:
        now = time.monotonic()
        elapsed = now - _NOMINATIM_LAST_CALL
        wait = _NOMINATIM_MIN_INTERVAL - elapsed
        if wait > 0:
            time.sleep(wait)
        # mark last call timestamp immediately before the request
        _NOMINATIM_LAST_CALL = time.monotonic()
        resp = requests.get(url, headers=headers, timeout=5)
    resp.raise_for_status()
    payload = resp.json()

    # Build a list of candidates, then apply a lenient county filter.
    # If filtering yields no results, fall back to unfiltered GB results.
    raw_candidates = []
    for idx, item in enumerate(payload):
        try:
            lat = float(item.get("lat"))
            lon = float(item.get("lon"))
        except (TypeError, ValueError):
            continue

        name = item.get("display_name") or item.get("name") or query
        raw_candidates.append({
            "id": f"loc:{len(raw_candidates)}",
            "name": name,
            "lat": lat,
            "lon": lon,
            "atco_code": None,
            "type": "location",
        })

    def _county_match(cand: dict) -> bool:
        # Lenient county post-filter: check display_name. (Address fields
        # are not preserved in `raw_candidates`.)
        if not county:
            return True
        county_lc = county.lower()
        name_lc = (cand.get("name") or "").lower()
        return county_lc in name_lc

    filtered = [c for c in raw_candidates if _county_match(c)]
    chosen = filtered if filtered else raw_candidates
    return chosen[:limit]


@app.get("/search/stops")
async def search_stops(
    q: str = "",
    limit: int = 10,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    classification: Optional[str] = None,
):
    """Search stops by name (case-insensitive substring match).

    Query params:
        q:              search string (required for results)
        limit:          max results to return (default 10)
        classification: optional filter — one of hub, interchange,
                        local, request_stop.  Only stops matching the
                        class are returned (geocode locations are
                        excluded when this filter is active).

    Returns JSON list of {id, name, atco_code, lat, lon, type
    [, classification]}.
    """
    from fastapi.responses import JSONResponse

    # Helper: haversine distance (meters) for optional map-centered sorting
    def _haversine_m(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
        try:
            import math
            R = 6371000.0
            phi1 = math.radians(float(a_lat))
            phi2 = math.radians(float(b_lat))
            dphi = math.radians(float(b_lat) - float(a_lat))
            dl = math.radians(float(b_lon) - float(a_lon))
            x = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dl / 2.0) ** 2
            return 2.0 * R * math.asin(math.sqrt(x))
        except Exception:
            return float("inf")

    def _maybe_sort_by_center(stops: list) -> list:
        if lat is None or lon is None:
            return stops
        try:
            c_lat = float(lat)
            c_lon = float(lon)
        except Exception:
            return stops
        with_dist = []
        for s in stops or []:
            try:
                s_lat = s.get("lat")
                s_lon = s.get("lon")
                if s_lat is None or s_lon is None:
                    d = float("inf")
                else:
                    d = _haversine_m(c_lat, c_lon, float(s_lat), float(s_lon))
                with_dist.append((d, s))
            except Exception:
                with_dist.append((float("inf"), s))
        with_dist.sort(key=lambda t: t[0])
        return [s for _, s in with_dist]

    _VALID_CLASSES = {"hub", "interchange", "local", "request_stop"}
    if classification and classification not in _VALID_CLASSES:
        return JSONResponse(
            status_code=400,
            content={"error": f"Invalid classification '{classification}'. "
                     f"Must be one of: {', '.join(sorted(_VALID_CLASSES))}"},
        )

    if not q:
        return []

    if _base_cache is None:
        return JSONResponse(
            status_code=503,
            content={"error": "Backend not initialized"},
        )

    try:
        # Classification filter still uses stop DB
        if classification:
            loader = _base_cache["loader"]
            stop_results = loader.search_stops(q, limit * 3)
            for stop in stop_results:
                stop["type"] = "stop"

            # When a map center is provided, rank stop candidates by distance
            # before applying the classification filter/limit.
            stop_results = _maybe_sort_by_center(stop_results)

            lookup = _get_classification_lookup()
            filtered = []
            for stop in stop_results:
                atco = stop.get("atco_code")
                cls = _resolve_stop_classification(atco, lookup)
                if cls == classification:
                    stop["classification"] = cls
                    filtered.append(stop)
                if len(filtered) >= limit:
                    break
            return filtered

        # Default: return a merged list of NaPTAN stops (from loader) and
        # geocoded POIs so the frontend can prompt both types. We prefer
        # stop DB results first, then append geocoded locations.
        location_results = []
        stop_results = []
        try:
            # Prefer ATCO/NaPTAN stop metadata (stop_coords table) when
            # available — this ensures canonical stop names/types are used
            # instead of the bus-specific stop names table.
            atco_loader = _base_cache.get("atco_loader") if _base_cache else None
            # Ensure the API always surfaces a small number of canonical
            # stop-type results first (best UX for autocomplete). We fetch
            # up to STOP_FIRST results from the stop_coords table and then
            # append geocoded locations; the final combined list is sliced
            # to the caller's `limit`.
            # Make STOP_FIRST configurable via env var for easy tuning.
            try:
                STOP_FIRST = int(os.environ.get("STOP_FIRST", "5"))
            except Exception:
                STOP_FIRST = 5
            # Request STOP_FIRST candidates from the DB regardless of the
            # caller's `limit` so we can choose the best stop-type
            # suggestions before appending location matches. We'll still
            # respect the requested `limit` when returning the final list.
            stop_first_limit = STOP_FIRST
            if atco_loader:
                try:
                    # Use the atco_loader's DB to search stop_coords by name
                    conn = atco_loader._connect()
                    # For read-only SELECT queries prefer autocommit so that
                    # a single failing statement does not leave the
                    # connection in an aborted transaction state which would
                    # break subsequent reads if the connection were reused.
                    try:
                        conn.autocommit = True
                    except Exception:
                        # Some driver wrappers may not expose autocommit; ignore
                        pass
                    cur = conn.cursor()
                    # Match by name, town, or the combined display (name, town)
                    # so queries that include a town (e.g. "George Street Lancaster")
                    # will match the intended stop.
                    # Normalize the incoming query and form a name_guess so
                    # punctuation and commas won't prevent matches. We also
                    # use a Postgres-side regexp_replace on the stored name
                    # + town when comparing to allow robust matching.
                    import re as _re
                    def _norm(s: str) -> str:
                        if not s:
                            return ""
                        # Remove punctuation, collapse spaces and lowercase
                        t = _re.sub(r"[^0-9a-zA-Z ]+", " ", s)
                        t = _re.sub(r"\s+", " ", t).strip().lower()
                        # Canonicalise common street tokens so variations like
                        # 'Street' vs 'St' or 'Road' vs 'Rd' match consistently
                        token_map = {
                            'street': 'st', 'st': 'st',
                            'road': 'rd', 'rd': 'rd',
                            'avenue': 'ave', 'ave': 'ave',
                            'lane': 'ln', 'ln': 'ln',
                            'drive': 'dr', 'dr': 'dr',
                            'boulevard': 'blvd', 'blvd': 'blvd',
                            'court': 'ct', 'ct': 'ct',
                            'crescent': 'cres', 'cres': 'cres',
                        }
                        parts = [p for p in t.split(" ") if p]
                        norm_parts = []
                        for p in parts:
                            if p in token_map:
                                norm_parts.append(token_map[p])
                            else:
                                # Strip trailing dots (e.g. 'St.' → 'st')
                                p2 = p.rstrip('.')
                                if p2 in token_map:
                                    norm_parts.append(token_map[p2])
                                else:
                                    norm_parts.append(p)
                        return " ".join(norm_parts)

                    norm_q = _norm(q)
                    if " " in q:
                        # If the final token looks like a street (e.g. "Street", "St",
                        # "Road") then don't treat it as a town — keep the full
                        # query as the name guess. This prevents queries like
                        # "Abingdon Street" being split into name="Abingdon",
                        # town="Street" which would incorrectly bias town
                        # matching.
                        tail = q.rsplit(" ", 1)[1].strip()
                        try:
                            if _looks_like_street(tail):
                                name_guess_raw = q
                            else:
                                name_guess_raw = q.rsplit(" ", 1)[0].strip()
                        except Exception:
                            name_guess_raw = q.rsplit(" ", 1)[0].strip()
                    else:
                        name_guess_raw = q
                    norm_name_guess = _norm(name_guess_raw)

                    # If the query looks like "<name> <town>", try an
                    # explicit name+town pre-query first so stops in that
                    # town are guaranteed to be included in the candidate
                    # pool (helps queries like "George Street Lancaster").
                    pre_rows = []
                    if " " in q:
                        parts = q.rsplit(" ", 1)
                        name_guess = parts[0].strip()
                        town_guess = parts[1].strip()
                        # Only treat the trailing token as a town if it
                        # doesn't look like a street/address (e.g. 'St',
                        # 'Street', 'Road'). If it looks like a street then
                        # skip town-specific matching.
                        if not name_guess or not town_guess or _looks_like_street(town_guess):
                            name_guess = None
                            town_guess = None
                        else:
                            try:
                                cur.execute(
                                    "SELECT atco_code, name, town, lat, lon FROM stop_coords "
                                    "WHERE LOWER(name) LIKE LOWER(%s) AND LOWER(town) LIKE LOWER(%s) LIMIT %s",
                                    (f"%{name_guess}%", f"%{town_guess}%", stop_first_limit),
                                )
                                pre_rows = cur.fetchall()
                            except Exception:
                                pre_rows = []

                    # Determine name/town guesses for relevance scoring
                    if " " in q:
                        _parts = q.rsplit(" ", 1)
                        _name_g = _parts[0].strip()
                        _town_g = _parts[1].strip()
                    else:
                        _name_g = q
                        _town_g = ""

                    # Primary query: use regexp_replace for punctuation-
                    # insensitive matching, ORDER BY relevance when pg_trgm
                    # is available (exactness bonus + trigram similarity).
                    try:
                        cur.execute(
                            "SELECT atco_code, name, town, lat, lon, "
                            "  CASE "
                            "    WHEN LOWER(name) LIKE LOWER(%s) AND LOWER(COALESCE(town,'')) LIKE LOWER(%s) THEN 10 "
                            "    WHEN LOWER(name) LIKE LOWER(%s) THEN 5 "
                            "    ELSE 0 "
                            "  END "
                            "  + similarity(LOWER(COALESCE(name,'')), LOWER(%s)) "
                            "  + similarity(LOWER(COALESCE(town,'')), LOWER(%s)) AS score "
                            "FROM stop_coords "
                            "WHERE regexp_replace(LOWER(COALESCE(name,'') || ' ' || COALESCE(town,'')), '[^a-z0-9 ]', '', 'g') LIKE %s "
                            "   OR regexp_replace(LOWER(COALESCE(name,'')), '[^a-z0-9 ]', '', 'g') LIKE %s "
                            "ORDER BY score DESC "
                            "LIMIT %s",
                            (
                                f"%{_name_g}%", f"%{_town_g}%" if _town_g else "%",
                                f"%{_name_g}%",
                                _name_g, _town_g if _town_g else q,
                                f"%{norm_q}%", f"%{norm_name_guess}%",
                                stop_first_limit,
                            ),
                        )
                        rows = [(r[0], r[1], r[2], r[3], r[4]) for r in cur.fetchall()]
                    except Exception:
                        # pg_trgm not available — fall back to regexp_replace
                        # with a manual exactness ORDER BY (no similarity).
                        try:
                            cur.execute(
                                "SELECT atco_code, name, town, lat, lon, "
                                "  CASE "
                                "    WHEN LOWER(name) LIKE LOWER(%s) AND LOWER(COALESCE(town,'')) LIKE LOWER(%s) THEN 10 "
                                "    WHEN LOWER(name) LIKE LOWER(%s) THEN 5 "
                                "    ELSE 0 "
                                "  END AS score "
                                "FROM stop_coords "
                                "WHERE regexp_replace(LOWER(COALESCE(name,'') || ' ' || COALESCE(town,'')), '[^a-z0-9 ]', '', 'g') LIKE %s "
                                "   OR regexp_replace(LOWER(COALESCE(name,'')), '[^a-z0-9 ]', '', 'g') LIKE %s "
                                "ORDER BY score DESC "
                                "LIMIT %s",
                                (
                                    f"%{_name_g}%", f"%{_town_g}%" if _town_g else "%",
                                    f"%{_name_g}%",
                                    f"%{norm_q}%", f"%{norm_name_guess}%",
                                    stop_first_limit,
                                ),
                            )
                            rows = [(r[0], r[1], r[2], r[3], r[4]) for r in cur.fetchall()]
                        except Exception:
                            # Final fallback: simple LIKE, no ordering
                            cur.execute(
                                "SELECT atco_code, name, town, lat, lon FROM stop_coords "
                                "WHERE LOWER(name) LIKE LOWER(%s) OR LOWER(name) LIKE LOWER(%s) LIMIT %s",
                                (f"%{q}%", f"%{name_guess_raw}%", stop_first_limit),
                            )
                            rows = cur.fetchall()

                    # Prepend any explicit pre_rows (unique) so they are
                    # considered first when selecting stop-type results.
                    if pre_rows:
                        seen_atco = {r[0] for r in pre_rows}
                        rows = list(pre_rows) + [r for r in rows if r[0] not in seen_atco]

                    # Always try to find town-specific stops when the
                    # query contains a space (e.g. "George Street Lancaster"
                    # or even "George Street Lancaseter"). This runs
                    # regardless of how many LIKE rows we already have, and
                    # *prepends* town-matching stops so they rank first.
                    if " " in q:
                        parts = q.rsplit(" ", 1)
                        name_guess = parts[0].strip()
                        town_guess = parts[1].strip()
                        if name_guess and town_guess:
                            town_rows = []
                            # 1) Exact name+town LIKE
                            try:
                                cur.execute(
                                    "SELECT atco_code, name, town, lat, lon FROM stop_coords "
                                    "WHERE LOWER(name) LIKE LOWER(%s) AND LOWER(town) LIKE LOWER(%s) LIMIT %s",
                                    (f"%{name_guess}%", f"%{town_guess}%", stop_first_limit),
                                )
                                town_rows = cur.fetchall()
                            except Exception:
                                pass
                            # 2) Trigram fuzzy name+town (handles typos)
                            if len(town_rows) < 2:
                                try:
                                    cur.execute(
                                        "SELECT atco_code, name, town, lat, lon, "
                                        "similarity(LOWER(name), LOWER(%s)) + "
                                        "similarity(LOWER(COALESCE(town,'')), LOWER(%s)) AS score "
                                        "FROM stop_coords "
                                        "WHERE similarity(LOWER(name), LOWER(%s)) > 0.2 "
                                        "AND similarity(LOWER(COALESCE(town,'')), LOWER(%s)) > 0.2 "
                                        "ORDER BY score DESC LIMIT %s",
                                        (name_guess, town_guess, name_guess, town_guess, stop_first_limit),
                                    )
                                    more = cur.fetchall()
                                    seen_t = {r[0] for r in town_rows}
                                    for r in more:
                                        if r[0] not in seen_t:
                                            town_rows.append((r[0], r[1], r[2], r[3], r[4]))
                                except Exception:
                                    # 3) Python difflib fallback for town matching
                                    try:
                                        from difflib import SequenceMatcher
                                        cur.execute(
                                            "SELECT atco_code, name, town, lat, lon FROM stop_coords "
                                            "WHERE LOWER(name) LIKE LOWER(%s)",
                                            (f"%{name_guess}%",),
                                        )
                                        candidates = cur.fetchall()
                                        scored = []
                                        for atco_c, nm, tn, la, lo in candidates:
                                            if not tn:
                                                continue
                                            ratio = SequenceMatcher(None, town_guess.lower(), tn.lower()).ratio()
                                            if ratio > 0.5:
                                                scored.append((ratio, atco_c, nm, tn, la, lo))
                                        scored.sort(key=lambda t: t[0], reverse=True)
                                        seen_t = {r[0] for r in town_rows}
                                        for _, atco_c, nm, tn, la, lo in scored[:stop_first_limit]:
                                            if atco_c not in seen_t:
                                                town_rows.append((atco_c, nm, tn, la, lo))
                                    except Exception:
                                        pass
                            # Prepend town-matching rows ahead of generic rows
                            if town_rows:
                                seen_town_atcos = {r[0] for r in town_rows}
                                rows = list(town_rows) + [r for r in rows if r[0] not in seen_town_atcos]
                    conn.close()
                    stop_results = []
                    for i, (atco, name, town, lat, lon) in enumerate(rows):
                        display = name or atco
                        if town:
                            display = f"{display}, {town}"
                        stop_results.append({
                            "id": i,
                            "name": name or atco,
                            "display_name": display,
                            "atco_code": atco,
                            "lat": lat,
                            "lon": lon,
                            "type": "stop",
                        })
                except Exception as exc:
                    logger.warning("ATCO stop lookup failed: %s", exc)
                # If ATCO/NaPTAN lookup returned no results, fall back to
                # the legacy bus_stop_names index so queries like "Abingdon
                # Street" that exist only in the bus index are still
                # surfaced to callers.
                if atco_loader and not stop_results:
                    try:
                        loader = _base_cache.get("loader") if _base_cache else None
                        if loader:
                            temp = loader.search_stops(q, stop_first_limit)
                            stop_results = temp[:stop_first_limit]
                            for stop in stop_results:
                                stop["type"] = "stop"
                    except Exception:
                        # non-fatal — we'll fall back to geocoding later
                        pass
            else:
                # Fallback to legacy loader when ATCO loader unavailable
                loader = _base_cache.get("loader") if _base_cache else None
                if loader:
                    # Ask the legacy loader for candidates but only keep
                    # up to `stop_first_limit` so we continue to guarantee
                    # a small set of stop-type results first.
                    # Ask the legacy loader for STOP_FIRST candidates so we
                    # have a larger pool to pick from (don't cap by request
                    # `limit` here).
                    temp = loader.search_stops(q, stop_first_limit)
                    stop_results = temp[:stop_first_limit]
                    for stop in stop_results:
                        stop["type"] = "stop"
        except Exception as exc:
            logger.warning("Stop DB lookup failed: %s", exc)

        # Critical UX: if the caller supplied a map center, re-rank the stop
        # candidate list by distance BEFORE we do any truncation/slicing for
        # the combined results.
        try:
            stop_results = _maybe_sort_by_center(stop_results)
        except Exception:
            pass

        try:
            # Geocoding is blocking (network + rate-limited); run in a thread
            location_results = await asyncio.to_thread(geocode_locations, q, limit)
        except Exception as exc:
            logger.warning("Geocoding lookup failed: %s", exc)
            location_results = []

        # If we found neither stop DB results nor geocoded locations, try
        # a global geocode lookup (no Lancashire county filter). This helps
        # queries for streets or places outside Lancashire (e.g. "Abingdon
        # Street") where the default county bias would filter out results.
        if not (stop_results or location_results):
            try:
                location_results = await asyncio.to_thread(geocode_locations, q, limit, None)
            except Exception:
                location_results = []

        # Backfill missing coordinates for stop_results when possible.
        # First try the ATCO/NaPTAN `stop_coords` table via atco_loader.
        try:
            missing_atcos = [s.get("atco_code") for s in (stop_results or []) if s.get("atco_code") and (s.get("lat") is None or s.get("lon") is None)]
            if missing_atcos:
                atco_loader = _base_cache.get("atco_loader") if _base_cache else None
                if atco_loader:
                    try:
                        conn = atco_loader._connect()
                        try:
                            conn.autocommit = True
                        except Exception:
                            pass
                        cur = conn.cursor()
                        # Query only the atcos we need
                        placeholders = ','.join(['%s'] * len(missing_atcos))
                        cur.execute(f"SELECT atco_code, lat, lon FROM stop_coords WHERE atco_code IN ({placeholders})", tuple(missing_atcos))
                        rows = cur.fetchall()
                        conn.close()
                        lookup = {r[0]: (r[1], r[2]) for r in rows}
                        for stop in (stop_results or []):
                            atco = stop.get('atco_code')
                            if atco and (stop.get('lat') is None or stop.get('lon') is None):
                                coords = lookup.get(atco)
                                if coords:
                                    stop['lat'], stop['lon'] = coords[0], coords[1]
                    except Exception:
                        # non-fatal — continue to other fallbacks
                        logger.debug('ATCO coords lookup failed for missing atcos')

            # Secondary fallback: for any still-missing coords, consult Nominatim
            # on a per-stop basis but cap the number of external requests to avoid rate-limits.
            STILL_MISSING = [s for s in (stop_results or []) if s.get('atco_code') and (s.get('lat') is None or s.get('lon') is None)]
            NOMINATIM_FALLBACK_CAP = 5
            fallback_calls = 0
            for stop in STILL_MISSING:
                if fallback_calls >= NOMINATIM_FALLBACK_CAP:
                    break
                name = stop.get('name') or ''
                display = stop.get('display_name') or ''
                # Use display name or name as the geocode query, prefer including town if present
                query_text = display or name
                try:
                    candidates = await asyncio.to_thread(geocode_locations, query_text, 1)
                    if candidates:
                        c = candidates[0]
                        stop['lat'] = c.get('lat')
                        stop['lon'] = c.get('lon')
                        fallback_calls += 1
                except Exception:
                    # ignore and continue
                    logger.debug('Per-stop geocode fallback failed for %s', query_text)
                    continue
        except Exception as exc:
            logger.debug('Backfilling stop coords failed: %s', exc)

        # Basic de-duplication by lower-cased name to avoid duplicates
        combined = []
        seen = set()
        for item in (stop_results or []):
            name = (item.get("name") or "").strip().lower()
            if name in seen:
                continue
            seen.add(name)
            combined.append(item)
        for item in (location_results or []):
            name = (item.get("name") or "").strip().lower()
            if name in seen:
                continue
            seen.add(name)
            combined.append(item)

        # Respect requested limit: return up to `limit` items. The
        # frontend further slices stop-type suggestions to 3, so this
        # keeps responses compact while ensuring both types are present.
        return combined[:limit]
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"error": str(exc)},
        )


# ── Station classification helpers ───────────────────────────────────
_classification_cache: Optional[Dict[str, str]] = None

# Cache mapping for per-merged-object lookup: { id(merged) : {stop_index: classification} }
_classification_by_merged: Dict[int, Dict[int, str]] = {}


def _get_classification_lookup() -> Dict[str, str]:
    """Return {atco_code: classification} for all stops in today's network.

    Lazily computed on first call; cached for the process lifetime.
    Uses today's MergedData (via the first router cache entry, or builds
    one for today's date if none exists yet).
    """
    global _classification_cache
    if _classification_cache is not None:
        return _classification_cache

    date_str = datetime.now().strftime("%Y-%m-%d")
    try:
        merged, _router, _walking = get_router_for_date(date_str)
    except Exception:
        return {}

    idx_lookup = classify_to_lookup(merged)

    # Convert stop-int → ATCO code using MergedData.get_atco_code
    atco_lookup: Dict[str, str] = {}
    for stop_int, cls in idx_lookup.items():
        code = merged.get_atco_code(stop_int)
        if code:
            atco_lookup[code] = cls

    _classification_cache = atco_lookup
    return _classification_cache


def _classification_for_merged(merged: Any) -> Dict[int, str]:
    """Return stop-index -> classification for a MergedData instance.

    Strategy:
      1. If we have a cached mapping for this merged object id, return it.
      2. Otherwise, try to reuse the ATCO-code based process-wide cache
         from `_get_classification_lookup()` by mapping ATCO -> stop-index
         for this merged and building the stop-index keyed lookup.
      3. If that fails, compute `classify_to_lookup(merged)`, cache it
         under the merged id, and return it.
    """
    if merged is None:
        return {}
    m_id = id(merged)
    # Fast path: per-merged cache
    if m_id in _classification_by_merged:
        return _classification_by_merged[m_id]

    # Try to reuse ATCO -> class process cache to avoid recomputing
    try:
        atco_map = _get_classification_lookup() or {}
        if atco_map:
            # Build stop-index -> class mapping by asking merged for each stop's ATCO
            lookup: Dict[int, str] = {}
            total = getattr(merged, 'stop_metadata', None)
            # If merged exposes stop count via stop_metadata use that, else try stop_to_routes length
            if isinstance(total, list):
                count = len(total)
            else:
                count = len(getattr(merged, 'stop_to_routes', []) or [])
            for si in range(count):
                try:
                    atco = merged.get_atco_code(si)
                except Exception:
                    atco = None
                cls = atco_map.get(atco) if atco else None
                if cls:
                    lookup[si] = cls
            # Only use this mapping if it covers at least one stop; otherwise fallthrough
            if lookup:
                _classification_by_merged[m_id] = lookup
                return lookup
    except Exception:
        # Fall back to computing directly
        pass

    # Last resort: compute from scratch and cache
    try:
        idx_lookup = classify_to_lookup(merged)
        _classification_by_merged[m_id] = idx_lookup
        return idx_lookup
    except Exception:
        return {}


def _resolve_stop_classification(
    atco_code: Optional[str],
    lookup: Dict[str, str],
) -> str:
    """Return the classification for a stop, defaulting to request_stop."""
    if not atco_code:
        return "request_stop"
    return lookup.get(atco_code, "request_stop")


@app.get("/stops/classify")
async def stops_classify(classification: Optional[str] = None):
    """Return station classification data for all stops.

    Query params:
        classification: optional filter — if provided, only stops of
                        that class are returned.

    Returns JSON list of {stop_index, name, degree, frequency,
    interchange, lines, classification}.
    """
    from fastapi.responses import JSONResponse

    _VALID_CLASSES = {"hub", "interchange", "local", "request_stop"}
    if classification and classification not in _VALID_CLASSES:
        return JSONResponse(
            status_code=400,
            content={"error": f"Invalid classification '{classification}'. "
                     f"Must be one of: {', '.join(sorted(_VALID_CLASSES))}"},
        )

    date_str = datetime.now().strftime("%Y-%m-%d")
    try:
        merged, _router, _walking = get_router_for_date(date_str)
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"error": "Backend not initialized"},
        )

    results = classify_all(merged)

    if classification:
        results = [r for r in results if r["classification"] == classification]

    return results


# ── Geo-enriched classified stops ────────────────────────────────────
# Cache the combined result so repeated requests are instant.
_stops_geo_cache: Optional[List[Dict[str, Any]]] = None


@app.get("/stops/geo")
async def stops_geo(
    classification: Optional[str] = None,
    bbox: Optional[str] = None,
):
    """Return classified bus stops with lat/lon coordinates.

    Merges ``/stops/classify`` data with NaPTAN coordinates so every
    stop has ``lat``, ``lon``, ``name``, ``classification``, ``lines``,
    and ``atco_code``.

    Query params:
        classification: optional filter (hub | interchange | local | request_stop)
        bbox:           optional viewport filter as ``south,west,north,east``

    Response: JSON list of
        ``{id, name, lat, lon, atco_code, classification, lines,
           degree, frequency}``.
    """
    from fastapi.responses import JSONResponse

    _VALID_CLASSES = {"hub", "interchange", "local", "request_stop"}
    if classification and classification not in _VALID_CLASSES:
        return JSONResponse(
            status_code=400,
            content={"error": f"Invalid classification '{classification}'. "
                     f"Must be one of: {', '.join(sorted(_VALID_CLASSES))}"},
        )

    # Parse optional bounding box
    south = west = north = east = None
    if bbox:
        try:
            parts = [float(x) for x in bbox.split(",")]
            if len(parts) != 4:
                raise ValueError("need 4 values")
            south, west, north, east = parts
        except (ValueError, TypeError):
            return JSONResponse(
                status_code=400,
                content={"error": "bbox must be 4 comma-separated floats: south,west,north,east"},
            )

    # Build (or reuse) the full enriched list
    global _stops_geo_cache
    if _stops_geo_cache is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
        try:
            merged, _router, _walking = get_router_for_date(date_str)
        except Exception:
            return JSONResponse(
                status_code=503,
                content={"error": "Backend not initialized"},
            )

        # Get NaPTAN coords: {atco_code: (lat, lon)}
        atco = _base_cache.get("atco_loader") if _base_cache else None
        coord_map: Dict[str, tuple] = {}
        if atco:
            try:
                coord_map = atco.get_all_stop_coords()
            except Exception as exc:
                logger.warning("Failed to load stop coords: %s", exc)

        # Classify every stop in the merged network
        all_classified = classify_all(merged)

        enriched: List[Dict[str, Any]] = []
        for stop in all_classified:
            atco_code = merged.get_atco_code(stop["stop_index"])
            if not atco_code:
                continue
            coords = coord_map.get(atco_code)
            if not coords:
                continue
            lat, lon = coords
            enriched.append({
                "id": atco_code,
                "name": stop["name"],
                "lat": lat,
                "lon": lon,
                "atco_code": atco_code,
                "classification": stop["classification"],
                "lines": sorted({
                    ln.split(":")[-1] for ln in stop["lines"]
                    if ln
                }),
                "degree": stop["degree"],
                "frequency": stop["frequency"],
            })

        _stops_geo_cache = enriched

    # Apply filters on the cached list
    results = _stops_geo_cache
    if classification:
        results = [s for s in results if s["classification"] == classification]
    if south is not None:
        results = [
            s for s in results
            if south <= s["lat"] <= north and west <= s["lon"] <= east
        ]

    return results


# ── Route line stops for a given line name ───────────────────────────
# Returns the ordered list of stops (with lat/lon) for every route
# variant that matches the requested line name.  Used by the frontend
# to draw polylines on the map when a user clicks a line chip.

_route_line_cache: Dict[str, Any] = {}
_route_label_cache: Dict[str, Any] = {}


def _geo_cache_suffix(lat: Optional[float], lon: Optional[float], bucket_deg: float = 0.25) -> str:
    """Stable cache suffix for optional geo point.

    Bucketed to avoid unbounded cache growth while preventing global
    collisions for short line names like "1".
    """
    try:
        if lat is None or lon is None:
            return ""
        blat = int(float(lat) / bucket_deg)
        blon = int(float(lon) / bucket_deg)
        return f"@{bucket_deg}:{blat}:{blon}"
    except Exception:
        return ""


def _filter_variants_near_point(
    variants: List[Dict[str, Any]],
    lat: Optional[float],
    lon: Optional[float],
    max_km: Optional[float] = None,
) -> tuple[List[Dict[str, Any]], bool]:
    """Filter route variants to those with at least one stop near (lat, lon).

    Guardrail against line-name collisions across national datasets.
    When nothing is near the point, we return an empty list and geo_ok=False.
    Callers can decide whether to fall back (non-strict) or 404 (strict_geo).
    """
    if lat is None or lon is None:
        return variants, True
    try:
        max_km_f = float(max_km) if max_km is not None else float(os.environ.get('ROUTE_LABEL_MAX_KM', '30'))
    except Exception:
        max_km_f = 30.0

    try:
        import math
        cos_lat = math.cos(math.radians(float(lat)))
    except Exception:
        cos_lat = 1.0

    max_lat_deg = max_km_f / 111.32
    max_lon_deg = max_km_f / (111.32 * max(cos_lat, 0.2))

    out: List[Dict[str, Any]] = []
    for v in variants:
        if not isinstance(v, dict):
            continue
        stops = v.get('stops')
        if not isinstance(stops, list) or not stops:
            continue
        keep = False
        for s in stops:
            if not isinstance(s, dict):
                continue
            slat = s.get('lat')
            slon = s.get('lon')
            if slat is None or slon is None:
                continue
            try:
                dlat = abs(float(slat) - float(lat))
                dlon = abs(float(slon) - float(lon))
            except Exception:
                continue
            if dlat <= max_lat_deg and dlon <= max_lon_deg:
                keep = True
                break
        if keep:
            out.append(v)

    if out:
        return out, True
    # Nothing near the point. IMPORTANT: do NOT fall back to the original
    # (possibly far-away) variants here; strict_geo callers rely on an empty
    # result to avoid wrong-city line-name collisions.
    return [], False


def _stitch_route_variant_from_link_tracks(
    merged,
    route_int: int,
    atcos: List[str],
    *,
    allow_partial: bool = True,
) -> Optional[List[List[float]]]:
    """Best-effort stitch for a route variant from route_link_tracks.

    Why this helper exists:
    - Some routes legitimately repeat the same ATCO code in a loop.
    - Some providers publish stop order and section-link fragments that are
      almost aligned but miss one terminal adjacency.

    We therefore:
    1) map requested ATCOs onto route_stops in forward order (duplicate-aware),
    2) stitch direct fragment edges,
    3) when a later edge is missing and ``allow_partial`` is true, keep the
       stitched prefix instead of discarding everything.
    """
    try:
        if not isinstance(route_int, int):
            return None
        if not isinstance(atcos, list) or len(atcos) < 2:
            return None

        # Access fragment map
        try:
            if hasattr(merged, 'get_route_link_tracks'):
                link_map = merged.get_route_link_tracks(route_int)
            else:
                links = getattr(merged, 'route_link_tracks', None)
                link_map = links[route_int] if (links and route_int < len(links)) else None
        except Exception:
            link_map = None
        if not link_map or not isinstance(link_map, dict):
            return None

        route_stops = merged.route_stops[route_int] if route_int < len(getattr(merged, 'route_stops', []) or []) else []
        if not route_stops or len(route_stops) < 2:
            return None

        # Build ATCO -> ordered list of positions in route_stops (not first-only).
        atco_positions: Dict[str, List[int]] = {}
        for pos, s_int in enumerate(route_stops):
            try:
                c = merged.get_atco_code(s_int)
            except Exception:
                c = None
            if c:
                atco_positions.setdefault(str(c).strip(), []).append(pos)

        # Resolve requested ATCO sequence to concrete stop_int sequence in route order.
        stop_ints: List[int] = []
        cursor = -1
        for a in atcos:
            key = str(a).strip()
            pos_list = atco_positions.get(key) or []
            if not pos_list:
                continue
            chosen_pos = None
            for p in pos_list:
                if p > cursor:
                    chosen_pos = p
                    break
            if chosen_pos is None:
                # Wrap once for looping routes.
                chosen_pos = pos_list[0]
            stop_ints.append(route_stops[chosen_pos])
            cursor = chosen_pos

        if len(stop_ints) < 2:
            return None

        stitched_pts: List[Tuple[float, float]] = []
        stitched_edges = 0
        for a, b in zip(stop_ints, stop_ints[1:]):
            seg = link_map.get((a, b))
            if not seg:
                rev = link_map.get((b, a))
                if rev:
                    seg = list(reversed(rev))
            if not seg:
                if allow_partial and stitched_edges > 0:
                    break
                return None
            if stitched_pts and seg and stitched_pts[-1] == seg[0]:
                stitched_pts.extend(seg[1:])
            else:
                stitched_pts.extend(seg)
            stitched_edges += 1

        coords = _normalize_latlon_coords(stitched_pts)
        return coords if len(coords) >= 2 else None
    except Exception:
        return None


def _resolve_route_stop_occurrences(
    merged,
    route_stops: List[int],
    from_atco: Optional[str],
    to_atco: Optional[str],
) -> Dict[str, object]:
    """Resolve ATCO endpoints onto duplicate-aware positions in route_stops."""
    out: Dict[str, object] = {
        'atco_positions': {},
        'from_stop_int': None,
        'to_stop_int': None,
        'from_pos': None,
        'to_pos': None,
    }
    try:
        if not route_stops:
            return out

        atco_positions: Dict[str, List[int]] = {}
        for pos, s_int in enumerate(route_stops or []):
            try:
                c = merged.get_atco_code(s_int)
            except Exception:
                c = None
            if c:
                atco_positions.setdefault(str(c).strip(), []).append(pos)

        out['atco_positions'] = atco_positions

        fk = str(from_atco).strip() if from_atco is not None else None
        tk = str(to_atco).strip() if to_atco is not None else None
        fpos = atco_positions.get(fk) if fk else []
        tpos = atco_positions.get(tk) if tk else []

        if fpos and tpos:
            same_key = bool(fk and tk and fk == tk)
            best = None
            for i in fpos:
                j = next((p for p in tpos if p > i), None) if same_key else next((p for p in tpos if p >= i), None)
                if j is None:
                    if same_key:
                        continue
                    j = tpos[-1]
                cand = (0 if j >= i else 1, abs(j - i), i, j)
                if best is None or cand < best:
                    best = cand
            if best is not None:
                i, j = best[2], best[3]
                out['from_pos'] = i
                out['to_pos'] = j
                out['from_stop_int'] = route_stops[i]
                out['to_stop_int'] = route_stops[j]
                return out

        if fpos:
            i = fpos[0]
            out['from_pos'] = i
            out['from_stop_int'] = route_stops[i]
        if tpos:
            j = tpos[0]
            out['to_pos'] = j
            out['to_stop_int'] = route_stops[j]
        return out
    except Exception:
        return out


def _build_stop_coord_maps(walking=None) -> tuple[Dict[str, tuple], Dict[int, tuple]]:
    """Return coordinate maps keyed by ATCO code and by merged stop index.

    Primary source is the NaPTAN-backed ``atco_loader`` map. When that source
    is unavailable or incomplete, the walking graph's stop-int coordinate map
    provides a local fallback for rendering ordered route variants.
    """
    atco_coord_map: Dict[str, tuple] = {}
    stop_int_coord_map: Dict[int, tuple] = {}

    # Primary map: ATCO -> (lat, lon)
    try:
        atco_loader = _base_cache.get("atco_loader") if _base_cache else None
        if atco_loader:
            raw = atco_loader.get_all_stop_coords() or {}
            if isinstance(raw, dict):
                atco_coord_map = raw
    except Exception:
        atco_coord_map = {}

    # Fallback map: stop_int -> (lat, lon) from walking graph
    try:
        raw_walking_coords = getattr(walking, "_coords", None)
        if isinstance(raw_walking_coords, dict):
            for k, v in raw_walking_coords.items():
                try:
                    if not isinstance(v, (tuple, list)) or len(v) < 2:
                        continue
                    stop_int = int(k)
                    lat0 = float(v[0])
                    lon0 = float(v[1])
                    stop_int_coord_map[stop_int] = (lat0, lon0)
                except Exception:
                    continue
    except Exception:
        stop_int_coord_map = {}

    return atco_coord_map, stop_int_coord_map


def _coords_for_route_stop(
    merged,
    stop_int: int,
    atco_coord_map: Dict[str, tuple],
    stop_int_coord_map: Dict[int, tuple],
) -> tuple[Optional[str], Optional[tuple[float, float]]]:
    """Resolve (atco_code, coords) for a merged stop index.

    Preference order:
    1) NaPTAN ATCO coordinate map (stable external code)
    2) Walking graph stop-int coordinates (local fallback)
    """
    try:
        atco_code = merged.get_atco_code(stop_int)
    except Exception:
        atco_code = None
    if not atco_code:
        return None, None

    coords = None
    try:
        coords = atco_coord_map.get(atco_code)
    except Exception:
        coords = None

    if not coords:
        try:
            coords = stop_int_coord_map.get(int(stop_int))
        except Exception:
            coords = None

    if not coords:
        return str(atco_code), None

    try:
        return str(atco_code), (float(coords[0]), float(coords[1]))
    except Exception:
        return str(atco_code), None


@app.get("/routes/label/{line}")
async def route_label(
    line: str,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    strict_geo: int = 0,
):
    """Return a compact label/metadata payload for a route line.

    The frontend uses this for lightweight display when a user clicks a live
    vehicle (map view). It is intentionally small and stable:

    Response::

        {
          "line": "100",
          "variant_count": 3,
          "route_ids": ["...", "..."],
        }

    Returns 404 when the backend has no label data for that line.
    """
    from fastapi.responses import JSONResponse

    line_key = (line or '').strip().upper()
    if not line_key:
        return JSONResponse(status_code=404, content={"detail": "Not Found"})

    cache_key = f"{line_key}{_geo_cache_suffix(lat, lon)}"
    if cache_key in _route_label_cache:
        return _route_label_cache[cache_key]

    # Prefer reusing the /routes/line cache; compute if needed.
    try:
        data = _route_line_cache.get(cache_key) or _route_line_cache.get(line_key)
        if not data:
            data = await routes_for_line(line_key, lat=lat, lon=lon)
    except Exception:
        return JSONResponse(status_code=404, content={"detail": "Not Found"})

    # If routes_for_line returned an error response, treat as not found.
    if not isinstance(data, dict):
        return JSONResponse(status_code=404, content={"detail": "Not Found"})

    variants = data.get('variants') if isinstance(data, dict) else None
    if not isinstance(variants, list) or len(variants) == 0:
        return JSONResponse(status_code=404, content={"detail": "Not Found"})

    # Filter variants by proximity when a reference point is supplied.
    geo_ok = True
    try:
        variants, geo_ok = _filter_variants_near_point(variants, lat, lon)
    except Exception:
        geo_ok = True
    if strict_geo and (lat is not None and lon is not None) and not geo_ok:
        return JSONResponse(status_code=404, content={"detail": "Not Found"})

    route_ids = []
    try:
        for v in variants:
            if not isinstance(v, dict):
                continue
            rid = v.get('route_id')
            if rid is None:
                continue
            route_ids.append(str(rid))
    except Exception:
        route_ids = []

    # Preserve order but remove duplicates for a stable, compact payload.
    try:
        seen = set()
        deduped = []
        for rid in route_ids:
            if rid in seen:
                continue
            seen.add(rid)
            deduped.append(rid)
        route_ids = deduped
    except Exception:
        pass

    payload = {
        "line": line_key,
        "variant_count": int(len(variants)),
        "route_ids": route_ids,
    }
    _route_label_cache[cache_key] = payload
    return payload


@app.get("/routes/line/{line}")
async def routes_for_line(
    line: str,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    strict_geo: int = 0,
):
    """Return route variants for a bus line, each with ordered stops + coords.

    Response::

        {
          "line": "100",
          "variants": [
            {
              "route_id": "ROUTE_abc",
              "stops": [
                {"name": "Stop A", "lat": 54.0, "lon": -2.8, "atco_code": "250..."},
                ...
              ]
            },
            ...           // max 3 most-distinct variants
          ]
        }
    """
    from fastapi.responses import JSONResponse

    # Normalise the line name for cache lookup (case-insensitive)
    # Note: BODS line identifiers are often coded (e.g. "PC0002407:417:1A").
    line_key = line.strip().upper()

    # Configurable thresholds (allow tuning via environment variables)
    # - ROUTE_MIN_STOPS: minimum number of stops a candidate journey must
    #   have to be considered a representative (default: 8)
    # - ROUTE_MAX_GAP_METERS: maximum single-gap allowed between consecutive
    #   stops in a variant (default: 3500 m)
    # - ROUTE_MEAN_GAP_MULT: multiplier applied to the best mean gap to
    #   reject outlier variants (default: 1.8)
    min_stops = int(os.environ.get('ROUTE_MIN_STOPS', '6'))
    max_gap_m = int(os.environ.get('ROUTE_MAX_GAP_METERS', '3500'))
    mean_gap_mult = float(os.environ.get('ROUTE_MEAN_GAP_MULT', '1.8'))

    cache_key = f"{line_key}{_geo_cache_suffix(lat, lon)}"
    if cache_key in _route_line_cache:
        return _route_line_cache[cache_key]

    date_str = datetime.now().strftime("%Y-%m-%d")
    import time
    t0 = time.time()
    try:
        merged, _router, _walking = get_router_for_date(date_str, apply_delay=False)
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"error": "Backend not initialized"},
        )
    t1 = time.time()
    try:
        # Log time spent obtaining router/merged data
        logger.info(f"routes_for_line: get_router_for_date took {int((t1-t0)*1000)}ms for line={line_key}")
    except Exception:
        pass

    # Coordinate lookup: ATCO map with walking stop-int fallback.
    t2 = time.time()
    atco_coord_map, stop_int_coord_map = _build_stop_coord_maps(_walking)
    t3 = time.time()
    try:
        logger.info(
            "routes_for_line: coord maps built in %dms (atco=%d, walking=%d) for line=%s",
            int((t3 - t2) * 1000),
            len(atco_coord_map),
            len(stop_int_coord_map),
            line_key,
        )
    except Exception:
        pass

    # ── Collect candidate route_ints for this line ─────────────────────────
    # Keep everything indexed by mergeddata route_int to avoid mismatching a
    # journey-derived stop order with a different route_id's stored geometry.
    matching_routes: list[int] = []

    def _route_matches_line(r_int: int) -> bool:
        """Return True if route_int's metadata indicates it belongs to this line.

        Historically this endpoint matched only the final ":suffix" (e.g. "...:1").
        That causes collisions (many cities have a "1") and makes coded ids like
        "PC0002407:417:1" impossible to request directly.

        Rules:
        - If the request includes ":" treat it as a full coded line id and require
          exact case-insensitive match.
        - Otherwise treat it as a human line shorthand and match on the final suffix.
        """
        try:
            if r_int < 0 or r_int >= len(merged.route_metadata):
                return False
            meta = merged.route_metadata[r_int] or {}
            raw_line = (meta.get("line_name") or "").strip()
            if not raw_line:
                return False
            raw_norm = raw_line.upper()
            if ":" in line_key:
                return raw_norm == line_key
            rline = raw_norm.split(":")[-1].strip()
            return rline == line_key
        except Exception:
            return False

    # Prefer geo-hinted stop_to_routes resolution when lat/lon provided.
    if lat is not None and lon is not None:
        try:
            dlat = 0.04
            dlon = 0.06
            south, west, north, east = float(lat) - dlat, float(lon) - dlon, float(lat) + dlat, float(lon) + dlon

            stops_geo_list = _stops_geo_cache
            if stops_geo_list is None:
                try:
                    _ = await stops_geo()  # populates _stops_geo_cache
                    stops_geo_list = _stops_geo_cache
                except Exception:
                    stops_geo_list = None

            # Map ATCO -> merged stop_int(s)
            atco_to_stopints: dict[str, list[int]] = {}
            for s_int in range(len(merged.stop_to_routes)):
                code = merged.get_atco_code(s_int)
                if code:
                    atco_to_stopints.setdefault(str(code).strip(), []).append(s_int)

            near_stop_ints: list[int] = []
            if stops_geo_list:
                for s in stops_geo_list:
                    try:
                        slat, slon = float(s.get('lat')), float(s.get('lon'))
                        if not (south <= slat <= north and west <= slon <= east):
                            continue
                        lines = s.get('lines') or []
                        if line_key not in [str(x).strip().upper() for x in lines]:
                            continue
                        atco_code = s.get('atco_code') or s.get('id')
                        if not atco_code:
                            continue
                        for s_int in atco_to_stopints.get(str(atco_code).strip(), []):
                            near_stop_ints.append(s_int)
                    except Exception:
                        continue

            cand = set()
            for s_int in near_stop_ints:
                try:
                    for r_int in merged.stop_to_routes[s_int]:
                        cand.add(int(r_int))
                except Exception:
                    continue
            if cand:
                matching_routes = sorted([r for r in cand if _route_matches_line(r)])
        except Exception:
            pass

    # Fallback: full scan by metadata match.
    #
    # IMPORTANT: For short, human line keys like "1" this fallback is dangerously
    # ambiguous across the whole dataset (many towns have a "1"). If the caller
    # didn't provide a geographic hint (lat/lon) we must not guess, otherwise the
    # frontend can show a totally different city's line.
    if not matching_routes:
        if ":" in line_key or (lat is not None and lon is not None):
            matching_routes = [r for r in range(len(merged.route_metadata)) if _route_matches_line(r)]
        else:
            # No geo hint for shorthand lines: only auto-resolve when all
            # suffix matches belong to exactly one coded line id. This keeps
            # the ambiguity guard while allowing safe lines like "74" in
            # datasets where only one coded line uses that suffix.
            suffix_matches: list[int] = []
            coded_names: set[str] = set()
            for r in range(len(merged.route_metadata)):
                if not _route_matches_line(r):
                    continue
                suffix_matches.append(r)
                try:
                    raw = ((merged.route_metadata[r] or {}).get("line_name") or "").strip().upper()
                    if raw:
                        coded_names.add(raw)
                except Exception:
                    continue
            matching_routes = suffix_matches if len(coded_names) == 1 else []

    # ── Build variants from route_stops (not journey_times) ───────────────
    # We now prefer route_stops because journey_times filtering depends on a
    # particular date/bucket journey selection, and can accidentally omit
    # routes that definitely have stop-to-stop section tracks in the DB.
    #
    # NOTE: route_stops may contain inbound+outbound interleaving for some
    # services. We'll still apply the mean/max-gap heuristics below; and the
    # frontend has additional sanity checks. If we need to split directions,
    # we can add it later.
    import math

    def _route_stops_variant(r_int: int) -> list[dict]:
        """Build ordered stop dicts from merged.route_stops[r_int]."""
        stops = []
        route_stops = merged.route_stops[r_int] if r_int < len(getattr(merged, 'route_stops', []) or []) else []
        for s_int in route_stops or []:
            atco_code, coords = _coords_for_route_stop(
                merged,
                s_int,
                atco_coord_map,
                stop_int_coord_map,
            )
            if not atco_code:
                continue
            if not coords:
                continue
            lat, lon = coords
            name = merged.stop_metadata[s_int] if s_int < len(merged.stop_metadata) else ""
            stops.append({
                "name": name or atco_code,
                "lat": float(lat),
                "lon": float(lon),
                "atco_code": str(atco_code),
            })
        return stops

    def _mean_gap(stops: list[dict]) -> float:
        """Mean consecutive distance in metres (cheap Euclidean approx)."""
        if len(stops) < 2:
            return 0.0
        total = 0.0
        cos_lat = math.cos(math.radians(stops[0]["lat"]))
        for i in range(len(stops) - 1):
            dlat = (stops[i + 1]["lat"] - stops[i]["lat"]) * 111_320
            dlon = (stops[i + 1]["lon"] - stops[i]["lon"]) * 111_320 * cos_lat
            total += math.sqrt(dlat * dlat + dlon * dlon)
        return total / (len(stops) - 1)
    variants = []
    for r_idx in matching_routes:
        s = _route_stops_variant(r_idx)
        if len(s) < min_stops:
            continue
        meta = merged.route_metadata[r_idx] or {}
        route_id = meta.get("route_id", f"route_{r_idx}")
        variants.append({
            "route_int": int(r_idx),
            "route_id": route_id,
            "stops": s,
        })

    # De-duplicate: keep only the most-distinct variants (by ATCO signature).
    seen_sigs: set[tuple] = set()
    unique = []
    for v in variants:
        sig = tuple(s["atco_code"] for s in v["stops"])
        if sig not in seen_sigs:
            seen_sigs.add(sig)
            unique.append(v)

    # Sort by number of stops descending (longest single-direction routes
    # first), take top 3 to avoid visual clutter.
    unique.sort(key=lambda v: len(v["stops"]), reverse=True)
    unique = unique[:3]

    # Drop variants whose mean gap is much worse than the best,
    # or that contain any single gap > 2.5 km — these are typically
    # messy journey patterns that zigzag across the map. If the
    # filters remove all candidates we fall back to the unfiltered
    # top-3 variants so short/irregular routes (e.g. local shuttles)
    # are still shown to the user.
    if unique:
        def _max_gap(stops):
            cos_lat = math.cos(math.radians(stops[0]["lat"]))
            mx = 0.0
            for i in range(len(stops) - 1):
                dlat = (stops[i + 1]["lat"] - stops[i]["lat"]) * 111_320
                dlon = (stops[i + 1]["lon"] - stops[i]["lon"]) * 111_320 * cos_lat
                mx = max(mx, math.sqrt(dlat * dlat + dlon * dlon))
            return mx

        mean_gaps = [_mean_gap(v["stops"]) for v in unique]
        best = min(mean_gaps)
        filtered = [
            v for v, mg in zip(unique, mean_gaps)
        if mg <= best * mean_gap_mult and _max_gap(v["stops"]) < max_gap_m
        ]
        if filtered:
            unique = filtered
        else:
            # Fallback: keep the unfiltered top-3 variants when the
            # gap-based heuristics eliminate everything.
            unique = unique[:3]

    result = {"line": line, "variants": unique}
    # Attach route geometry.
    # Prefer stitching stop-to-stop fragment tracks (route_link_tracks) along the
    # representative stop order. This mirrors the routing geometry behaviour and
    # avoids "teleport" segments when full-route polylines are jumbled.
    for v in result.get('variants', []) or []:
        try:
            r_int = v.get('route_int')
            if not isinstance(r_int, int):
                continue

            stops = v.get('stops') or []
            atcos = [s.get('atco_code') for s in stops if isinstance(s, dict) and s.get('atco_code')]
            coords = _stitch_route_variant_from_link_tracks(
                merged,
                r_int,
                atcos,
                allow_partial=True,
            )
            if coords and len(coords) >= 2:
                v['geometry'] = coords
                v['geometry_source'] = 'route_link_tracks'
            else:
                # Never fall back to a full-route polyline for line overlays.
                # Full polylines can be jumbled (merged inbound/outbound/branches)
                # and produce obvious "teleport" artefacts. If fragment stitching
                # fails, omit geometry so the frontend can fall back to stop-to-stop.
                try:
                    v.pop('geometry', None)
                    v.pop('geometry_source', None)
                except Exception:
                    pass
        except Exception:
            continue
    # Only cache positive results. Caching empty variant lists can cause
    # stale-empty responses when the router/atco caches are built later
    # (for example shortly after server start). Allow empty results to be
    # recomputed on subsequent calls so late-initialised data can populate
    # the response.
    if unique:
        # Apply optional geo filtering before caching so /routes/label reuse is safe.
        try:
            filtered_unique, geo_ok = _filter_variants_near_point(unique, lat, lon)
            if strict_geo and (lat is not None and lon is not None) and not geo_ok:
                # Don't cache a wrong global answer; allow callers to handle 404.
                from fastapi.responses import JSONResponse
                return JSONResponse(status_code=404, content={"detail": "Not Found"})
            result = {"line": line, "variants": filtered_unique}
        except Exception:
            pass

        _route_line_cache[cache_key] = result
    return result


@app.get("/routes/stop/{atco}")
async def routes_for_stop(atco: str):
    """Return route variants that pass through the given ATCO stop code.

    Response mirrors `/routes/line/{line}` but selects routes by whether
    the stop appears in their journey stop lists. Useful for showing the
    route(s) that pass through a clicked stop on the frontend.
    """
    from fastapi.responses import JSONResponse

    atco_code = atco.strip()
    # thresholds (same as routes_for_line)
    min_stops = int(os.environ.get('ROUTE_MIN_STOPS', '8'))
    max_gap_m = int(os.environ.get('ROUTE_MAX_GAP_METERS', '3500'))
    mean_gap_mult = float(os.environ.get('ROUTE_MEAN_GAP_MULT', '1.8'))

    try:
        merged, _router, _walking = get_router_for_date(datetime.now().strftime("%Y-%m-%d"))
    except Exception:
        return JSONResponse(status_code=503, content={"error": "Backend not initialized"})

    # Build mapping of stop_ints whose ATCO code matches the provided code
    matching_stop_ints = []
    for s_int in range(len(merged.stop_to_routes)):
        code = merged.get_atco_code(s_int)
        if code == atco_code:
            matching_stop_ints.append(s_int)

    if not matching_stop_ints:
        return {"atco": atco_code, "routes": []}

    # Collect routes that serve any matching stop_int
    matching_route_idxs = set()
    for s in matching_stop_ints:
        for rid in merged.stop_to_routes[s]:
            matching_route_idxs.add(rid)

    # Prefer route_stops so the result set is entirely constrained by stop_to_routes.
    # This avoids any journey_times-based fallback accidentally pulling in a different
    # city's same-numbered line.
    atco_coord_map, stop_int_coord_map = _build_stop_coord_maps(_walking)

    def _route_stops_variant(r_int: int) -> list[dict]:
        """Build ordered stop dicts from merged.route_stops[r_int]."""
        stops = []
        route_stops = merged.route_stops[r_int] if r_int < len(getattr(merged, 'route_stops', []) or []) else []
        for s_int in route_stops or []:
            code, coords = _coords_for_route_stop(
                merged,
                s_int,
                atco_coord_map,
                stop_int_coord_map,
            )
            if not code:
                continue
            if not coords:
                continue
            lat, lon = coords
            name = merged.stop_metadata[s_int] if s_int < len(merged.stop_metadata) else ""
            stops.append({
                "name": name or code,
                "lat": float(lat),
                "lon": float(lon),
                "atco_code": str(code),
            })
        return stops

    import math
    def _mean_gap(stops: list[dict]) -> float:
        if len(stops) < 2:
            return 0.0
        total = 0.0
        cos_lat = math.cos(math.radians(stops[0]["lat"]))
        for i in range(len(stops) - 1):
            dlat = (stops[i + 1]["lat"] - stops[i]["lat"]) * 111_320
            dlon = (stops[i + 1]["lon"] - stops[i]["lon"]) * 111_320 * cos_lat
            total += math.sqrt(dlat * dlat + dlon * dlon)
        return total / (len(stops) - 1)

    variants = []
    for r_idx in sorted(matching_route_idxs):
        s = _route_stops_variant(r_idx)
        if len(s) < min_stops:
            continue
        meta = merged.route_metadata[r_idx] or {}
        route_id = meta.get("route_id", f"route_{r_idx}")
        variants.append({
            "route_int": int(r_idx),
            "route_id": route_id,
            "stops": s,
        })

    # Deduplicate, sort, filter (same as routes_for_line)
    seen_sigs = set()
    unique = []
    for v in variants:
        sig = tuple(s["atco_code"] for s in v["stops"])
        if sig not in seen_sigs:
            seen_sigs.add(sig)
            unique.append(v)
    unique.sort(key=lambda v: len(v["stops"]), reverse=True)
    unique = unique[:3]

    if unique:
        def _max_gap(stops):
            cos_lat = math.cos(math.radians(stops[0]["lat"]))
            mx = 0.0
            for i in range(len(stops) - 1):
                dlat = (stops[i + 1]["lat"] - stops[i]["lat"]) * 111_320
                dlon = (stops[i + 1]["lon"] - stops[i]["lon"]) * 111_320 * cos_lat
                mx = max(mx, math.sqrt(dlat * dlat + dlon * dlon))
            return mx

        mean_gaps = [_mean_gap(v["stops"]) for v in unique]
        best = min(mean_gaps)
        filtered = [v for v, mg in zip(unique, mean_gaps) if mg <= best * mean_gap_mult and _max_gap(v["stops"]) < max_gap_m]
        if filtered:
            unique = filtered

    # If no variants found via stop_to_routes, return empty list.
    if not unique:
        return {"atco": atco_code, "routes": []}

    # Attach fragment-only geometry, mirroring /routes/line/{line}.
    for v in unique:
        try:
            r_int = v.get('route_int')
            if not isinstance(r_int, int):
                continue

            stops = v.get('stops') or []
            atcos = [s.get('atco_code') for s in stops if isinstance(s, dict) and s.get('atco_code')]
            coords = _stitch_route_variant_from_link_tracks(
                merged,
                r_int,
                atcos,
                allow_partial=True,
            )
            if coords and len(coords) >= 2:
                v['geometry'] = coords
                v['geometry_source'] = 'route_link_tracks'
            else:
                try:
                    v.pop('geometry', None)
                    v.pop('geometry_source', None)
                except Exception:
                    pass
        except Exception:
            continue

    return {"atco": atco_code, "routes": unique}


@app.get("/routes/line_at_stop/{atco}/{line}")
async def routes_for_line_at_stop(
    atco: str,
    line: str,
    limit: int = 10,
):
    """Return route variants for a line *restricted to routes serving a stop*.

    This endpoint is designed to eliminate ambiguity for short line names like
    "1" by only considering route_ints from merged.stop_to_routes for the given
    ATCO stop.

    Response mirrors `/routes/line/{line}`:

        {"line": "1", "variants": [ {"route_id": "...", "stops": [...], "geometry": [...] }, ... ]}
    """
    from fastapi.responses import JSONResponse

    atco_code = atco.strip()
    line_key = line.strip().upper()

    # thresholds (same as routes_for_line)
    min_stops = int(os.environ.get('ROUTE_MIN_STOPS', '6'))
    max_gap_m = int(os.environ.get('ROUTE_MAX_GAP_METERS', '3500'))
    mean_gap_mult = float(os.environ.get('ROUTE_MEAN_GAP_MULT', '1.8'))

    try:
        merged, _router, _walking = get_router_for_date(datetime.now().strftime("%Y-%m-%d"))
    except Exception:
        return JSONResponse(status_code=503, content={"error": "Backend not initialized"})

    # Identify stop_int(s) for this ATCO code
    matching_stop_ints: list[int] = []
    for s_int in range(len(merged.stop_to_routes)):
        try:
            code = merged.get_atco_code(s_int)
        except Exception:
            code = None
        if code == atco_code:
            matching_stop_ints.append(s_int)

    if not matching_stop_ints:
        return {"line": line, "variants": []}

    # Candidate route_ints are strictly those serving this stop
    candidate_routes: set[int] = set()
    for s_int in matching_stop_ints:
        try:
            for r_int in merged.stop_to_routes[s_int]:
                candidate_routes.add(int(r_int))
        except Exception:
            continue

    def _route_matches_line(r_int: int) -> bool:
        try:
            if r_int < 0 or r_int >= len(merged.route_metadata):
                return False
            meta = merged.route_metadata[r_int] or {}
            raw_line = (meta.get("line_name") or "").strip()
            if not raw_line:
                return False
            raw_norm = raw_line.upper()
            if ":" in line_key:
                return raw_norm == line_key
            return raw_norm.split(":")[-1].strip() == line_key
        except Exception:
            return False

    matching_routes = sorted([r for r in candidate_routes if _route_matches_line(r)])
    if not matching_routes:
        return {"line": line, "variants": []}

    # NaPTAN coordinate lookup
    atco_coord_map, stop_int_coord_map = _build_stop_coord_maps(_walking)

    def _route_stops_variant(r_int: int) -> list[dict]:
        """Build ordered stop dicts from merged.route_stops[r_int].

        Important: we *always* preserve the underlying route stop order. If a
        stop is missing NaPTAN coordinates we skip it for rendering, but we
        never re-order the remaining stops based on partial lookups.
        """
        stops_with_pos: list[tuple[int, dict]] = []
        route_stops = merged.route_stops[r_int] if r_int < len(getattr(merged, 'route_stops', []) or []) else []
        for pos, s_int in enumerate(route_stops or []):
            code, coords = _coords_for_route_stop(
                merged,
                s_int,
                atco_coord_map,
                stop_int_coord_map,
            )
            if not code:
                continue
            if not coords:
                continue
            lat0, lon0 = coords
            name = merged.stop_metadata[s_int] if s_int < len(merged.stop_metadata) else ""
            stops_with_pos.append((
                pos,
                {
                    "name": name or code,
                    "lat": float(lat0),
                    "lon": float(lon0),
                    "atco_code": str(code),
                },
            ))

        stops_with_pos.sort(key=lambda x: x[0])
        return [d for _, d in stops_with_pos]

    import math
    def _mean_gap(stops: list[dict]) -> float:
        if len(stops) < 2:
            return 0.0
        total = 0.0
        cos_lat = math.cos(math.radians(stops[0]["lat"]))
        for i in range(len(stops) - 1):
            dlat = (stops[i + 1]["lat"] - stops[i]["lat"]) * 111_320
            dlon = (stops[i + 1]["lon"] - stops[i]["lon"]) * 111_320 * cos_lat
            total += math.sqrt(dlat * dlat + dlon * dlon)
        return total / (len(stops) - 1)

    variants: list[dict] = []
    for r_int in matching_routes:
        stops = _route_stops_variant(r_int)
        if len(stops) < min_stops:
            continue
        meta = merged.route_metadata[r_int] or {}
        route_id = meta.get("route_id", f"route_{r_int}")
        variants.append({
            "route_int": int(r_int),
            "route_id": route_id,
            "stops": stops,
        })

    # Deduplicate and keep a few most distinct variants
    seen_sigs: set[tuple] = set()
    unique: list[dict] = []
    for v in variants:
        sig = tuple(s["atco_code"] for s in v.get("stops") or [])
        if sig not in seen_sigs:
            seen_sigs.add(sig)
            unique.append(v)
    unique.sort(key=lambda v: len(v.get("stops") or []), reverse=True)
    # Historically we returned only a few variants to keep payloads small.
    # For stop-popup line overlays we often want to browse more variants,
    # so make this cap configurable.
    try:
        lim = int(limit) if limit is not None else 10
    except Exception:
        lim = 10
    # Keep it sane to avoid returning huge payloads.
    if lim < 1:
        lim = 1
    if lim > 25:
        lim = 25
    unique = unique[:lim]

    # Apply the same mean/max gap heuristics as /routes/line
    if unique:
        def _max_gap(stops: list[dict]) -> float:
            cos_lat = math.cos(math.radians(stops[0]["lat"]))
            mx = 0.0
            for i in range(len(stops) - 1):
                dlat = (stops[i + 1]["lat"] - stops[i]["lat"]) * 111_320
                dlon = (stops[i + 1]["lon"] - stops[i]["lon"]) * 111_320 * cos_lat
                mx = max(mx, math.sqrt(dlat * dlat + dlon * dlon))
            return mx

        mean_gaps = [_mean_gap(v["stops"]) for v in unique]
        best = min(mean_gaps)
        filtered = [v for v, mg in zip(unique, mean_gaps) if mg <= best * mean_gap_mult and _max_gap(v["stops"]) < max_gap_m]
        if filtered:
            unique = filtered

    # Attach fragment-only geometry (route_link_tracks), same as /routes/line.
    for v in unique:
        try:
            r_int = v.get('route_int')
            if not isinstance(r_int, int):
                continue

            stops = v.get('stops') or []
            atcos = [s.get('atco_code') for s in stops if isinstance(s, dict) and s.get('atco_code')]
            coords = _stitch_route_variant_from_link_tracks(
                merged,
                r_int,
                atcos,
                allow_partial=True,
            )
            if coords and len(coords) >= 2:
                v['geometry'] = coords
                v['geometry_source'] = 'route_link_tracks'
            else:
                v.pop('geometry', None)
                v.pop('geometry_source', None)
        except Exception:
            continue

    return {"line": line, "variants": unique}


# — Static files & frontend ————————————————————————————————

# Only mount static files if the directory exists (skipped during tests)
_static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
if os.path.isdir(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")


@app.get("/")
async def get_frontend():
    """Serve the frontend HTML page."""
    return FileResponse(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
    )


# Some browsers (Safari / iOS) request apple-touch-icon files from root.
# These are optional and not present in the project by default; return a
# tiny 1x1 transparent PNG so requests don't 404 and clutter logs.
@app.get("/apple-touch-icon.png")
@app.get("/apple-touch-icon-precomposed.png")
@app.get("/favicon.ico")
async def _apple_touch_icon():
    from fastapi.responses import Response
    import base64

    # 1x1 transparent PNG (very small, base64-encoded)
    png_b64 = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII="
    )
    data = base64.b64decode(png_b64)
    return Response(content=data, media_type="image/png")


def _bus_delay_status(delay_s: Optional[int]) -> str:
    """Convert delay_seconds to a human-readable bus status string."""
    # Treat missing or small negative delays as 'On time'. Only sufficiently
    # large positive delays should be labelled 'Delayed N min'. The project
    # preference: if a vehicle is early, show it as 'On time' rather than
    # explicitly marking it 'Early'. Keep delay value itself unchanged.
    if delay_s is None:
        return "On time"
    if delay_s >= 60:
        minutes = round(delay_s / 60)
        return f"Delayed {minutes} min"
    # All negative delays are presented to callers as negative numeric
    # minutes but the human-readable status will be 'On time'. This avoids
    # showing 'Early' labels in the UI.
    return "On time"


def _haversine_m(lat1, lon1, lat2, lon2):
    """Return distance in meters between two lat/lon points using haversine."""
    import math
    R = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _compute_delay_from_timetable(line_ref, dest, lat_v, lon_v, return_jid: bool = False, origin_dep_secs: int = None, operator_ref: str = None, strict_tol: int = 600, feed_origin_atco: str = None, feed_destination_atco: str = None, origin_tz_offset_secs: int = 0, allow_offtrack: bool = False, allow_abs_delay: bool = False, return_debug: bool = False):
    """Compute a delay (seconds) by matching a live vehicle to a timetable journey.

    Stable algorithm – designed to return consistent results across
    consecutive calls even when GPS coordinates jitter by a few metres:

    1.  Collect candidate journeys matching line/destination.
    2.  For each, project the vehicle onto the route track polyline using
        **edge interpolation** (not just vertex snapping) so that tiny
        GPS changes produce smooth, monotonic progress changes.
    3.  Apply time-window and spatial guards to reject implausible matches.
    3b. If ``origin_dep_secs`` is provided (from the feed's
        ``<OriginAimedDepartureTime>``), **reject** any candidate whose
        first-stop departure time does not match within a tolerance of
        120 seconds.  This is the strongest single filter — a vehicle
        knows which service it is running.
    4.  Interpolate expected_time from the stop-progress curve.
    5.  Score candidates with ``(dist_m, abs(delay), j_id)``.  The key
        insight: when multiple journeys share the same route, ``dist_m``
        is identical — so the best match is the journey whose
        interpolated expected_time is closest to *now* (smallest
        ``|delay|``).  ``j_id`` provides final determinism.
    6.  Sanity-clamp: reject any match whose |delay| exceeds
        ``max(journey_duration * 0.5, 1200)`` – a bus more than 20 min
        late (or half its journey duration) is almost certainly a
        mismatch against the wrong departure.

    Returns int seconds or None when no confident match is found.

    When ``return_jid`` is True (historical name), this function returns a
    tuple ``(delay_seconds, route_int)`` where ``route_int`` is the merged
    route index (i.e. ``merged.journey_to_route[journey_id]``). This is the
    most useful identifier for rendering route tracks.
    """
    try:
        from datetime import datetime
        import math

        today = datetime.now().date().isoformat()
        now = datetime.now()
        now_seconds = now.hour * 3600 + now.minute * 60 + now.second

        # When computing delays we must avoid recursive application of
        # already-applied delays. Request the raw merged/router/walking
        # without applying the delay-adjusted timetable.
        merged, router, walking = get_router_for_date(today, start_time=now_seconds, apply_delay=False)
    except Exception:
        return None

    # --- provenance/debug: capture what actually blocked matching ---
    _dbg: dict[str, Any] = {
        'line': (line_ref or '').split(':')[-1].strip() if line_ref else None,
        'operator_ref': operator_ref,
        'origin_dep_secs': origin_dep_secs,
        'origin_atco': (str(feed_origin_atco).strip() if feed_origin_atco else None),
        'destination_atco': (str(feed_destination_atco).strip() if feed_destination_atco else None),
    }

    def _dbg_cand_dist(name: str, cands: list[tuple] | list[int] | set[int]) -> None:
        """Record a small neg/pos distribution snapshot for candidate journeys.

        `cands` can be:
        - list[(j_id, start_dep, end_arr, r_int)]
        - list[j_id] / set[j_id]
        """
        if not return_debug:
            return
        try:
            # Normalise to journey ids
            if not cands:
                _dbg.setdefault('candidate_trace', []).append({'stage': name, 'n': 0})
                return
            if isinstance(next(iter(cands)), tuple):
                jids = [int(x[0]) for x in cands]  # type: ignore[index]
            else:
                jids = [int(x) for x in cands]  # type: ignore[arg-type]
            neg = 0
            pos = 0
            unknown = 0
            sd_min = None
            sd_max = None
            neg_ex = []
            pos_ex = []
            for jid in jids:
                try:
                    jt = merged.journey_times[jid]
                    if not jt:
                        unknown += 1
                        continue
                    sd = jt[0][2]
                    if sd is None:
                        unknown += 1
                        continue
                    sd = int(sd)
                except Exception:
                    unknown += 1
                    continue
                if sd_min is None or sd < sd_min:
                    sd_min = sd
                if sd_max is None or sd > sd_max:
                    sd_max = sd
                if sd < 0:
                    neg += 1
                    if len(neg_ex) < 3:
                        neg_ex.append(jid)
                else:
                    pos += 1
                    if len(pos_ex) < 3:
                        pos_ex.append(jid)
            rec = {
                'stage': name,
                'n': int(len(jids)),
                'neg_n': int(neg),
                'pos_n': int(pos),
                'unknown_n': int(unknown),
                'start_dep_min': int(sd_min) if sd_min is not None else None,
                'start_dep_max': int(sd_max) if sd_max is not None else None,
                'neg_examples': neg_ex,
                'pos_examples': pos_ex,
            }
            _dbg.setdefault('candidate_trace', []).append(rec)
        except Exception:
            pass

    # Staging debug: capture candidate distributions (neg vs pos day-frame)
    # after each stage latch/refine. This is *debug only* and does not affect
    # matching behaviour.
    if return_debug:
        try:
            _stage_res = _stage_filter_journeys_for_live_bus(
                merged,
                walking,
                line_ref=(line_ref or '').split(':')[-1].strip() if line_ref else (line_ref or ''),
                operator_ref=operator_ref,
                dest=dest,
                feed_origin_atco=feed_origin_atco,
                feed_destination_atco=feed_destination_atco,
                origin_dep_secs=origin_dep_secs,
                origin_tz_offset_secs=origin_tz_offset_secs,
                strict_tol=strict_tol,
                return_debug=True,
            )
            # _stage_res is (ids, debug)
            _dbg['stage_debug'] = _stage_res[1]
        except Exception as _e:
            try:
                _dbg['stage_debug_error'] = str(_e)
            except Exception:
                pass

    # Prefilter breakdown: help explain why pos-day candidates disappear *before*
    # latching. This is debug-only and intentionally bounded.
    _prefilter_dbg = None
    if return_debug:
        _prefilter_dbg = {
            'spatial_too_far_total': 0,
            'spatial_too_far_pos': 0,
            'spatial_too_far_neg': 0,
            'spatial_too_far_examples': [],  # [{j_id, start_dep, r_int, nearby_min_m, threshold_m}]
            'nearest_stop_not_served_total': 0,
            'nearest_stop_not_served_pos': 0,
            'nearest_stop_not_served_neg': 0,
            'nearest_stop_not_served_examples': [],  # [{j_id, start_dep, nearest_stop_int, r_int}]
        }
        _dbg['prefilter_breakdown'] = _prefilter_dbg

    def _dbg_gate(name: str, **extra) -> None:
        """Record the deepest-known fatal gate that caused rejection.

        We keep only the first fatal gate in a stable order so payload stays small.
        """
        try:
            if not return_debug:
                return
            if _dbg.get('fatal_gate') is None:
                _dbg['fatal_gate'] = name
                if extra:
                    # keep it bounded
                    for k, v in list(extra.items())[:20]:
                        _dbg[k] = v
        except Exception:
            pass

    # Ensure we emit gate summary even on early returns later.
    def _log_gate_summary(result) -> Any:
        try:
            if (DEBUG_MATCH or return_debug) and _gate_counts:
                top = sorted(_gate_counts.items(), key=lambda kv: kv[1], reverse=True)[:6]
                logger.info(
                    "matcher: gate_summary line=%s op=%s origin_dep=%s origin_atco=%s dest_atco=%s gates=%s",
                    (line_ref or '').split(':')[-1].strip(),
                    operator_ref,
                    origin_dep_secs,
                    (str(feed_origin_atco).strip() if feed_origin_atco else None),
                    (str(feed_destination_atco).strip() if feed_destination_atco else None),
                    dict(top),
                )
        except Exception:
            pass
        if return_debug:
            # Only return a small bounded summary for API diagnostics.
            try:
                top = sorted(_gate_counts.items(), key=lambda kv: kv[1], reverse=True)[:6]
                _dbg['gates_top'] = dict(top)
                return (result, _dbg)
            except Exception:
                return (result, _dbg)
        return result

    # Index to avoid scanning all journeys on every live-vehicle match.
    # Keyed by the identity of the merged timetable instance.
    # Value: dict[str, list[int]] mapping short line name -> journey ids.
    global _LIVE_MATCH_JOURNEYS_BY_LINE
    try:
        _LIVE_MATCH_JOURNEYS_BY_LINE
    except NameError:
        _LIVE_MATCH_JOURNEYS_BY_LINE = {}

    def _journeys_for_line_short(short_line: str) -> list[int]:
        try:
            if not short_line:
                return []
            mkey = id(merged)
            by_line = _LIVE_MATCH_JOURNEYS_BY_LINE.get(mkey)
            if by_line is None:
                by_line = {}
                try:
                    for j_id, jmeta in enumerate(getattr(merged, 'journey_metadata', []) or []):
                        if not jmeta:
                            continue
                        line_name = jmeta.get('line_name') or ''
                        s = line_name.split(':')[-1].strip() if line_name else ''
                        if not s:
                            continue
                        by_line.setdefault(s, []).append(j_id)
                except Exception:
                    # If indexing fails, fall back to empty index.
                    by_line = {}
                _LIVE_MATCH_JOURNEYS_BY_LINE[mkey] = by_line
            return by_line.get(short_line, [])
        except Exception:
            return []

    # Cache per-journey endpoint ATCO sets and start/end times for this merged.
    # This avoids repeatedly walking journey_times to extract endpoint stop codes.
    global _LIVE_MATCH_JOURNEY_ENDPOINTS
    try:
        _LIVE_MATCH_JOURNEY_ENDPOINTS
    except NameError:
        _LIVE_MATCH_JOURNEY_ENDPOINTS = {}

    def _get_journey_endpoint_cache():
        """Return (origin_atcos, dest_atcos, start_dep, end_arr) arrays."""
        mkey = id(merged)
        cached = _LIVE_MATCH_JOURNEY_ENDPOINTS.get(mkey)
        if cached is not None:
            return cached

        try:
            ENDPOINT_SHIFT_STOPS = int(os.environ.get('MATCH_ENDPOINT_SHIFT_STOPS', '2'))
        except Exception:
            ENDPOINT_SHIFT_STOPS = 2

        n_j = len(getattr(merged, 'journey_metadata', []) or [])
        origin_list: list[set[str]] = [set() for _ in range(n_j)]
        dest_list: list[set[str]] = [set() for _ in range(n_j)]
        start_list: list[Optional[int]] = [None for _ in range(n_j)]
        end_list: list[Optional[int]] = [None for _ in range(n_j)]

        for j_id in range(n_j):
            try:
                jt = merged.journey_times[j_id]
                if not jt:
                    continue
            except Exception:
                continue

            # Times
            try:
                start_list[j_id] = jt[0][2]
            except Exception:
                start_list[j_id] = None
            try:
                end_list[j_id] = jt[-1][1] if jt[-1][1] is not None else jt[-1][2]
            except Exception:
                end_list[j_id] = None

            # Endpoint ATCO sets (allow a little shift at both ends)
            try:
                n_end = min(max(0, 3 + int(ENDPOINT_SHIFT_STOPS)), len(jt))
            except Exception:
                n_end = min(3, len(jt))

            try:
                for i in range(min(n_end, len(jt))):
                    atco = merged.get_atco_code(jt[i][0])
                    if atco:
                        origin_list[j_id].add(str(atco).strip())
            except Exception:
                pass

            try:
                for i in range(1, min(n_end, len(jt)) + 1):
                    atco = merged.get_atco_code(jt[-i][0])
                    if atco:
                        dest_list[j_id].add(str(atco).strip())
            except Exception:
                pass

        cached = (origin_list, dest_list, start_list, end_list)
        _LIVE_MATCH_JOURNEY_ENDPOINTS[mkey] = cached
        return cached

    # Debug toggle: enable verbose matcher logging when MATCH_DEBUG=1 or BUS_LIVE_PROVENANCE is truthy
    try:
        DEBUG_MATCH = (str(os.environ.get('MATCH_DEBUG') or '') == '1') or (str(os.environ.get('BUS_LIVE_PROVENANCE') or '').lower() in ('1', 'true', 'yes'))
    except Exception:
        DEBUG_MATCH = False

    # When provenance/debug is enabled, capture a small per-call count of the
    # main rejection gates. This makes it much easier to diagnose situations
    # where all vehicles end up "unmatched".
    _gate_counts: dict[str, int] = {}

    def _gate_hit(name: str) -> None:
        try:
            _gate_counts[name] = int(_gate_counts.get(name, 0)) + 1
        except Exception:
            pass

    line_q = (line_ref or "").strip()
    dest_q = (dest or "").strip().lower()

    # Stop-name matching (free-text destination) is fuzzy and can be expensive.
    # Disable by default for determinism/performance.
    try:
        ENABLE_STOP_NAME_MATCH = str(os.environ.get('ENABLE_STOP_NAME_MATCH') or '').lower() in ('1', 'true', 'yes')
    except Exception:
        ENABLE_STOP_NAME_MATCH = False

    # Cache journey stop membership within a matcher call so we don't rebuild
    # `[sid for sid, ... in jt]` and corresponding sets repeatedly per candidate.
    _journey_stop_set_cache: dict[int, set[int]] = {}

    def _get_journey_stop_set(j_id: int, jt_local) -> set[int]:
        try:
            cached = _journey_stop_set_cache.get(j_id)
            if cached is not None:
                return cached
            s = set()
            for sid, _at, _dt in jt_local:
                try:
                    s.add(int(sid))
                except Exception:
                    # stop ids are expected to be ints; ignore malformed
                    continue
            _journey_stop_set_cache[j_id] = s
            return s
        except Exception:
            # Ensure dict has a stable value so we don't retry work for this j_id
            _journey_stop_set_cache[j_id] = set()
            return _journey_stop_set_cache[j_id]

    # Local caches to cut repeated walking-module lookups.
    # These exist only for the duration of a single matcher call (per vehicle).
    _coords_cache: dict[int, Optional[tuple[float, float]]] = {}

    def _get_coords(stop_int: int) -> Optional[tuple[float, float]]:
        try:
            if stop_int in _coords_cache:
                return _coords_cache[stop_int]
            c = walking.get_loc_coords(stop_int)
            if c and len(c) == 2:
                res = (float(c[0]), float(c[1]))
            else:
                res = None
            _coords_cache[stop_int] = res
            return res
        except Exception:
            _coords_cache[stop_int] = None
            return None

    # Query the walking module once. This is used both for defining a
    # nearby-stops gate and (optionally) for nearest-stop consistency.
    try:
        _reachable_stops = walking.reachable_stops((lat_v, lon_v)) or []
    except Exception:
        _reachable_stops = []

    # Normalize feed ATCOs once so staged matching is consistent.
    try:
        feed_origin_atco_n = str(feed_origin_atco).strip() if feed_origin_atco else None
    except Exception:
        feed_origin_atco_n = None
    try:
        feed_destination_atco_n = str(feed_destination_atco).strip() if feed_destination_atco else None
    except Exception:
        feed_destination_atco_n = None

    # ── helper: haversine distance in metres ──
    def _hav(lat1, lon1, lat2, lon2):
        R = 6371000.0
        p1, p2 = math.radians(lat1), math.radians(lat2)
        dp = math.radians(lat2 - lat1)
        dl = math.radians(lon2 - lon1)
        a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        return 2 * R * math.asin(math.sqrt(a))

    # ── helper: cumulative distance along a polyline ──
    def _cum_distances(track):
        dists = [0.0]
        for i in range(1, len(track)):
            dists.append(dists[-1] + _hav(track[i - 1][0], track[i - 1][1],
                                           track[i][0], track[i][1]))
        return dists

    # ── helper: project a point onto a polyline (edge-interpolated) ──
    def _project_onto_track(plat, plon, track, cum_dists):
        """Project (plat, plon) onto the nearest point on the polyline.

        Uses proper edge-interpolation so that a point between two track
        vertices gets a smoothly-varying progress fraction, eliminating
        the vertex-snap instability that caused flicker.

        Returns (min_dist_m, progress_fraction  0..1).
        """
        if not track:
            return (1e9, 0.0)
        total_len = cum_dists[-1] if cum_dists[-1] > 0 else 1.0
        best_dist = 1e9
        best_along = 0.0  # cumulative metres along track of closest point

        for i in range(len(track) - 1):
            ax, ay = track[i]
            bx, by = track[i + 1]
            # Vector AB and AP (in approximate local metres)
            cos_lat = math.cos(math.radians((ax + bx) / 2))
            abx = (by - ay) * cos_lat * 111320.0
            aby = (bx - ax) * 111320.0
            apx = (plon - ay) * cos_lat * 111320.0
            apy = (plat - ax) * 111320.0
            ab2 = abx * abx + aby * aby
            if ab2 < 1e-9:
                t = 0.0
            else:
                t = (apx * abx + apy * aby) / ab2
                t = max(0.0, min(1.0, t))
            # Closest point on segment
            cx = ax + t * (bx - ax)
            cy = ay + t * (by - ay)
            d = _hav(plat, plon, cx, cy)
            if d < best_dist:
                best_dist = d
                seg_len = cum_dists[i + 1] - cum_dists[i]
                best_along = cum_dists[i] + t * seg_len
                best_seg = i
                best_t = t

        # Also check all vertices (handles single-point tracks, endpoints)
        for i in range(len(track)):
            d = _hav(plat, plon, track[i][0], track[i][1])
            if d < best_dist:
                best_dist = d
                best_along = cum_dists[i]
                best_seg = i
                best_t = 0.0

        # Return (distance_m, progress_frac, segment_index, seg_t)
        try:
            return (best_dist, best_along / total_len, best_seg, best_t)
        except Exception:
            return (best_dist, best_along / total_len, None, 0.0)

    # ── helper: compute stop progress fractions along a track ──
    def _stop_progress_on_track(jt, walking_mod, track, cum_dists):
        total_len = cum_dists[-1] if cum_dists[-1] > 0 else 1.0
        result = []
        for sid, atime, dtime in jt:
            try:
                coords = _get_coords(sid)
                if not coords:
                    continue
                slat, slon = coords
            except Exception:
                continue
            # Project stop onto track (edge-interpolated for consistency)
            best_d = 1e9
            best_along = 0.0
            for i in range(len(track) - 1):
                ax, ay = track[i]
                bx, by = track[i + 1]
                cos_lat = math.cos(math.radians((ax + bx) / 2))
                abx = (by - ay) * cos_lat * 111320.0
                aby = (bx - ax) * 111320.0
                apx = (slon - ay) * cos_lat * 111320.0
                apy = (slat - ax) * 111320.0
                ab2 = abx * abx + aby * aby
                if ab2 < 1e-9:
                    t = 0.0
                else:
                    t = (apx * abx + apy * aby) / ab2
                    t = max(0.0, min(1.0, t))
                cx = ax + t * (bx - ax)
                cy = ay + t * (by - ay)
                d = _hav(slat, slon, cx, cy)
                if d < best_d:
                    best_d = d
                    seg_len = cum_dists[i + 1] - cum_dists[i]
                    best_along = cum_dists[i] + t * seg_len
            for i in range(len(track)):
                d = _hav(slat, slon, track[i][0], track[i][1])
                if d < best_d:
                    best_d = d
                    best_along = cum_dists[i]
            sched = atime if atime is not None else dtime
            if sched is not None:
                result.append((best_along / total_len, sched))
        result.sort(key=lambda x: x[0])
        return result

    # Optional: map of normalized stop-name -> list of merged stop indices,
    # used only for weak free-text destination matching.
    stop_name_map = {}
    if ENABLE_STOP_NAME_MATCH:
        try:
            for si, sname in enumerate(merged.stop_metadata or []):
                try:
                    if not sname:
                        continue
                    key = str(sname).strip().lower()
                    stop_name_map.setdefault(key, []).append(si)
                except Exception:
                    continue
        except Exception:
            stop_name_map = {}

    # ── 1. collect candidate journeys ──
    candidates = []
    # Precompute set of stops within SEARCH_STOP_RADIUS_M metres of the
    # vehicle location. Per request, only journeys that serve at least one
    # of these nearby stops will be considered. If no stops exist within
    # the radius, consider the vehicle off-track and abort early.
    try:
        try:
            SEARCH_STOP_RADIUS_M = int(os.environ.get('MATCH_STOP_RADIUS_M', '3000'))
        except Exception:
            SEARCH_STOP_RADIUS_M = 3000
        nearby_stops_set = set()
        # Fast-path: use walking's own reachable stop search first, then
        # treat the vehicle as off-track if nothing is reachable.
        for sid, _walk_secs in _reachable_stops:
            nearby_stops_set.add(sid)
    except Exception:
        nearby_stops_set = set()

    # If there are no stops within the search radius, treat as off-track.
    if not nearby_stops_set:
        if str(os.environ.get('BUS_LIVE_PROVENANCE') or '').lower() in ('1', 'true', 'yes'):
            logger.info('matcher: no nearby stops within %dm — vehicle considered off-track', SEARCH_STOP_RADIUS_M)
        return None
    # Spatial prefilter: skip routes whose geometry is far from the
    # vehicle to avoid cross-region matches when short line names are
    # ambiguous. Read threshold from env so it can be tuned.
    try:
        MATCH_MAX_TRACK_DIST_M = int(os.environ.get('MATCH_MAX_TRACK_DIST_M', '2000'))
    except Exception:
        MATCH_MAX_TRACK_DIST_M = 2000
    # New staged matching (requested):
    #   1) line (+operator) match (same as before)
    #   2) try latch by origin_atco + origin_dep_secs (time compare at that stop)
    #   3) if none latched, try latch by destination_atco
    #   4) for destination-latched candidates, also require origin_atco to exist
    #      in the journey and compare feed origin_dep_secs against the arrival/
    #      departure time at that origin stop.
    #
    # Notes:
    # - We still keep the downstream spatial scoring logic; this block is about
    #   collecting plausible candidates deterministically.
    # - If origin_dep_secs is not present, we fall back to the previous endpoint
    #   heuristics to avoid dropping all candidates.

    staged_flags = {}  # j_id -> {'origin_atco': bool, 'dest_atco': bool, 'origin_time_aligned': bool}

    # Precompute feed origin time adjusted by timezone offset.
    od_adj = None
    if origin_dep_secs is not None:
        try:
            od_adj = int(origin_dep_secs) + int(origin_tz_offset_secs or 0)
        except Exception:
            try:
                od_adj = int(origin_dep_secs)
            except Exception:
                od_adj = None

    # Iterate only journeys for this short line name.
    j_ids_for_line = _journeys_for_line_short(line_q)
    _dbg_cand_dist('line_index_j_ids', j_ids_for_line)
    origin_atcos_by_j, dest_atcos_by_j, start_dep_by_j, end_arr_by_j = _get_journey_endpoint_cache()

    for j_id in j_ids_for_line:
        try:
            jmeta = merged.journey_metadata[j_id]
        except Exception:
            continue
        if not jmeta:
            continue
        # Line already filtered by index.
        # Gather endpoint ATCOs for staged matching (cached).
        try:
            origin_atcos = origin_atcos_by_j[j_id] if j_id < len(origin_atcos_by_j) else set()
            dest_atcos = dest_atcos_by_j[j_id] if j_id < len(dest_atcos_by_j) else set()
        except Exception:
            origin_atcos = set()
            dest_atcos = set()

        # Load journey_times only when we actually need them for deeper checks.
        try:
            jt = merged.journey_times[j_id]
            if not jt:
                continue
        except Exception:
            continue

        origin_atco_match = bool(feed_origin_atco_n and feed_origin_atco_n in origin_atcos)
        dest_atco_match = bool(feed_destination_atco_n and feed_destination_atco_n in dest_atcos)

        # If no explicit destination ATCO is provided, fall back to free-text
        # destination matching (previous behaviour) but do NOT treat that as a
        # destination-atco latch. It's just a weak hint.
        weak_dest_text_match = False
        if ENABLE_STOP_NAME_MATCH and not feed_destination_atco_n and dest_q:
            try:
                candidate_dest_atcos = set()
                for sname_key, sidx_list in stop_name_map.items():
                    if dest_q in sname_key or sname_key in dest_q:
                        for si in sidx_list:
                            atco = merged.get_atco_code(si)
                            if atco:
                                candidate_dest_atcos.add(str(atco).strip())
                if candidate_dest_atcos and any(da in candidate_dest_atcos for da in dest_atcos):
                    weak_dest_text_match = True
            except Exception:
                weak_dest_text_match = False

        # Stage 2: origin_atco + time alignment.
        origin_time_aligned = False
        origin_stop_time = None
        if origin_atco_match and od_adj is not None:
            try:
                for stop_int, arr_t, dep_t in jt:
                    s_atco = merged.get_atco_code(stop_int)
                    if s_atco and str(s_atco).strip() == feed_origin_atco_n:
                        # Use arrival if present, else departure.
                        origin_stop_time = arr_t if arr_t is not None else dep_t
                        if origin_stop_time is not None and abs(int(origin_stop_time) - int(od_adj)) <= int(strict_tol):
                            origin_time_aligned = True
                        break
            except Exception:
                origin_time_aligned = False

        staged_flags[j_id] = {
            'origin_atco': origin_atco_match,
            'dest_atco': dest_atco_match,
            'weak_dest_text': weak_dest_text_match,
            'origin_time_aligned': origin_time_aligned,
        }

        # Strict operator/service match: require journey's recorded service_code
        # to match the feed-provided operator_ref. If the journey metadata
        # lacks a service_code, attempt to derive from line_name prefix if present.
        if operator_ref:
            # Prefer the authoritative National Operator Code (if present)
            # which maps to <Operators><Operator><NationalOperatorCode>
            op_noc = jmeta.get("operator_national_code") or None
            svc = None
            if op_noc:
                svc = str(op_noc).strip()
            else:
                # Fall back to service_code (older datasets)
                sc = jmeta.get("service_code") or ""
                if sc:
                    svc = str(sc).strip()
                else:
                    # line_name may be prefixed with service_code:line when loaded
                    ln = (jmeta.get("line_name") or "")
                    if ":" in ln:
                        svc = ln.split(":")[0]
            if not svc or svc.strip() != str(operator_ref).strip():
                # operator mismatch — in strict mode, reject candidate
                continue
        # We defer the endpoint/time latching decision until after we have
        # scanned all journeys. For now just store basic times.
        try:
            start_dep = start_dep_by_j[j_id] if j_id < len(start_dep_by_j) else None
            end_arr = end_arr_by_j[j_id] if j_id < len(end_arr_by_j) else None
            if start_dep is None or end_arr is None:
                # fallback to direct extraction for this journey
                start_dep = jt[0][2]
                end_arr = jt[-1][1] if jt[-1][1] is not None else jt[-1][2]
        except Exception:
            continue

        # Time-window guard: if a candidate's scheduled origin start is too far
        # in the past, reject it early. This complements the existing
        # "too far in the future" check to prevent stale previous journeys from
        # being matched.
        #
        # Default: 4 hours.
        try:
            MATCH_REJECT_PAST_START_S = int(os.environ.get('MATCH_REJECT_PAST_START_S', str(4 * 3600)))
        except Exception:
            MATCH_REJECT_PAST_START_S = 4 * 3600
        try:
            if start_dep is not None and MATCH_REJECT_PAST_START_S is not None:
                if int(start_dep) < int(now_seconds) - int(MATCH_REJECT_PAST_START_S):
                    continue
        except Exception:
            pass

        # Day guard: allow journeys from yesterday and today.
        # The merged timetable includes journeys with day-offsets
        # (previous day encoded as negative seconds). Restrict to
        # journeys whose scheduled start_dep falls within the range
        # [-86400, 86400) so we consider yesterday and today only.
        try:
            if start_dep is None:
                continue
            if start_dep < -86400 or start_dep >= 86400:
                continue
        except Exception:
            continue

        r_int = merged.journey_to_route[j_id] if j_id < len(merged.journey_to_route) else -1
        if r_int < 0:
            continue

        # Quick spatial pre-filter: ensure the candidate route's stop coords have
        # at least one vertex within MATCH_MAX_TRACK_DIST_M of the vehicle.
    # (legacy full-route polylines are intentionally removed.)
        try:
            nearby_min = 1e9
            route_stops = merged.route_stops[r_int] if r_int < len(merged.route_stops) else []
            for sid in route_stops:
                try:
                    coords = _get_coords(sid)
                    if coords:
                        d = _hav(lat_v, lon_v, coords[0], coords[1])
                        if d < nearby_min:
                            nearby_min = d
                except Exception:
                    continue
            if nearby_min > MATCH_MAX_TRACK_DIST_M:
                # route too far from vehicle — skip candidate
                if _prefilter_dbg is not None:
                    try:
                        sdv = int(start_dep) if start_dep is not None else None
                    except Exception:
                        sdv = None
                    try:
                        _prefilter_dbg['spatial_too_far_total'] = int(_prefilter_dbg.get('spatial_too_far_total', 0)) + 1
                        if sdv is not None and sdv >= 0:
                            _prefilter_dbg['spatial_too_far_pos'] = int(_prefilter_dbg.get('spatial_too_far_pos', 0)) + 1
                        elif sdv is not None and sdv < 0:
                            _prefilter_dbg['spatial_too_far_neg'] = int(_prefilter_dbg.get('spatial_too_far_neg', 0)) + 1
                        if len(_prefilter_dbg.get('spatial_too_far_examples') or []) < 3:
                            _prefilter_dbg['spatial_too_far_examples'].append({
                                'j_id': int(j_id),
                                'start_dep': sdv,
                                'r_int': int(r_int),
                                'nearby_min_m': float(nearby_min),
                                'threshold_m': int(MATCH_MAX_TRACK_DIST_M),
                            })
                    except Exception:
                        pass
                continue
        except Exception:
            # If anything goes wrong with the prefilter, don't block
            # matching — fall back to the original permissive behaviour.
            pass

        # NOTE: nearest-stop-served prefilter removed.
        #
        # Historically we rejected candidates when the vehicle's nearest stop
        # wasn't served by the journey (using `merged.journey_stop_index`).
        # Provenance showed this gate was wiping out essentially all "pos-day"
        # candidates in the AM merged timetable, forcing all matches onto
        # yesterday-shifted journeys and triggering outside_time_window.
        #
        # We keep the debug counters for observability, but we no longer reject
        # candidates here.
        try:
            nearby = _reachable_stops
            if nearby:
                nearest_stop_int, walk_secs = nearby[0]
                jsi = merged.journey_stop_index[j_id] if j_id < len(merged.journey_stop_index) else {}
                if nearest_stop_int not in jsi:
                    if _prefilter_dbg is not None:
                        try:
                            sdv = int(start_dep) if start_dep is not None else None
                        except Exception:
                            sdv = None
                        try:
                            _prefilter_dbg['nearest_stop_not_served_total'] = int(_prefilter_dbg.get('nearest_stop_not_served_total', 0)) + 1
                            if sdv is not None and sdv >= 0:
                                _prefilter_dbg['nearest_stop_not_served_pos'] = int(_prefilter_dbg.get('nearest_stop_not_served_pos', 0)) + 1
                            elif sdv is not None and sdv < 0:
                                _prefilter_dbg['nearest_stop_not_served_neg'] = int(_prefilter_dbg.get('nearest_stop_not_served_neg', 0)) + 1
                            if len(_prefilter_dbg.get('nearest_stop_not_served_examples') or []) < 3:
                                _prefilter_dbg['nearest_stop_not_served_examples'].append({
                                    'j_id': int(j_id),
                                    'start_dep': sdv,
                                    'nearest_stop_int': int(nearest_stop_int),
                                    'r_int': int(r_int),
                                })
                        except Exception:
                            pass
        except Exception:
            # If walking lookup fails, don't block — fall back to other checks
            pass

        # ── Origin checks removed as they are now evaluated alongside destination above ──

        # ── OriginAimedDepartureTime gate (optional) ──
        # Use the feed-provided OriginAimedDepartureTime when present. Feeds
        # may include a timezone offset; if provided we incorporate that
        # offset when aligning the feed time-of-day to the scheduled start
        # (also try ±1 day shifts). This is stronger than the previous
        # naive comparison and avoids ±1h heuristics.
        if origin_dep_secs is not None:
            try:
                od = int(origin_dep_secs)
            except Exception:
                od = origin_dep_secs
            tz_off = int(origin_tz_offset_secs or 0)
            within_tol = False
            # Align feed origin time-of-day with scheduled start using
            # the feed-provided timezone offset only. Do NOT perform
            # ±1 day shifts here — requiring day-shifts tended to match
            # incorrect services when dates rolled over. If alignment
            # with timezone offset fails, reject the candidate.
            try:
                adj = od + int(tz_off)
            except Exception:
                adj = od
            try:
                if abs(start_dep - adj) <= int(strict_tol):
                    within_tol = True
            except Exception:
                within_tol = False
            # Do NOT treat origin time misalignment as a fatal rejection.
            # Instead, keep the candidate but mark that the origin time did
            # not align so scoring can penalise it (prefer aligned matches).

        # Record as a line/operator candidate. We'll filter to staged latches below.
        candidates.append((j_id, start_dep, end_arr, r_int))

    _dbg_cand_dist('after_prefilter_candidates', candidates)

    # Apply staged filtering requested by user.
    if not candidates:
        _dbg_gate('no_candidates')
        return None

    # Candidate time distribution sanity snapshot (helps diagnose bad day-offset merges)
    try:
        if return_debug:
            # Merged-wide sanity: does this merged contain any 'today' journeys?
            # (start_dep in [0, 86400)). If this is zero, then we likely merged the
            # wrong days or loaded an empty/shifted dataset.
            try:
                jt_all = getattr(merged, 'journey_times', []) or []
                n_tot = len(jt_all)
                n_pos_day = 0
                n_neg = 0
                min_sd = None
                max_sd = None
                pos_examples = []
                # Keep it bounded; sample evenly but cheaply.
                step = max(1, int(n_tot / 2000))
                for j_id in range(0, n_tot, step):
                    try:
                        jt = jt_all[j_id]
                        if not jt:
                            continue
                        sd = jt[0][2]
                        if sd is None:
                            continue
                        sd = int(sd)
                    except Exception:
                        continue
                    if min_sd is None or sd < min_sd:
                        min_sd = sd
                    if max_sd is None or sd > max_sd:
                        max_sd = sd
                    if sd < 0:
                        n_neg += 1
                    if 0 <= sd < 86400:
                        n_pos_day += 1
                        if len(pos_examples) < 3:
                            pos_examples.append(int(j_id))
                _dbg['merged_journeys_sampled'] = int(len(range(0, n_tot, step))) if n_tot else 0
                _dbg['merged_start_dep_min'] = int(min_sd) if min_sd is not None else None
                _dbg['merged_start_dep_max'] = int(max_sd) if max_sd is not None else None
                _dbg['merged_start_dep_neg_sampled_n'] = int(n_neg)
                _dbg['merged_start_dep_posday_sampled_n'] = int(n_pos_day)
                if pos_examples:
                    _dbg['merged_posday_example_jids'] = pos_examples
            except Exception:
                pass

            _dbg['candidates_n'] = int(len(candidates))
            s_list = [int(s) for (_j, s, _e, _r) in candidates if s is not None]
            e_list = [int(e) for (_j, _s, e, _r) in candidates if e is not None]
            if s_list:
                _dbg['start_dep_min'] = int(min(s_list))
                _dbg['start_dep_max'] = int(max(s_list))
                _dbg['start_dep_neg_n'] = int(sum(1 for v in s_list if v < 0))
                _dbg['start_dep_pos_n'] = int(sum(1 for v in s_list if v >= 0))
            if e_list:
                _dbg['end_arr_min'] = int(min(e_list))
                _dbg['end_arr_max'] = int(max(e_list))
                _dbg['end_arr_neg_n'] = int(sum(1 for v in e_list if v < 0))
                _dbg['end_arr_pos_n'] = int(sum(1 for v in e_list if v >= 0))
            _dbg['now_seconds'] = int(now_seconds)
    except Exception:
        pass

    cand_ids = [c[0] for c in candidates]
    _dbg_cand_dist('cand_ids_before_latch', cand_ids)
    latched = set()

    # Stage 2 latch: origin ATCO + origin start time match (preferred).
    # SAFE SOFT-GATE: if we can't find any time-aligned candidates but we *do*
    # have a strong anchor (origin ATCO exists in the journey), we keep those
    # as candidates rather than dropping everything.
    if feed_origin_atco_n and od_adj is not None:
        aligned = set()
        origin_only = set()
        for j_id in cand_ids:
            f = staged_flags.get(j_id) or {}
            if f.get('origin_atco'):
                origin_only.add(j_id)
                if f.get('origin_time_aligned'):
                    aligned.add(j_id)
        if aligned:
            latched = aligned
        elif origin_only:
            latched = origin_only

    _dbg_cand_dist('after_stage2_latched', latched if latched else cand_ids)

    # Stage 3 latch: destination ATCO (for those not already latched)
    if not latched and feed_destination_atco_n:
        for j_id in cand_ids:
            f = staged_flags.get(j_id) or {}
            if f.get('dest_atco'):
                latched.add(j_id)

    _dbg_cand_dist('after_stage3_latched', latched if latched else cand_ids)

    # Stage 4: for destination-latched candidates, require origin ATCO to
    # exist in the journey and compare origin time against ARRIVAL time at
    # that stop (arrival preferred; fallback to departure).
    if latched and feed_destination_atco_n and feed_origin_atco_n and od_adj is not None:
        refined = set()
        for j_id in latched:
            try:
                jt = merged.journey_times[j_id]
            except Exception:
                continue
            try:
                stop_time = None
                for stop_int, arr_t, dep_t in jt:
                    s_atco = merged.get_atco_code(stop_int)
                    if s_atco and str(s_atco).strip() == feed_origin_atco_n:
                        stop_time = arr_t if arr_t is not None else dep_t
                        break
                if stop_time is None:
                    continue
                if abs(int(stop_time) - int(od_adj)) <= int(strict_tol):
                    refined.add(j_id)
            except Exception:
                continue
        if refined:
            latched = refined

    _dbg_cand_dist('after_stage4_refined', latched if latched else cand_ids)

    # If we latched anything, restrict candidates to latched set.
    if latched:
        candidates = [c for c in candidates if c[0] in latched]

    if DEBUG_MATCH:
        try:
            logger.info(
                "matcher(staged): line=%s op=%s origin_atco=%s dest_atco=%s od_adj=%s candidates=%d latched=%s",
                line_q,
                operator_ref,
                feed_origin_atco_n,
                feed_destination_atco_n,
                od_adj,
                len(candidates),
                sorted(list(latched)) if latched else [],
            )
        except Exception:
            pass

    if not candidates:
        return None

    # ── 2. score each candidate ──
    best_delay = None
    best_score = None
    best_jid = None
    best_start_dep = None

    # If we have a feed origin departure time (OriginAimedDepartureTime),
    # and any candidate matches it *exactly* at the journey's origin stop,
    # that is a very strong identifier. Prefer it deterministically over
    # abs(delay) scoring.
    od_exact_match_jid = None
    od_exact_match_route_int = None

    _track_cache = {}

    # Spatial gating threshold (metres): if a vehicle is further than this
    # distance from the route track, consider it off-track and skip the
    # candidate. Make this configurable via env var so operators can tune
    # for noisy GPS or coarse route geometry.
    try:
        # Reduce default to be stricter by default; we now also have a
        # "between far stops" exception below to avoid false off-track
        # rejections on long, sparse stop-to-stop legs.
        MATCH_MAX_TRACK_DIST_M = int(os.environ.get('MATCH_MAX_TRACK_DIST_M', '1200'))
    except Exception:
        MATCH_MAX_TRACK_DIST_M = 1200

    # Scoring-stage only off-track threshold (metres). This can be tuned
    # independently of the earlier route-proximity prefilter.
    try:
        MATCH_SCORE_MAX_TRACK_DIST_M = int(os.environ.get('MATCH_SCORE_MAX_TRACK_DIST_M', str(MATCH_MAX_TRACK_DIST_M)))
    except Exception:
        MATCH_SCORE_MAX_TRACK_DIST_M = MATCH_MAX_TRACK_DIST_M

    # If the vehicle is far from the track, we may still want to accept it
    # when it lies between two consecutive stops that are far apart.
    #
    # Intuition: on routes with sparse stops, the polyline built from stop
    # coordinates may under-represent the real road geometry, and a bus can
    # legitimately be "off" that simplified track while travelling between
    # two distant stops.
    try:
        MATCH_BETWEEN_STOPS_MIN_GAP_M = int(os.environ.get('MATCH_BETWEEN_STOPS_MIN_GAP_M', '800'))
    except Exception:
        MATCH_BETWEEN_STOPS_MIN_GAP_M = 800
    try:
        MATCH_BETWEEN_STOPS_MAX_PERP_M = int(os.environ.get('MATCH_BETWEEN_STOPS_MAX_PERP_M', '400'))
    except Exception:
        MATCH_BETWEEN_STOPS_MAX_PERP_M = 400

    # Configurable stricter gating thresholds (seconds/metres). These
    # default to conservative values but can be tuned via env vars.
    try:
        MATCH_ALLOW_BEFORE_S = int(os.environ.get('MATCH_ALLOW_BEFORE_S', '900'))
    except Exception:
        MATCH_ALLOW_BEFORE_S = 900
    try:
        MATCH_ALLOW_AFTER_S = int(os.environ.get('MATCH_ALLOW_AFTER_S', '900'))
    except Exception:
        MATCH_ALLOW_AFTER_S = 900
    # Destination proximity ignore threshold (metres). If a vehicle is within
    # this distance of the scheduled destination stop, treat it as effectively
    # at-terminus and skip matching it to an in-service journey.
    try:
        MATCH_DEST_IGNORE_M = int(os.environ.get('MATCH_DEST_IGNORE_M', '60'))
    except Exception:
        MATCH_DEST_IGNORE_M = 60

    for j_id, start_dep, end_arr, r_int in candidates:
        # Ensure we use the correct journey_times for this candidate.
        # Previously code relied on a possibly stale `jt` variable from
        # the candidate-collection loop which led to mixing-up scheduled
        # stop lists between candidates and produced inconsistent
        # interpolations. Fetch `jt` here explicitly.
        try:
            jt = merged.journey_times[j_id]
            if not jt:
                continue
        except Exception:
            continue

        journey_stop_set = _get_journey_stop_set(j_id, jt)
    # Get or compute track + cumulative distances. Prefer a track built from
    # the candidate's scheduled stop coordinates (the journey's `jt`). Fall
    # back to route_stops coordinates when journey stop coordinates are
    # unavailable. (legacy full-route polylines are intentionally removed.)
        if r_int not in _track_cache:
            # First: attempt to build a journey-specific track from jt
            track = []
            try:
                for sid, atime, dtime in jt:
                    try:
                        coords = _get_coords(sid)
                        if coords:
                            track.append(coords)
                    except Exception:
                        continue
            except Exception:
                track = []

            # If the journey-based track is not usable, fall back to
            # route-level stop coordinates.
            if not track:
                route_stops = merged.route_stops[r_int] if r_int < len(merged.route_stops) else []
                track = []
                for sid in route_stops:
                    try:
                        coords = _get_coords(sid)
                        if coords:
                            track.append(coords)
                    except Exception:
                        continue

            if not track:
                # Fatal gate: no route/journey track available -> skip this candidate.
                # Cache the failure to avoid repeated attempts for the same route.
                _track_cache[r_int] = None
                if DEBUG_MATCH:
                    _gate_hit('no_track')
                _dbg_gate('no_track', route_int=r_int, journey_id=j_id)
                continue
            cum = _cum_distances(track)
            _track_cache[r_int] = (track, cum)
        cached = _track_cache[r_int]
        if cached is None:
            continue
        track, cum = cached

        # Project vehicle onto track (edge-interpolated). This must run
        # both when we just built the cache and when we re-use it.
        dist_m, progress, proj_seg_idx, proj_t = _project_onto_track(lat_v, lon_v, track, cum)

        # Spatial gate: only accept candidates that can be considered
        # "on-track". We use the edge-projection distance plus a small
        # distance threshold to ensure the vehicle projects sensibly onto the
        # route geometry. This prevents matching to distant routes that
        # merely share a short line name.
        try:
            offtrack = False
            if MATCH_SCORE_MAX_TRACK_DIST_M is not None and dist_m > MATCH_SCORE_MAX_TRACK_DIST_M:
                offtrack = True

            # Exception: if we're "off-track" but plausibly between two
            # far-apart consecutive scheduled stops, do not reject.
            if offtrack and not allow_offtrack:
                try:
                    # Need a segment index for the projected edge.
                    if proj_seg_idx is not None and isinstance(proj_seg_idx, int):
                        i = int(proj_seg_idx)
                        if 0 <= i < len(track) - 1:
                            a = track[i]
                            b = track[i + 1]
                            gap_m = _hav(a[0], a[1], b[0], b[1])

                            # Perpendicular distance from point to segment AB
                            # (in local metres using the same cosine-lat scaling
                            # as the projection routine).
                            ax, ay = a
                            bx, by = b
                            cos_lat = math.cos(math.radians((ax + bx) / 2))
                            abx = (by - ay) * cos_lat * 111320.0
                            aby = (bx - ax) * 111320.0
                            apx = (lon_v - ay) * cos_lat * 111320.0
                            apy = (lat_v - ax) * 111320.0
                            ab2 = abx * abx + aby * aby
                            if ab2 > 1e-9:
                                t = (apx * abx + apy * aby) / ab2
                                t = max(0.0, min(1.0, t))
                                cx = ax + t * (bx - ax)
                                cy = ay + t * (by - ay)
                                perp_m = _hav(lat_v, lon_v, cx, cy)
                            else:
                                perp_m = dist_m

                            if gap_m >= MATCH_BETWEEN_STOPS_MIN_GAP_M and perp_m <= MATCH_BETWEEN_STOPS_MAX_PERP_M:
                                offtrack = False
                                if return_debug:
                                    try:
                                        _dbg['offtrack_between_stops_ok'] = True
                                        _dbg['between_stops_gap_m'] = float(gap_m)
                                        _dbg['between_stops_perp_m'] = float(perp_m)
                                    except Exception:
                                        pass
                except Exception:
                    pass

            if offtrack and not allow_offtrack:
                if str(os.environ.get('BUS_LIVE_PROVENANCE') or '').lower() in ('1', 'true', 'yes'):
                    logger.info("matcher: candidate j_id=%s rejected for being off-track (dist_m=%.1f > dist_thresh=%s)", j_id, dist_m, MATCH_SCORE_MAX_TRACK_DIST_M)
                if DEBUG_MATCH:
                    _gate_hit('offtrack')
                _dbg_gate('offtrack_dist', dist_m=float(dist_m), dist_thresh=float(MATCH_SCORE_MAX_TRACK_DIST_M or 0), journey_id=j_id, route_int=r_int)
                continue
        except Exception:
            # On failure of the on-track check, fall back to permissive
            # behaviour so we don't silently drop valid candidates.
            pass

        # Origin proximity check removed by request: do not reject
        # candidates solely because they appear near the route start and
        # the nearest stop differs. This makes the matcher permissive for
        # vehicles that may have slightly shifted GPS positions at origin.

        # ── exact origin departure match (highest priority) ──
        # If the feed provided an origin departure time and our staging pass
        # found an origin stop time for this journey, then an exact equality
        # should short-circuit matching.
        try:
            if od_exact_match_jid is None and od_adj is not None:
                f = staged_flags.get(j_id) or {}
                # Only trust this when the origin stop was actually identified.
                if f.get('origin_atco'):
                    try:
                        # Compute the scheduled time at the origin stop (arrival
                        # preferred; fallback to departure), mirroring staging.
                        origin_stop_time = None
                        if feed_origin_atco_n:
                            for stop_int, arr_t, dep_t in jt:
                                s_atco = merged.get_atco_code(stop_int)
                                if s_atco and str(s_atco).strip() == feed_origin_atco_n:
                                    origin_stop_time = arr_t if arr_t is not None else dep_t
                                    break
                        if origin_stop_time is not None and int(origin_stop_time) == int(od_adj):
                            od_exact_match_jid = j_id
                            od_exact_match_route_int = r_int
                            if return_debug:
                                try:
                                    _dbg['origin_dep_exact_match_jid'] = int(j_id)
                                    _dbg['origin_dep_exact_match_route_int'] = int(r_int)
                                except Exception:
                                    pass
                    except Exception:
                        pass
        except Exception:
            pass

        # ── temporal gates ──
        journey_dur = max(end_arr - start_dep, 1)

        # NOTE: 'near start but too early' gate removed by request — do not
        # reject candidates solely because they appear near the start and
        # the scheduled departure is slightly in the future. This relaxes
        # the strict early-rejection behaviour.

        # Near-end checks removed by request: do not reject candidates
        # for being slightly past the scheduled end or not near the terminus.

        # Strict time window.
        #
        # SAFE SOFT-GATE: if we have strong anchors (origin ATCO+time aligned),
        # don't hard-reject late-running services just because they're outside
        # the scheduled window by a small/medium amount. Instead, let the
        # abs(delay) scoring pick the best match.
        anchored = False
        try:
            f = staged_flags.get(j_id) or {}
            anchored = bool(f.get('origin_atco') and f.get('origin_time_aligned'))
        except Exception:
            anchored = False

        if now_seconds < start_dep - MATCH_ALLOW_BEFORE_S or now_seconds > end_arr + MATCH_ALLOW_AFTER_S:
            if not anchored:
                if str(os.environ.get('BUS_LIVE_PROVENANCE') or '').lower() in ('1', 'true', 'yes'):
                    logger.info("matcher: candidate j_id=%s outside_strict_window (start_dep=%d now=%d end_arr=%d allow_before=%d allow_after=%d)", j_id, start_dep, now_seconds, end_arr, MATCH_ALLOW_BEFORE_S, MATCH_ALLOW_AFTER_S)
                if DEBUG_MATCH:
                    _gate_hit('outside_strict_window')
                _dbg_gate(
                    'outside_time_window',
                    journey_id=j_id,
                    route_int=r_int,
                    now_seconds=int(now_seconds),
                    start_dep=int(start_dep),
                    end_arr=int(end_arr),
                    allow_before_s=int(MATCH_ALLOW_BEFORE_S),
                    allow_after_s=int(MATCH_ALLOW_AFTER_S),
                    early_by_s=int((start_dep - MATCH_ALLOW_BEFORE_S) - now_seconds) if now_seconds < start_dep - MATCH_ALLOW_BEFORE_S else 0,
                    late_by_s=int(now_seconds - (end_arr + MATCH_ALLOW_AFTER_S)) if now_seconds > end_arr + MATCH_ALLOW_AFTER_S else 0,
                )
                # Fatal: skip candidate
                continue
            else:
                if str(os.environ.get('BUS_LIVE_PROVENANCE') or '').lower() in ('1', 'true', 'yes'):
                    logger.info("matcher: candidate j_id=%s outside_strict_window_but_anchored (start_dep=%d now=%d end_arr=%d allow_before=%d allow_after=%d)", j_id, start_dep, now_seconds, end_arr, MATCH_ALLOW_BEFORE_S, MATCH_ALLOW_AFTER_S)

        # Journey fully elapsed and not near terminus — log but do not reject
        if journey_dur > 0 and now_seconds > end_arr + max(600, journey_dur * 0.5):
            if str(os.environ.get('BUS_LIVE_PROVENANCE') or '').lower() in ('1', 'true', 'yes'):
                logger.info("matcher: candidate j_id=%s fully_elapsed (now=%d end_arr=%d journey_dur=%d)", j_id, now_seconds, end_arr, journey_dur)

        # ── 3. interpolate expected_time ──
        # Prefer stop-coordinate-based progress: compute stop progress
        # fractions by projecting each scheduled stop (ATCO coords) onto
        # the journey-specific track we built above. This makes the
        # expected_time come directly from `journey_times` + ATCO coords
        # as requested. If stop-based projection fails, fall back to the
        # uniform fractional mapping across stop indices (coarse). If
        # that also fails, fall back to linear interpolation across the
        # scheduled journey duration.
        stop_progs = []
        try:
            # Attempt to compute stop progress along the current track
            # using the existing helper. This will return a list of
            # (fraction_along, sched_time) pairs sorted by fraction.
            stop_progs = _stop_progress_on_track(jt, walking, track, cum)
            # If the journey's terminal stops lacked coordinates and
            # therefore were not projected, synthesize projected stops
            # at the start (0.0) and/or end (1.0) of the track using
            # the scheduled stop times. This anchors interpolation when
            # stop metadata is incomplete.
            try:
                # First scheduled stop
                first_sid, first_atime, first_dtime = jt[0]
                first_sched = first_atime if first_atime is not None else first_dtime
                if first_sched is not None:
                    found_first = any(abs(p[1] - first_sched) <= 1 for p in stop_progs) if stop_progs else False
                    if not found_first:
                        stop_progs.append((0.0, first_sched))
                # Last scheduled stop
                last_sid, last_atime, last_dtime = jt[-1]
                last_sched = last_atime if last_atime is not None else last_dtime
                if last_sched is not None:
                    found_final = any(abs(p[1] - last_sched) <= 1 for p in stop_progs) if stop_progs else False
                    if not found_final:
                        stop_progs.append((1.0, last_sched))
                if stop_progs:
                    stop_progs.sort(key=lambda x: x[0])
            except Exception:
                # Non-fatal: if anything goes wrong here, continue and
                # let the existing fallbacks handle missing projections.
                pass
        except Exception:
            stop_progs = []

        # If we failed to build stop_progs from coordinates, fall back
        # to a simple uniform fractional mapping across stop indices.
        if not stop_progs:
            try:
                sched_times = []
                for sid, atime, dtime in jt:
                    sched = atime if atime is not None else dtime
                    if sched is None:
                        continue
                    sched_times.append(sched)
                n = len(sched_times)
                if n == 0:
                    if str(os.environ.get('BUS_LIVE_PROVENANCE') or '').lower() in ('1', 'true', 'yes'):
                        logger.info("matcher: candidate j_id=%s no scheduled stop times — cannot build stop_progs", j_id)
                else:
                    if n == 1:
                        stop_progs = [(0.0, sched_times[0])]
                    else:
                        for idx, sched in enumerate(sched_times):
                            frac = idx / (n - 1)
                            stop_progs.append((frac, sched))
            except Exception:
                if str(os.environ.get('BUS_LIVE_PROVENANCE') or '').lower() in ('1', 'true', 'yes'):
                    logger.info("matcher: candidate j_id=%s failed building uniform stop_progs", j_id)

        if stop_progs:
            if progress <= stop_progs[0][0]:
                expected_time = stop_progs[0][1]
            elif progress >= stop_progs[-1][0]:
                expected_time = stop_progs[-1][1]
            else:
                expected_time = None
                for k in range(len(stop_progs) - 1):
                    p0, t0 = stop_progs[k]
                    p1, t1 = stop_progs[k + 1]
                    if p0 <= progress <= p1:
                        seg = p1 - p0
                        frac = (progress - p0) / seg if seg > 0 else 0.0
                        expected_time = t0 + frac * (t1 - t0)
                        break

        if expected_time is None:
            # Previously we rejected candidates where interpolation
            # of an expected_time from stop progress failed. Per
            # request, make this non-fatal: log and fall back to a
            # simple linear interpolation across the scheduled journey
            # duration using `progress` as the fraction along the
            # route. This gives a coarse but usable estimate.
            if str(os.environ.get('BUS_LIVE_PROVENANCE') or '').lower() in ('1', 'true', 'yes'):
                logger.info(
                    "matcher: candidate j_id=%s no_expected_time — falling back to linear estimate",
                    j_id,
                )
            try:
                journey_span = max(end_arr - start_dep, 1)
                expected_time = int(start_dep + progress * journey_span)
            except Exception:
                if str(os.environ.get('BUS_LIVE_PROVENANCE') or '').lower() in ('1', 'true', 'yes'):
                    logger.info(
                        "matcher: candidate j_id=%s fallback linear expected_time failed — dropping",
                        j_id,
                    )
                continue

        # Reject if vehicle is within a small radius of the scheduled
        # destination stop — such vehicles are effectively at their
        # terminus and should not be presented as in-service matches.
        try:
            try:
                dest_stop_int = jt[-1][0]
                dest_coords = _get_coords(dest_stop_int)
            except Exception:
                dest_coords = None
            if dest_coords and len(dest_coords) == 2:
                if _hav(lat_v, lon_v, dest_coords[0], dest_coords[1]) <= MATCH_DEST_IGNORE_M:
                    if str(os.environ.get('BUS_LIVE_PROVENANCE') or '').lower() in ('1', 'true', 'yes'):
                        logger.info("matcher: candidate j_id=%s near_destination (dist<=%d m) — skipping", j_id, MATCH_DEST_IGNORE_M)
                    continue
        except Exception:
            # On any failure to resolve destination coords, do not
            # treat as fatal here — let scoring proceed.
            pass

        delay = int(now_seconds - expected_time)
        # Temporary detailed debug logging controlled by MATCH_DEBUG / BUS_LIVE_PROVENANCE env var.
        if DEBUG_MATCH:
            try:
                logger.info(
                    "matcher: candidate j_id=%s dist_m=%.1f prog=%.3f seg=%s seg_t=%.3f expected_time=%s delay=%d start_dep=%d end_arr=%d",
                    j_id, float(dist_m), float(progress), str(proj_seg_idx), float(proj_t), str(expected_time), int(delay), int(start_dep), int(end_arr),
                )
                # Log a short snapshot of stop_progs (at most first 6 entries)
                try:
                    logger.info("matcher: candidate j_id=%s stop_progs=%s", j_id, repr(stop_progs[:6]))
                except Exception:
                    pass
                # Also print to stdout so scripts/debug_match.py captures it
                try:
                    print(f"matcher: candidate j_id={j_id} dist_m={float(dist_m):.1f} prog={float(progress):.3f} seg={proj_seg_idx} seg_t={float(proj_t):.3f} expected_time={expected_time} delay={int(delay)} start_dep={int(start_dep)} end_arr={int(end_arr)}")
                    try:
                        print("matcher: stop_progs:", repr(stop_progs[:6]))
                    except Exception:
                        pass
                except Exception:
                    pass
            except Exception:
                pass
        # End of candidate processing

        # ── sanity clamp: reject absurd delays ──
        # For a 60-min journey, allow at most 30 min delay (or 20 min
        # minimum).  The old threshold of max(dur*1.5, 3600) was far too
        # generous and let 60+ min "delays" through.
        # Use a looser plausibility clamp (1.5× journey duration, min 3600s)
        # so legitimately late services are not rejected. Log only under provenance.
        max_plausible = max(int(journey_dur * 1.5), 3600)
        if abs(delay) > max_plausible:
            # Treat absurd delays as a mismatch by default — skip this
            # candidate so we don't present extremely large/implausible
            # delays to the UI. When `allow_abs_delay` is True (used by the
            # background recompute), accept large delays as valid.
            if not allow_abs_delay:
                if str(os.environ.get('BUS_LIVE_PROVENANCE') or '').lower() in ('1', 'true', 'yes'):
                    logger.info(
                        "matcher: candidate j_id=%s abs_delay_too_big -> skipping (delay=%d max_plausible=%d)",
                        j_id, delay, max_plausible,
                    )
                _dbg_gate(
                    'abs_delay_clamp',
                    journey_id=j_id,
                    route_int=r_int,
                    delay_s=int(delay),
                    abs_delay_s=int(abs(delay)),
                    max_plausible_s=int(max_plausible),
                    journey_dur_s=int(journey_dur),
                )
                continue

    # ── deterministic, time-stable scoring ──
        # Primary: distance to track (lower = better).
        # Secondary: absolute delay – the best match is the journey whose
        #   interpolated expected_time is closest to now.  This is critical
        #   when multiple journeys share the same route (same dist_m): the
        #   old j_id tiebreaker picked the *earliest* departure, producing
        #   huge phantom delays.
        # Tertiary: j_id for absolute determinism when everything ties.
        # Soft preference: if we have a feed origin time, prefer candidates
        # where the journey's origin-stop time aligned within strict_tol.
        # This is *not* fatal — we still allow selection based on smallest
        # abs(delay) when vehicles are very late / the feed origin time is
        # unreliable.
        origin_penalty = 0
        try:
            if origin_dep_secs is not None:
                f = staged_flags.get(j_id) or {}
                if not f.get('origin_time_aligned'):
                    origin_penalty = int(strict_tol) * 2
        except Exception:
            origin_penalty = 0

        score = (dist_m, abs(delay) + origin_penalty, j_id)

        if best_score is None or score < best_score:
            best_score = score
            best_delay = delay
            best_jid = j_id
            best_start_dep = start_dep

    # If we found an exact origin departure match, return it immediately.
    # We still need a delay; derive it from the matched journey's schedule
    # using the same interpolation logic via a small, single-candidate rerun
    # of the scoring loop.
    if od_exact_match_jid is not None:
        # Fast-path: if we already computed it as best_jid, just return.
        if best_jid == od_exact_match_jid:
            if best_delay is not None and best_delay < 0:
                best_delay = 0
            if return_jid:
                return _log_gate_summary((best_delay, int(od_exact_match_route_int or -1)))
            return _log_gate_summary(best_delay)

        # Otherwise, recompute delay for just the matched journey.
        try:
            only = [(c[0], c[1], c[2], c[3]) for c in candidates if c[0] == od_exact_match_jid]
        except Exception:
            only = []
        if only:
            # Reset best_* and reuse the same per-candidate logic by running
            # the loop body over a 1-item list. This keeps behaviour aligned.
            best_delay = None
            best_score = None
            best_jid = None
            best_start_dep = None
            for j_id, start_dep, end_arr, r_int in only:
                try:
                    jt = merged.journey_times[j_id]
                    if not jt:
                        continue
                except Exception:
                    continue

                # Reuse cached track if present; otherwise compute.
                journey_stop_set = _get_journey_stop_set(j_id, jt)
                if r_int not in _track_cache:
                    track = []
                    try:
                        for sid, atime, dtime in jt:
                            try:
                                coords = _get_coords(sid)
                                if coords:
                                    track.append(coords)
                            except Exception:
                                continue
                    except Exception:
                        track = []
                    if not track:
                        route_stops = merged.route_stops[r_int] if r_int < len(merged.route_stops) else []
                        track = []
                        for sid in route_stops:
                            try:
                                coords = _get_coords(sid)
                                if coords:
                                    track.append(coords)
                            except Exception:
                                continue
                    if not track:
                        continue
                    cum = _cum_distances(track)
                    _track_cache[r_int] = (track, cum)
                cached = _track_cache.get(r_int)
                if not cached:
                    continue
                track, cum = cached

                dist_m, progress, proj_seg_idx, proj_t = _project_onto_track(lat_v, lon_v, track, cum)
                journey_dur = max(end_arr - start_dep, 1)

                # Build stop_progs (same as main loop)
                stop_progs = []
                try:
                    stop_progs = _stop_progress_on_track(jt, walking, track, cum)
                    try:
                        first_sid, first_atime, first_dtime = jt[0]
                        first_sched = first_atime if first_atime is not None else first_dtime
                        if first_sched is not None:
                            found_first = any(abs(p[1] - first_sched) <= 1 for p in stop_progs) if stop_progs else False
                            if not found_first:
                                stop_progs.append((0.0, first_sched))
                        last_sid, last_atime, last_dtime = jt[-1]
                        last_sched = last_atime if last_atime is not None else last_dtime
                        if last_sched is not None:
                            found_final = any(abs(p[1] - last_sched) <= 1 for p in stop_progs) if stop_progs else False
                            if not found_final:
                                stop_progs.append((1.0, last_sched))
                        if stop_progs:
                            stop_progs.sort(key=lambda x: x[0])
                    except Exception:
                        pass
                except Exception:
                    stop_progs = []
                if not stop_progs:
                    try:
                        sched_times = []
                        for sid, atime, dtime in jt:
                            sched = atime if atime is not None else dtime
                            if sched is None:
                                continue
                            sched_times.append(sched)
                        n = len(sched_times)
                        if n == 1:
                            stop_progs = [(0.0, sched_times[0])]
                        elif n > 1:
                            for idx, sched in enumerate(sched_times):
                                frac = idx / (n - 1)
                                stop_progs.append((frac, sched))
                    except Exception:
                        stop_progs = []

                expected_time = None
                if stop_progs:
                    if progress <= stop_progs[0][0]:
                        expected_time = stop_progs[0][1]
                    elif progress >= stop_progs[-1][0]:
                        expected_time = stop_progs[-1][1]
                    else:
                        for k in range(len(stop_progs) - 1):
                            p0, t0 = stop_progs[k]
                            p1, t1 = stop_progs[k + 1]
                            if p0 <= progress <= p1:
                                seg = p1 - p0
                                frac = (progress - p0) / seg if seg > 0 else 0.0
                                expected_time = t0 + frac * (t1 - t0)
                                break

                if expected_time is None:
                    try:
                        journey_span = max(end_arr - start_dep, 1)
                        expected_time = int(start_dep + progress * journey_span)
                    except Exception:
                        continue

                delay = int(now_seconds - expected_time)
                best_delay = delay
                best_jid = j_id
                best_start_dep = start_dep
                break

        if best_delay is not None and best_delay < 0:
            best_delay = 0
        if return_jid:
            return _log_gate_summary((best_delay, int(od_exact_match_route_int or -1)))
        return _log_gate_summary(best_delay)

    # If we never found a candidate that passed gates/scoring, return no match.
    if best_jid is None:
        _dbg_gate('no_candidate_survived')
        return _log_gate_summary((None, None) if return_jid else None)

    # If the chosen journey's scheduled start time is more than 30 minutes in the future,
    # reject the bus entirely (it likely mapped incorrectly or shouldn't be tracked yet).
    if best_start_dep is not None and best_start_dep - now_seconds > 1800:
        if str(os.environ.get('BUS_LIVE_PROVENANCE') or '').lower() in ('1', 'true', 'yes'):
            logger.info(
                "matcher: rejecting matched bus for journey %s because its start_dep %d is > 30 mins in future",
                best_jid,
                best_start_dep,
            )
        return _log_gate_summary((None, None) if return_jid else None)

    # Symmetric guard: if the chosen journey's scheduled start time is too far
    # in the past (default 4h), reject it. This prevents stale previous
    # journeys from being matched when a line name is ambiguous.
    try:
        MATCH_REJECT_PAST_START_S = int(os.environ.get('MATCH_REJECT_PAST_START_S', str(4 * 3600)))
    except Exception:
        MATCH_REJECT_PAST_START_S = 4 * 3600
    try:
        if best_start_dep is not None and now_seconds - int(best_start_dep) > int(MATCH_REJECT_PAST_START_S):
            if str(os.environ.get('BUS_LIVE_PROVENANCE') or '').lower() in ('1', 'true', 'yes'):
                logger.info(
                    "matcher: rejecting matched bus for journey %s because its start_dep %d is > %ds in the past",
                    best_jid,
                    best_start_dep,
                    MATCH_REJECT_PAST_START_S,
                )
            return _log_gate_summary((None, None) if return_jid else None)
    except Exception:
        pass

    # Clamp negative delays to zero at the source of computation so
    # callers don't have to special-case early/negative values.
    if best_delay is not None and best_delay < 0:
        best_delay = 0
    if return_jid:
        try:
            r_int = -1
            try:
                r_int = merged.journey_to_route[best_jid] if best_jid is not None else -1
            except Exception:
                r_int = -1
            return _log_gate_summary((best_delay, r_int))
        except Exception:
            return _log_gate_summary((best_delay, -1))
    return _log_gate_summary(best_delay)


# ─────────────────────────────────────────────────────────────────────────────
# Live-match instability suppression
#
# If a vehicle is repeatedly matched to many different timetable journeys in a
# short window, the match is likely unstable (e.g. GPS jitter, ambiguous line
# names, or missing operator scoping). In that case we prefer to suppress the
# match rather than present a rapidly flipping delay/route.
#
# This helper is intentionally small + testable and is used by provenance tests.
_LIVE_MATCH_HISTORY: dict[str, tuple[float, set[int]]] = {}


def _live_vehicle_should_suppress_match(vehicle_key: str, journey_id: int) -> bool:
    """Return True if we should suppress the current match as unstable.

    Rule (configurable by env vars):
    - Maintain an in-memory set of distinct `journey_id`s per `vehicle_key`.
    - If the number of distinct journeys exceeds BUS_LIVE_MAX_DISTINCT_MATCHES
      within BUS_LIVE_MATCH_HISTORY_TTL_S seconds, return True.
    - TTL expiry resets the history.

    This is best-effort; on any error it returns False (do not suppress).
    """
    try:
        import time

        try:
            ttl_s = int(os.environ.get('BUS_LIVE_MATCH_HISTORY_TTL_S', '120'))
        except Exception:
            ttl_s = 120
        try:
            max_distinct = int(os.environ.get('BUS_LIVE_MAX_DISTINCT_MATCHES', '3'))
        except Exception:
            max_distinct = 3

        now = time.time()
        key = str(vehicle_key or '').strip()
        if not key:
            return False

        rec = _LIVE_MATCH_HISTORY.get(key)
        if not rec or ttl_s <= 0 or (now - float(rec[0])) > float(ttl_s):
            s: set[int] = set()
            _LIVE_MATCH_HISTORY[key] = (now, s)
        else:
            s = rec[1]

        try:
            jid = int(journey_id)
        except Exception:
            return False
        s.add(jid)

        return len(s) > int(max_distinct)
    except Exception:
        return False


def _stage_filter_journeys_for_live_bus(
    merged,
    walking,
    *,
    line_ref: str,
    operator_ref: str | None = None,
    dest: str | None = None,
    feed_origin_atco: str | None = None,
    feed_destination_atco: str | None = None,
    origin_dep_secs: int | None = None,
    origin_tz_offset_secs: int = 0,
    strict_tol: int = 600,
    return_debug: bool = False,
):
    """Return candidate journey ids after applying the staged ATCO/time latch.

    This helper isolates the *new rule* from the rest of the delay matcher
    (spatial projection + delay scoring). It's used by tests and can be used
    by provenance tooling.

    Inputs are intentionally similar to _compute_delay_from_timetable, but it
    requires a preloaded `merged` + `walking`.
    """
    line_q = (line_ref or "").strip()
    dest_q = (dest or "").strip().lower() if dest else ""
    try:
        feed_origin_atco_n = str(feed_origin_atco).strip() if feed_origin_atco else None
    except Exception:
        feed_origin_atco_n = None
    try:
        feed_destination_atco_n = str(feed_destination_atco).strip() if feed_destination_atco else None
    except Exception:
        feed_destination_atco_n = None

    od_adj = None
    if origin_dep_secs is not None:
        try:
            od_adj = int(origin_dep_secs) + int(origin_tz_offset_secs or 0)
        except Exception:
            try:
                od_adj = int(origin_dep_secs)
            except Exception:
                od_adj = None

    # stop name map for weak destination matching.
    stop_name_map = {}
    try:
        for si, sname in enumerate(getattr(merged, 'stop_metadata', []) or []):
            if not sname:
                continue
            stop_name_map.setdefault(str(sname).strip().lower(), []).append(si)
    except Exception:
        stop_name_map = {}

    def _journey_start_dep_seconds(j_id: int) -> int | None:
        """Best-effort start departure seconds for a journey.

        We intentionally treat negative values as yesterday-shifted and
        positive values as today-shifted (in the AM merged timetable).
        """
        try:
            jt = merged.journey_times[j_id]
            if not jt:
                return None
            # Each entry is (stop_int, arr_t, dep_t)
            _, arr_t, dep_t = jt[0]
            t0 = dep_t if dep_t is not None else arr_t
            return int(t0) if t0 is not None else None
        except Exception:
            return None

    def _dist_snapshot(j_ids: list[int] | set[int], *, label: str, max_examples: int = 5) -> dict:
        neg: list[int] = []
        pos: list[int] = []
        unknown: list[int] = []
        for jid in j_ids:
            sd = _journey_start_dep_seconds(jid)
            if sd is None:
                unknown.append(jid)
            elif sd < 0:
                neg.append(jid)
            else:
                pos.append(jid)
        # Keep examples tiny but deterministic.
        neg_ex = sorted(neg)[:max_examples]
        pos_ex = sorted(pos)[:max_examples]
        unk_ex = sorted(unknown)[:max_examples]
        snap = {
            'label': label,
            'n': len(list(j_ids)),
            'neg_n': len(neg),
            'pos_n': len(pos),
            'unknown_n': len(unknown),
            'neg_examples': neg_ex,
            'pos_examples': pos_ex,
            'unknown_examples': unk_ex,
        }
        # Include basic range if available.
        try:
            times = [t for t in (_journey_start_dep_seconds(jid) for jid in j_ids) if t is not None]
            if times:
                snap['start_dep_min'] = int(min(times))
                snap['start_dep_max'] = int(max(times))
        except Exception:
            pass
        return snap

    debug = None
    if return_debug:
        debug = {
            'line_ref': line_ref,
            'operator_ref': operator_ref,
            'dest_q': (dest or ''),
            'feed_origin_atco': feed_origin_atco,
            'feed_destination_atco': feed_destination_atco,
            'origin_dep_secs': origin_dep_secs,
            'origin_tz_offset_secs': origin_tz_offset_secs,
            'od_adj': od_adj,
            'strict_tol': strict_tol,
            'stages': [],
        }

    candidates: list[int] = []
    staged_flags: dict[int, dict] = {}

    for j_id, jmeta in enumerate(getattr(merged, 'journey_metadata', []) or []):
        if not jmeta:
            continue
        line_name = jmeta.get('line_name') or ''
        simple_line = line_name.split(':')[-1] if line_name else ''
        if line_q and simple_line != line_q:
            continue

        # Operator filter (same approach as main matcher)
        if operator_ref:
            op_noc = jmeta.get('operator_national_code') or None
            svc = None
            if op_noc:
                svc = str(op_noc).strip()
            else:
                sc = jmeta.get('service_code') or ''
                if sc:
                    svc = str(sc).strip()
                else:
                    ln = (jmeta.get('line_name') or '')
                    if ':' in ln:
                        svc = ln.split(':')[0]
            if not svc or svc.strip() != str(operator_ref).strip():
                continue

        try:
            jt = merged.journey_times[j_id]
            if not jt:
                continue
        except Exception:
            continue

        # Endpoint ATCO sets (same as main: a few stops from ends)
        dest_atcos = []
        origin_atcos = []
        try:
            for i in range(1, min(4, len(jt) + 1)):
                atco = merged.get_atco_code(jt[-i][0])
                if atco:
                    dest_atcos.append(str(atco).strip())
            for i in range(min(3, len(jt))):
                atco = merged.get_atco_code(jt[i][0])
                if atco:
                    origin_atcos.append(str(atco).strip())
        except Exception:
            pass

        origin_atco_match = bool(feed_origin_atco_n and feed_origin_atco_n in origin_atcos)
        dest_atco_match = bool(feed_destination_atco_n and feed_destination_atco_n in dest_atcos)

        weak_dest_text_match = False
        if not feed_destination_atco_n and dest_q:
            try:
                candidate_dest_atcos = set()
                for sname_key, sidx_list in stop_name_map.items():
                    if dest_q in sname_key or sname_key in dest_q:
                        for si in sidx_list:
                            atco = merged.get_atco_code(si)
                            if atco:
                                candidate_dest_atcos.add(str(atco).strip())
                if candidate_dest_atcos and any(da in candidate_dest_atcos for da in dest_atcos):
                    weak_dest_text_match = True
            except Exception:
                weak_dest_text_match = False

        origin_time_aligned = False
        if origin_atco_match and od_adj is not None:
            try:
                for stop_int, arr_t, dep_t in jt:
                    s_atco = merged.get_atco_code(stop_int)
                    if s_atco and str(s_atco).strip() == feed_origin_atco_n:
                        stop_time = arr_t if arr_t is not None else dep_t
                        if stop_time is not None and abs(int(stop_time) - int(od_adj)) <= int(strict_tol):
                            origin_time_aligned = True
                        break
            except Exception:
                origin_time_aligned = False

        staged_flags[j_id] = {
            'origin_atco': origin_atco_match,
            'dest_atco': dest_atco_match,
            'weak_dest_text': weak_dest_text_match,
            'origin_time_aligned': origin_time_aligned,
        }

        candidates.append(j_id)

    if not candidates:
        if return_debug:
            debug['stages'].append({'label': 'after_scan', 'n': 0})
            return ([], debug)
        return []

    if return_debug:
        debug['stages'].append(_dist_snapshot(candidates, label='after_scan'))

    latched: set[int] = set()

    # Stage 2 latch: origin ATCO + origin start time match.
    if feed_origin_atco_n and od_adj is not None:
        aligned = set()
        origin_only = set()
        for j_id in candidates:
            f = staged_flags.get(j_id) or {}
            if f.get('origin_atco'):
                origin_only.add(j_id)
                if f.get('origin_time_aligned'):
                    aligned.add(j_id)
        if return_debug:
            debug['stages'].append(_dist_snapshot(origin_only, label='stage2_origin_only'))
            debug['stages'].append(_dist_snapshot(aligned, label='stage2_aligned'))
        # If we have any aligned origin matches, they win outright.
        if aligned:
            latched = aligned
        # Otherwise, restrict to journeys that at least contain the origin ATCO.
        elif origin_only:
            latched = origin_only

    if return_debug:
        debug['stages'].append(_dist_snapshot(latched if latched else candidates, label='after_stage2_latch_effective'))

    # Stage 3 latch: destination ATCO (for those not already latched)
    if not latched and feed_destination_atco_n:
        for j_id in candidates:
            f = staged_flags.get(j_id) or {}
            if f.get('dest_atco'):
                latched.add(j_id)

    if return_debug:
        debug['stages'].append(_dist_snapshot(latched if latched else candidates, label='after_stage3_latch_effective'))

    # Stage 4: for destination-latched candidates, require origin ATCO to exist
    # and compare origin time against the arrival/departure time at that stop.
    if latched and feed_destination_atco_n and feed_origin_atco_n and od_adj is not None:
        refined: set[int] = set()
        for j_id in latched:
            try:
                jt = merged.journey_times[j_id]
            except Exception:
                continue
            stop_time = None
            try:
                for stop_int, arr_t, dep_t in jt:
                    s_atco = merged.get_atco_code(stop_int)
                    if s_atco and str(s_atco).strip() == feed_origin_atco_n:
                        stop_time = arr_t if arr_t is not None else dep_t
                        break
            except Exception:
                stop_time = None
            if stop_time is None:
                continue
            try:
                if abs(int(stop_time) - int(od_adj)) <= int(strict_tol):
                    refined.add(j_id)
            except Exception:
                continue
        if refined:
            latched = refined

    if return_debug:
        debug['stages'].append(_dist_snapshot(latched if latched else candidates, label='after_stage4_refine_effective'))

    result = sorted(latched) if latched else sorted(candidates)
    if return_debug:
        return (result, debug)
    return result


def _diagnose_match_failure(line_ref, dest, lat_v, lon_v, origin_dep_secs=None, operator_ref=None, strict_tol: int = 600, feed_origin_atco: str = None, feed_destination_atco: str = None, origin_tz_offset_secs: int = 0):
    """Return a list of reject reasons explaining why strict matching failed.

    This runs a sequence of checks that mirror the strict matcher's gates but
    returns human-readable rejection codes rather than performing full scoring.
    """
    reasons = []
    try:
        from datetime import datetime
        today = datetime.now().date().isoformat()
        now = datetime.now()
        now_seconds = now.hour * 3600 + now.minute * 60 + now.second
        merged, router, walking = get_router_for_date(today, start_time=now_seconds, apply_delay=False)
    except Exception:
        return ["no_merged_data"]

    line_q = (line_ref or "").strip()
    simple_line = line_q.split(":")[-1].strip()
    dest_q = (dest or "").strip().lower()

    # 1) line candidates
    line_candidates = []
    for j_id, jm in enumerate(merged.journey_metadata):
        if not jm:
            continue
        ln = (jm.get("line_name") or "")
        if ln.split(":")[-1].strip() == simple_line:
            line_candidates.append((j_id, jm))
    if not line_candidates:
        reasons.append("no_line_match")
        return reasons

    # 2 & 4) destination AND/OR origin ATCO checks (unified)
    # We require at least one match for either destination or origin across all candidates.
    dest_ok = False
    dest_matches = []
    if feed_destination_atco:
        for j_id, jm in line_candidates:
            try:
                jt = merged.journey_times[j_id]
                dest_atcos = []
                for i in range(1, min(4, len(jt) + 1)):
                    atco = merged.get_atco_code(jt[-i][0])
                    if atco: dest_atcos.append(str(atco).strip())
            except Exception:
                dest_atcos = []
            if str(feed_destination_atco).strip() in dest_atcos:
                dest_matches.append((j_id, jm))
                dest_ok = True
    else:
        stop_name_map = {}
        try:
            for si, sname in enumerate(merged.stop_metadata or []):
                if not sname:
                    continue
                key = str(sname).strip().lower()
                stop_name_map.setdefault(key, []).append(si)
        except Exception:
            stop_name_map = {}
        candidate_dest_atcos = set()
        if dest_q:
            for sname_key, sidx_list in stop_name_map.items():
                if dest_q in sname_key or sname_key in dest_q:
                    for si in sidx_list:
                        atco = merged.get_atco_code(si)
                        if atco:
                            candidate_dest_atcos.add(str(atco).strip())
        if candidate_dest_atcos:
            for j_id, jm in line_candidates:
                try:
                    jt = merged.journey_times[j_id]
                    dest_atcos = []
                    for i in range(1, min(4, len(jt) + 1)):
                        atco = merged.get_atco_code(jt[-i][0])
                        if atco: dest_atcos.append(str(atco).strip())
                except Exception:
                    dest_atcos = []
                if any(da in candidate_dest_atcos for da in dest_atcos):
                    dest_ok = True
                    break

    origin_ok = False
    for j_id, jm in line_candidates:
        try:
            jt = merged.journey_times[j_id]
            origin_atcos = []
            for i in range(min(3, len(jt))):
                atco = merged.get_atco_code(jt[i][0])
                if atco: origin_atcos.append(str(atco).strip())
        except Exception:
            continue
        if feed_origin_atco:
            if str(feed_origin_atco).strip() in origin_atcos:
                origin_ok = True
                break
        else:
            try:
                nearby = walking.reachable_stops((lat_v, lon_v))
                if not nearby:
                    continue
                nearest_stop_int, walk_secs = nearby[0]
                nearest_atco = merged.get_atco_code(nearest_stop_int)
                if nearest_atco and str(nearest_atco).strip() in origin_atcos:
                    origin_ok = True
                    break
            except Exception:
                continue

    if not dest_ok and not origin_ok:
        reasons.append("destination_and_origin_atco_mismatch")
        return reasons

    # 3) operator/service check — prefer authoritative operator_national_code
    # when present, otherwise fall back to service_code or line prefix.
    if operator_ref:
        matched_op = False
        for j_id, jm in line_candidates:
            # Prefer operator_national_code (added by loader) for strict matching
            op_noc = jm.get("operator_national_code") or ""
            if op_noc and str(op_noc).strip() == str(operator_ref).strip():
                matched_op = True
                break
            # Fall back to service_code
            svc = jm.get("service_code") or ""
            if not svc:
                ln = (jm.get("line_name") or "")
                if ":" in ln:
                    svc = ln.split(":")[0]
            if svc and str(svc).strip() == str(operator_ref).strip():
                matched_op = True
                break
        if not matched_op:
            reasons.append("operator_mismatch")
            return reasons

    # 5) origin_dep_secs (fatal)
    if origin_dep_secs is None:
        # Treat missing OriginAimedDepartureTime as a fatal rejection: the
        # authoritative feed did not include the journey's aimed departure time
        # so we cannot confidently match this vehicle to a scheduled journey.
        reasons.append("missing_origin_dep")
        return reasons

    # When origin_dep_secs is present, attempt to align to scheduled
    # start_dep using the feed-provided timezone offset (if available)
    # and ±1 day shifts. This avoids crude ±1h heuristics and uses the
    # exact offset supplied in the ISO datetime.
    try:
        od = int(origin_dep_secs)
    except Exception:
        od = origin_dep_secs
    tz_off = int(origin_tz_offset_secs or 0)
    within_tol = False
    # Align using timezone offset only; do not attempt ±1 day shifts here.
    for j_id, jm in line_candidates:
        try:
            jt = merged.journey_times[j_id]
            start_dep = jt[0][2]
            
            origin_match = False
            if feed_origin_atco:
                first_stop_int = jt[0][0]
                journey_first_atco = merged.get_atco_code(first_stop_int)
                if journey_first_atco and str(journey_first_atco).strip() == str(feed_origin_atco).strip():
                    origin_match = True
            
            if not origin_match and feed_origin_atco:
                for stop_int, arr_t, dep_t in jt:
                    s_atco = merged.get_atco_code(stop_int)
                    if s_atco and str(s_atco).strip() == str(feed_origin_atco).strip():
                        if dep_t is not None:
                            start_dep = dep_t
                        elif arr_t is not None:
                            start_dep = arr_t
                        break
        except Exception:
            continue
        try:
            adj = od + int(tz_off)
        except Exception:
            adj = od
        try:
            if abs(start_dep - adj) <= int(strict_tol):
                within_tol = True
                break
        except Exception:
            continue
    if not within_tol:
        reasons.append("origin_dep_out_of_tolerance")
        return reasons

    # If we reached here, the failure must be spatial/temporal gating deeper in matcher
    reasons.append("spatial_or_temporal_gate_failed")
    return reasons


# ── Live delay cache for journey planning ────────────────────────
# Fetches all live bus positions once, caches for a short window, and
# looks up delays by line name.  Used by build_journey_plan_response()
# to annotate bus legs with real-time information.
_live_delay_cache: Dict[str, Any] = {"ts": 0.0, "data": []}
_LIVE_DELAY_TTL = 30  # seconds

# Live bus endpoint cache. This endpoint can be polled frequently by the
# frontend; fetching and parsing SIRI XML plus running timetable matching is
# expensive. Cache raw feed results briefly to reduce load.



def _fetch_all_live_buses() -> list:
    """Return all live bus records from all operators (cached)."""
    if _live_endpoints_disabled():
        return []
    import time as _time
    now = _time.time()
    if now - _live_delay_cache["ts"] < _LIVE_DELAY_TTL and _live_delay_cache["data"]:
        return _live_delay_cache["data"]
    try:
        from bus_live import BusLive
        bl = BusLive(timeout=20)
        # Fetch with a very wide bounding box to get everything
        results = bl.get_bus_live(54.0, -2.8, lat_tol=2.0, lon_tol=2.0)
        _live_delay_cache["data"] = results
        _live_delay_cache["ts"] = now
        return results
    except Exception:
        return _live_delay_cache["data"]  # stale is better than nothing


def _get_live_delay_for_line(line_name: str) -> Optional[int]:
    """Look up the current delay (seconds) for a bus line from live feeds.

    Queries the cached live-bus positions, filters by exact short line name,
    and computes delay.  For vehicles that have a feed-supplied delay, we
    use that directly.  For vehicles without feed delay, we call the
    timetable-matching algorithm.

    Returns the median delay (seconds) across all matching live vehicles,
    or None if no live vehicle is found for the line.
    """
    if not line_name:
        return None
    line_q = line_name.strip()
    buses = _fetch_all_live_buses()
    delays: list[int] = []
    for item in buses:
        # support optional trailing metadata dict as added by bus_live
        meta = {}
        base = item
        try:
            if isinstance(item[-1], dict):
                meta = item[-1]
                base = item[:-1]
        except Exception:
            base = item

        if len(base) == 7:
            line_ref, dest, lat_v, lon_v, _op, delay_s, origin_dep = base
        else:
            line_ref, dest, lat_v, lon_v, _op, delay_s, origin_dep, _bearing = base
        # Exact short line name match
        short = (line_ref or "").split(":")[-1].strip()
        if short != line_q:
            continue
        if delay_s is not None:
            delays.append(delay_s)
        else:
            try:
                # Prefer feed-supplied operator code when available in metadata
                op_for_match = meta.get('operator_ref') if meta and isinstance(meta, dict) and meta.get('operator_ref') else _op
                computed = _compute_delay_from_timetable(
                    line_ref, dest, lat_v, lon_v,
                    origin_dep_secs=origin_dep,
                    origin_tz_offset_secs=(meta.get('origin_tz_offset_s') if meta and isinstance(meta, dict) else 0),
                    operator_ref=op_for_match,
                    strict_tol=600,
                    feed_origin_atco=(meta.get('origin_atco') if meta and isinstance(meta, dict) else None),
                    feed_destination_atco=(meta.get('destination_atco') if meta and isinstance(meta, dict) else None),
                )
                if computed is not None:
                    delays.append(computed)
            except Exception:
                pass
    if not delays:
        return None
    # Use median to avoid outlier skew
    delays.sort()
    mid = len(delays) // 2
    return delays[mid]


@app.get("/bus/live/{operator}")
async def bus_live_operator(
    operator: str,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    # Default tolerance is tight so unit tests and client expectations match.
    # Can be overridden by callers when they want a wider scan.
    latTol: float = 0.0003,
    lonTol: float = 0.0003,
    keep_vehicle_id: Optional[str] = None,
):
    """Get live bus positions for a specific operator."""
    from fastapi.responses import JSONResponse

    if _live_endpoints_disabled():
        return []

    # If routing is active, serve a recent last-known snapshot instead of an
    # empty list so the UI remains responsive while routing computations run.
    # Key on operator + coarse location/tolerance params.
    try:
        SNAPSHOT_TTL_S = float(os.environ.get('BUS_LIVE_SNAPSHOT_TTL_S', '30'))
    except Exception:
        SNAPSHOT_TTL_S = 30.0

    # If we're currently doing a heavy routing build, avoid doing any extra
    # work here (including any lazy router init used for stop-name matching).
    # But keep the UI alive by returning a recent snapshot when available.
    snapshot_key = (
        str(operator).lower(),
        # round to reduce key explosion from tiny map movements
        round(lat or 0.0, 4),
        round(lon or 0.0, 4),
        round(float(latTol or 0.0), 4),
        round(float(lonTol or 0.0), 4),
        str(keep_vehicle_id) if keep_vehicle_id else None,
    )
    try:
        if is_routing_active():
            snap = _bus_live_snapshot_get(snapshot_key, SNAPSHOT_TTL_S)
            if snap is not None:
                return snap
            return []
    except Exception:
        pass

    if lat is None or lon is None:
        return JSONResponse(
            status_code=400,
            content={"error": "lat and lon are required"},
        )

    urls = None
    if operator.lower() != "all":
        urls = [f"https://transport.scc.lancs.ac.uk/bus/live/{operator}"]

    try:
        results = get_bus_live(
            lat,
            lon,
            urls=urls,
            lat_tol=latTol,
            lon_tol=lonTol,
            keep_vehicle_id=keep_vehicle_id,
        )
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})

    # Store last-known snapshot for use during routing-active windows.
    try:
        if isinstance(results, list):
            _bus_live_snapshot_set(snapshot_key, results)
    except Exception:
        pass

    out = []
    # Enable provenance/debug fields when BUS_LIVE_PROVENANCE is set in the env.
    bus_provenance = str(os.environ.get('BUS_LIVE_PROVENANCE') or '').lower() in ('1', 'true', 'yes')
    # Maximum visible delay (seconds) for UI and persisted journey map.
    # Vehicles/journeys with delays greater than this are treated as
    # absent for display and not persisted during recompute.
    try:
        MAX_VISIBLE_DELAY_S = int(os.environ.get('MAX_VISIBLE_DELAY_S', '1200'))
    except Exception:
        MAX_VISIBLE_DELAY_S = 1200
    # Optional destination-name stop proximity filter.
    # This is fuzzy (depends on free-text destination) and can be expensive.
    # Disabled by default; re-enable with ENABLE_STOP_NAME_MATCH=1.
    try:
        ENABLE_STOP_NAME_MATCH = str(os.environ.get('ENABLE_STOP_NAME_MATCH') or '').lower() in ('1', 'true', 'yes')
    except Exception:
        ENABLE_STOP_NAME_MATCH = False

    # Option A: embed precomputed route track coords in /bus/live responses.
    # This shifts geometry work to refresh, making click-to-show-track instant.
    try:
        BUS_LIVE_INCLUDE_TRACKS = str(os.environ.get('BUS_LIVE_INCLUDE_TRACKS') or '').lower() in ('1', 'true', 'yes')
    except Exception:
        BUS_LIVE_INCLUDE_TRACKS = False

    _stop_name_map = None
    if ENABLE_STOP_NAME_MATCH:
        try:
            from datetime import datetime as _dt
            today_date = _dt.now().date().isoformat()
            now_secs = _dt.now().hour * 3600 + _dt.now().minute * 60 + _dt.now().second
            _merged, _rtr, _walking = get_router_for_date(today_date, start_time=now_secs, apply_delay=False)
            _stop_name_map = {}
            for si, sname in enumerate(_merged.stop_metadata or []):
                try:
                    if not sname:
                        continue
                    key = str(sname).strip().lower()
                    coords = _walking.get_loc_coords(si)
                    if coords and len(coords) == 2:
                        _stop_name_map.setdefault(key, []).append((si, coords[0], coords[1]))
                except Exception:
                    continue
        except Exception:
            _stop_name_map = None
    for item in results:
        # Support legacy 7-tuples and 8-tuples (with bearing) and the
        # extended shape where a final dict contains metadata (vehicle/journey ids).
        meta = {}
        base = item
        try:
            # If the last element is a dict, treat it as metadata
            if isinstance(item[-1], dict):
                meta = item[-1]
                base = item[:-1]
        except Exception:
            base = item

        # Now base is either length 7 (no bearing) or 8 (with bearing)
        if len(base) == 7:
            line_ref, dest, lat_v, lon_v, _operator, delay_s, origin_dep = base
            bearing = None
        else:
            line_ref, dest, lat_v, lon_v, _operator, delay_s, origin_dep, bearing = base
        computed = None
        matched_route_int = None
        # Diagnostic fields only computed when bus_provenance is enabled
        reject_reasons = None
        match_reason = None
        matcher_dbg = None

        # Always attempt timetable matching so we can attach stable journey/route
        # mapping fields (route_int + match_reason) even when the feed already
        # provides a delay. The frontend uses this to decide whether a bus is
        # "mapped" (and thus colored) per-refresh.
        try:
            # Prefer feed-supplied operator code when available in metadata
            op_for_match = meta.get('operator_ref') if meta and isinstance(meta, dict) and meta.get('operator_ref') else _operator
            # Normalize spacing: strip whitespace so comparisons are stable
            try:
                if op_for_match is not None:
                    op_for_match = str(op_for_match).strip()
            except Exception:
                pass

            matched = _compute_delay_from_timetable(
                line_ref, dest, lat_v, lon_v,
                return_jid=True,
                origin_dep_secs=origin_dep,
                origin_tz_offset_secs=(meta.get('origin_tz_offset_s') if meta and isinstance(meta, dict) else 0),
                operator_ref=op_for_match,
                strict_tol=600,
                feed_origin_atco=(meta.get('origin_atco') if meta and isinstance(meta, dict) else None),
                feed_destination_atco=(meta.get('destination_atco') if meta and isinstance(meta, dict) else None),
                return_debug=bus_provenance,
            )
            if bus_provenance and isinstance(matched, (list, tuple)) and len(matched) == 2 and isinstance(matched[1], dict):
                matched, matcher_dbg = matched

            if matched:
                # matched is expected to be a (delay_s, route_int) tuple when return_jid=True.
                computed, matched_route_int = matched

                # (None, None) means "no match" (not a contract violation).
                if computed is None or matched_route_int is None:
                    computed = None
                    matched_route_int = None
                    if bus_provenance:
                        try:
                            reject_reasons = _diagnose_match_failure(
                                line_ref, dest, lat_v, lon_v,
                                origin_dep_secs=origin_dep,
                                origin_tz_offset_secs=(meta.get('origin_tz_offset_s') if meta and isinstance(meta, dict) else 0),
                                operator_ref=op_for_match,
                                strict_tol=600,
                                feed_origin_atco=(meta.get('origin_atco') if meta and isinstance(meta, dict) else None),
                                feed_destination_atco=(meta.get('destination_atco') if meta and isinstance(meta, dict) else None),
                            )
                        except Exception:
                            reject_reasons = ['diagnosis_failed']

                    # Only flag invalid_match_missing_route_int when the matcher claims
                    # it found a delay but did not provide a route_int.
                    if bus_provenance and computed is not None and matched_route_int is None:
                        reject_reasons = reject_reasons or []
                        if 'invalid_match_missing_route_int' not in reject_reasons:
                            reject_reasons.append('invalid_match_missing_route_int')
                        try:
                            entry_match_dbg = {
                                'raw_match_return': repr(matched),
                                'raw_match_type': str(type(matched)),
                            }
                            meta_dbg = {
                                'line': line_ref,
                                'dest': dest,
                                'origin_dep_secs': origin_dep,
                                'operator_ref': op_for_match,
                                'origin_atco': (meta.get('origin_atco') if meta and isinstance(meta, dict) else None),
                                'destination_atco': (meta.get('destination_atco') if meta and isinstance(meta, dict) else None),
                            }
                            meta['_match_contract_debug'] = {'match': entry_match_dbg, 'ctx': meta_dbg}
                        except Exception:
                            pass
                else:
                    if bus_provenance:
                        match_reason = 'matched'
            else:
                computed = None
                matched_route_int = None
                # perform diagnosis to explain rejection only when provenance enabled
                if bus_provenance:
                    try:
                        reject_reasons = _diagnose_match_failure(
                            line_ref, dest, lat_v, lon_v,
                            origin_dep_secs=origin_dep,
                            origin_tz_offset_secs=(meta.get('origin_tz_offset_s') if meta and isinstance(meta, dict) else 0),
                            operator_ref=op_for_match,
                            strict_tol=600,
                            feed_origin_atco=(meta.get('origin_atco') if meta and isinstance(meta, dict) else None),
                            feed_destination_atco=(meta.get('destination_atco') if meta and isinstance(meta, dict) else None),
                        )
                    except Exception:
                        reject_reasons = ['diagnosis_failed']
        except Exception:
            computed = None
            matched_route_int = None
        # Prefer timetable-derived (computed) delay when we have a
        # confident timetable match (computed). Feed-supplied delays can
        # be incorrect or absurd; if we've matched a vehicle to a
        # timetable journey we should use the computed value as the
        # authoritative source. Fall back to the raw feed delay when no
        # computed value is available.
        final_delay = computed if computed is not None else delay_s

        # Enforce UI filtering: by default only include vehicles that were
        # successfully matched to a timetable journey. This prevents the
        # frontend from showing feed-only vehicles that have no authoritative
        # journey mapping. Set SHOW_UNMATCHED=1 in the environment to opt
        # into including unmatched feed vehicles for debugging/operational
        # reasons.
        # Default to showing unmatched vehicles so the endpoint always returns
        # useful data in dev/testing contexts. Set SHOW_UNMATCHED=0 to restore
        # strict filtering.
        show_unmatched_raw = str(os.environ.get('SHOW_UNMATCHED') or '').lower()
        show_unmatched = show_unmatched_raw not in ('0', 'false', 'no')
        if matched_route_int is None and not show_unmatched:
            # do not render unmatched vehicles into the API response
            continue
        # If we loaded stop metadata, try to filter vehicles that are already
        # at their destination. Destination strings in feeds are free-text;
        # we perform a simple name-match against stop metadata and if a
        # matching stop is within 50 metres of the vehicle, skip it.
        try:
            if _stop_name_map and dest:
                dest_q = str(dest).strip().lower()
                skip = False
                for sname_key, coords_list in _stop_name_map.items():
                    if dest_q in sname_key or sname_key in dest_q:
                        for (_si, s_lat, s_lon) in coords_list:
                            if _haversine_m(lat_v, lon_v, s_lat, s_lon) <= 50.0:
                                skip = True
                                break
                    if skip:
                        break
                if skip:
                    # do not include vehicles that have effectively reached their destination
                    continue
        except Exception:
            # On any failure in matching/filtering, fall back to including the vehicle
            pass
        # Prefer operator REF/code when present in metadata — callers and
        # matchers should use the canonical operator code (e.g. "SCCU")
        # rather than a human-readable name. Keep the human-readable
        # operator name available under `operator_name` for display/logging.
        op_code = meta.get('operator_ref') if meta and isinstance(meta, dict) and meta.get('operator_ref') else None
        # Normalize op_code spacing for API consumers
        try:
            if op_code is not None:
                op_code = str(op_code).strip()
        except Exception:
            pass
        entry = {
            "line": line_ref,
            "destination": dest,
            "lat": lat_v,
            "lon": lon_v,
            # Preserve backward-compatible human-readable operator field
            # while also exposing the canonical operator code in
            # `operator_ref` when available.
            "operator": _operator,
            **({"operator_ref": op_code} if op_code else {}),
            "delay_minutes": round(final_delay / 60, 1) if final_delay is not None else None,
            "status": _bus_delay_status(final_delay),
            "bearing": bearing,
        }

        # Attach the merged_key for which `route_int` (if present) is valid.
        # This lets clients request geometry using route_int without ambiguity
        # across the noon (AM/PM) merged split.
        try:
            from datetime import datetime as _dt
            now_dt = _dt.now()
            now_secs = now_dt.hour * 3600 + now_dt.minute * 60 + now_dt.second
            entry['merged_key'] = _merged_key_for_date_and_start_time(now_dt.date().isoformat(), now_secs)
        except Exception:
            pass

        # Expose matched route_int so clients can render tracks instantly.
        if matched_route_int is not None:
            try:
                entry['route_int'] = int(matched_route_int)
            except Exception:
                entry['route_int'] = matched_route_int

        # Best-effort: attach the full stop sequence for this route so clients
        # can fall back to stop-coordinate polylines when track fragments are
        # unavailable. This MUST NOT be OSRM-smoothed client-side.
        try:
            ri_for_stops = entry.get('route_int')
            if ri_for_stops is not None:
                ri_for_stops = int(ri_for_stops)
            merged = None
            try:
                if globals().get('_base_cache'):
                    prebuilt = _base_cache.get('prebuilt_cache')
                    if prebuilt:
                        for _k, v in prebuilt.items():
                            try:
                                merged = v[0]
                            except Exception:
                                merged = None
                            if merged:
                                break
                if merged is None:
                    rcache = globals().get('_router_cache')
                    rlock = globals().get('_router_cache_lock')
                    if rcache is not None:
                        if rlock:
                            with rlock:
                                items = list(rcache.values())
                        else:
                            items = list(rcache.values())
                        for val in items:
                            try:
                                merged = val[0]
                            except Exception:
                                merged = None
                            if merged:
                                break
            except Exception:
                merged = None

            if merged is not None and ri_for_stops is not None:
                route_stops = None
                try:
                    route_stops = merged.route_stops[ri_for_stops] if ri_for_stops < len(getattr(merged, 'route_stops', []) or []) else None
                except Exception:
                    route_stops = None
                if route_stops:
                    stop_ids = []
                    for s_int in route_stops:
                        try:
                            code = merged.get_atco_code(s_int)
                        except Exception:
                            code = None
                        if code:
                            stop_ids.append(str(code).strip())
                    if len(stop_ids) >= 2:
                        entry['stop_ids'] = stop_ids
        except Exception:
            pass
        # Route mapping signal: route_int is the authoritative indicator that a
        # vehicle was matched to a timetable journey for *this refresh*.
        #
        # Provenance/diagnostic fields (enabled by BUS_LIVE_PROVENANCE) are
        # used for debugging why matching fails.
        if bus_provenance:
            entry['match_reason'] = match_reason or ('matched' if matched_route_int is not None else 'unmatched')
            entry['reject_reasons'] = reject_reasons or []
            # Always include gate summary if available (even when unmatched)
            if matcher_dbg is not None:
                entry['matcher_gates'] = matcher_dbg
            try:
                if meta and isinstance(meta, dict) and meta.get('_match_contract_debug'):
                    entry['_match_contract_debug'] = meta.get('_match_contract_debug')
            except Exception:
                pass
        # If the feed provided an OriginAimedDepartureTime (seconds since
        # midnight) surface it to clients so they can filter vehicles by
        # their scheduled start time without recomputing/parsing XML.
        if origin_dep is not None:
            entry["origin_dep_secs"] = origin_dep
        # Surface best-effort metadata fields (may be missing)
        if meta:
            # If the feed included a raw operator code, surface it to clients
            if meta.get('operator_ref'):
                entry['operator_ref'] = meta.get('operator_ref')
            # If the feed included explicit ATCO codes for origin/destination, expose them
            if meta.get('origin_atco'):
                entry['origin_atco'] = meta.get('origin_atco')
            if meta.get('destination_atco'):
                entry['destination_atco'] = meta.get('destination_atco')
            entry["vehicle_ref"] = meta.get("vehicle_ref")
            entry["framed_journey_ref"] = meta.get("framed_journey_ref")
            entry["dated_journey_ref"] = meta.get("dated_journey_ref")
            entry["vehicle_journey_code"] = meta.get("vehicle_journey_code")
            # Do NOT pass through feed-supplied `logged_journey_id`.
            # The frontend must only rely on server-authoritative matches
            # (set earlier from the merged timetable) — accepting a
            # feed-provided logged_journey_id can make the UI display a
            # vehicle as matched even when the strict matcher rejected it.

        # Best-effort: attach cached or precomputed route track geometry.
        # NOTE: currently only computes when entry already contains a
        # route_id/route_int (future follow-up: derive these during refresh).
        if BUS_LIVE_INCLUDE_TRACKS:
            try:
                if bus_provenance:
                    coords, src, was_cached = _compute_vehicle_track_coords_with_source_for_live(entry)
                    if coords and len(coords) >= 2:
                        entry['track_coords'] = coords
                        entry['track_source'] = src
                        entry['track_cached'] = bool(was_cached)
                else:
                    coords = _get_cached_bus_track(entry)
                    if not coords:
                        coords = _compute_vehicle_track_coords_for_live(entry)
                    if coords and len(coords) >= 2:
                        entry['track_coords'] = coords
            except Exception:
                pass

        # UI-level filtering: do not include vehicles with delay > threshold
        try:
            if entry.get('delay_minutes') is not None:
                # convert displayed minutes back to seconds for comparison
                if (entry.get('delay_minutes') * 60) > MAX_VISIBLE_DELAY_S:
                    # skip vehicles with excessive delay
                    continue
        except Exception:
            # On any failure in this check, fall back to including the vehicle
            pass

        out.append(entry)
    return out


@app.get("/debug/live_match_contract")
async def debug_live_match_contract(
    operator: str = 'SCCU',
    lat: float = 54.0466,
    lon: float = -2.8007,
    latTol: float = 0.15,
    lonTol: float = 0.15,
    sample: int = 25,
    limit: int = 50,
):
    """Return only live vehicles that violated the live-match contract.

    This is a lightweight way to inspect cases where a match appears truthy
    but doesn't produce a journey id (e.g. invalid_match_missing_jid).

    Intended for local debugging with BUS_LIVE_PROVENANCE=1.
    """
    # This endpoint must be fast. So we fetch raw live vehicles and run the
    # matcher on only a small sample, returning just the contract violations.
    from fastapi.responses import JSONResponse

    if _live_endpoints_disabled():
        return {
            'operator': operator,
            'lat': lat,
            'lon': lon,
            'latTol': latTol,
            'lonTol': lonTol,
            'sample': max(0, int(sample)) if isinstance(sample, int) or str(sample).isdigit() else sample,
            'limit': max(0, int(limit)) if isinstance(limit, int) or str(limit).isdigit() else limit,
            'raw_total': 0,
            'violations': [],
            'disabled': True,
        }

    try:
        smp = max(0, int(sample))
    except Exception:
        smp = 25
    try:
        lim = max(0, int(limit))
    except Exception:
        lim = 50

    urls = None
    if operator and str(operator).lower() != 'all':
        urls = [f"https://transport.scc.lancs.ac.uk/bus/live/{operator}"]

    try:
        raw = get_bus_live(lat, lon, urls=urls, lat_tol=latTol, lon_tol=lonTol)
    except Exception as exc:
        try:
            import traceback
            return JSONResponse(
                status_code=500,
                content={'error': str(exc), 'traceback': traceback.format_exc()},
            )
        except Exception:
            return JSONResponse(status_code=500, content={'error': str(exc)})

    bus_provenance = str(os.environ.get('BUS_LIVE_PROVENANCE') or '').lower() in ('1', 'true', 'yes')
    violations = []

    for idx, item in enumerate(raw[:smp] if smp else raw):
        # Mirror the /bus/live unpacking logic for tuple + optional meta-dict
        meta = {}
        base = item
        try:
            if isinstance(item[-1], dict):
                meta = item[-1]
                base = item[:-1]
        except Exception:
            base = item

        try:
            if len(base) == 7:
                line_ref, dest, lat_v, lon_v, _operator, delay_s, origin_dep = base
            else:
                line_ref, dest, lat_v, lon_v, _operator, delay_s, origin_dep, _bearing = base
        except Exception:
            continue

        # Only investigate vehicles where we need to compute a delay
        if delay_s is not None:
            continue

        op_for_match = meta.get('operator_ref') if meta and isinstance(meta, dict) and meta.get('operator_ref') else _operator
        try:
            if op_for_match is not None:
                op_for_match = str(op_for_match).strip()
        except Exception:
            pass

        try:
            matched = _compute_delay_from_timetable(
                line_ref,
                dest,
                lat_v,
                lon_v,
                return_jid=True,
                origin_dep_secs=origin_dep,
                origin_tz_offset_secs=(meta.get('origin_tz_offset_s') if meta and isinstance(meta, dict) else 0),
                operator_ref=op_for_match,
                strict_tol=600,
                feed_origin_atco=(meta.get('origin_atco') if meta and isinstance(meta, dict) else None),
                feed_destination_atco=(meta.get('destination_atco') if meta and isinstance(meta, dict) else None),
            )
        except Exception as exc:
            try:
                import traceback
                return JSONResponse(
                    status_code=500,
                    content={
                        'error': f'matcher_exception: {exc}',
                        'traceback': traceback.format_exc(),
                        'operator': operator,
                        'idx': idx,
                        'line': line_ref,
                        'dest': dest,
                        'vehicle_ref': meta.get('vehicle_ref') if isinstance(meta, dict) else None,
                    },
                )
            except Exception:
                return JSONResponse(status_code=500, content={'error': f'matcher_exception: {exc}'})

        # Contract: if matched is truthy, it must be (delay, jid) with jid not None.
        if matched:
            try:
                computed, jid = matched
            except Exception:
                computed, jid = None, None
            if jid is None and computed is not None:
                violations.append({
                    'idx': idx,
                    'vehicle_ref': meta.get('vehicle_ref') if isinstance(meta, dict) else None,
                    'line': line_ref,
                    'dest': dest,
                    'operator_ref': op_for_match,
                    'origin_dep_secs': origin_dep,
                    'origin_atco': meta.get('origin_atco') if isinstance(meta, dict) else None,
                    'destination_atco': meta.get('destination_atco') if isinstance(meta, dict) else None,
                    'raw_match_return': repr(matched),
                    'raw_match_type': str(type(matched)),
                    'bus_provenance': bus_provenance,
                })

        if lim and len(violations) >= lim:
            break

    return {
        'operator': operator,
        'lat': lat,
        'lon': lon,
        'latTol': latTol,
        'lonTol': lonTol,
        'sample': smp,
        'limit': lim,
        'raw_total': len(raw),
        'violations': violations,
    }

# ── Upcoming timetabled departures from a bus stop ──────────────────


@app.get("/bus/arrivals/{stop_code}")
async def bus_arrivals(stop_code: str, limit: int = 5):
    """Return upcoming timetabled departures from a bus stop.

    Path param:
        stop_code: ATCO code of the stop (e.g. ``2500LAA12000``)

    Query param:
        limit: max results to return (default 5)

    Response: JSON list of
        ``{line, destination, scheduledTime, status}``
    where ``status`` is always ``"On time"`` (real-time delay data is
    delivered via the WebSocket live-updates channel, not this endpoint).
    """
    from fastapi.responses import JSONResponse
    from time_utils import seconds_to_time

    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    now_secs = now.hour * 3600 + now.minute * 60 + now.second

    try:
        merged, _router, _walking = get_router_for_date(date_str, start_time=now_secs)
    except Exception:
        return JSONResponse(status_code=503, content={"error": "Backend not initialized"})

    # Build ATCO code → merged stop index reverse map
    atco_to_idx: Dict[str, int] = {}
    for i in range(len(merged.stop_to_routes)):
        code = merged.get_atco_code(i)
        if code:
            atco_to_idx[code] = i

    stop_index = atco_to_idx.get(stop_code)
    if stop_index is None:
        return JSONResponse(
            status_code=404,
            content={"error": f"Stop not found: {stop_code}"},
        )

    # Window: now → now + 90 minutes
    window_end = now_secs + 5400
    departures: List[Dict[str, Any]] = []
    seen_journeys: set = set()

    for route_idx in merged.stop_to_routes[stop_index]:
        if route_idx >= len(merged.route_journeys):
            continue
        meta = (
            (merged.route_metadata[route_idx] or {})
            if route_idx < len(merged.route_metadata)
            else {}
        )
        raw_line = (meta.get("line_name") or "").strip()
        line = raw_line.split(":")[-1] if raw_line else "?"

        for j_idx in merged.route_journeys[route_idx]:
            if j_idx in seen_journeys:
                continue
            seen_journeys.add(j_idx)

            jsi = (
                merged.journey_stop_index[j_idx]
                if j_idx < len(merged.journey_stop_index)
                else {}
            )
            if stop_index not in jsi:
                continue
            pos = jsi[stop_index]
            jt = merged.journey_times[j_idx]
            if pos >= len(jt):
                continue

            dep_time = jt[pos][2]  # (stop_int, arr_time, dep_time)
            if dep_time < now_secs or dep_time > window_end:
                continue

            # Destination = name of the last stop in this journey
            dest_stop_int = jt[-1][0]
            destination = (
                merged.stop_metadata[dest_stop_int]
                if dest_stop_int < len(merged.stop_metadata)
                else ""
            ) or "Unknown"

            departures.append({
                "line": line,
                "destination": destination,
                "scheduledTime": seconds_to_time(int(dep_time)),
                "status": "On time",
            })

    departures.sort(key=lambda d: d["scheduledTime"])
    return departures[:limit]


# Global cache for (merged, router, walking) by (date, AM/PM bucket)
_router_cache = {}
_router_cache_lock = threading.Lock()
# Track background builds in progress (cache_key set)
_background_builds = set()

# Single-flight synchronisation for router builds: when multiple concurrent
# requests ask for the same cache_key and it isn't cached yet, only one build
# will execute. Others will wait for the builder to populate the cache.
_router_build_events: Dict[Any, threading.Event] = {}
# Base init state: _base_cache holds the cached base data when
# initialization succeeds, and _base_init_attempted indicates whether
# we've already tried initialisation once. This prevents repeated,
# noisy re-attempts (and repeated "Initializing Transport Backend System"
# prints) when the first attempt fails.
_base_cache = None
_base_init_attempted = False

# Real-time delay map: {journey_id: delay_seconds}
_journey_delay_map: Dict[int, int] = {}
_journey_delay_lock = threading.Lock()
_delay_map_ts: float = 0.0
_delay_map_version: int = 0
# Update interval in seconds (default 180s == 3min)
_DELAY_UPDATE_INTERVAL = int(os.environ.get('DELAY_UPDATE_INTERVAL', '180'))
# How many historical delay-aware router versions to keep in the in-memory
# cache. Older versions will be pruned to avoid unbounded memory growth when
# the live delay map increments frequently. Set via env var
# ROUTER_CACHE_MAX_VERSIONS (default: 3).
_ROUTER_CACHE_MAX_VERSIONS = int(os.environ.get('ROUTER_CACHE_MAX_VERSIONS', '3'))


def _prune_router_cache_for_date_bucket(date_str: str, bucket: str, keep: int = None) -> None:
    """Prune old router cache entries for a specific (date, bucket).

    Keeps only the newest `keep` versions for keys shaped (date_str, bucket, version).
    Older entries are deleted from `_router_cache` while holding
    `_router_cache_lock`.
    """
    if keep is None:
        keep = _ROUTER_CACHE_MAX_VERSIONS
    with _router_cache_lock:
        # Collect keys that match (date_str, bucket, version)
        matches = []
        for k in list(_router_cache.keys()):
            if isinstance(k, tuple) and len(k) == 3 and k[0] == date_str and k[1] == bucket:
                try:
                    ver = int(k[2])
                except Exception:
                    continue
                matches.append((ver, k))
        if len(matches) <= keep:
            return
        matches.sort()
        # Remove oldest entries beyond the newest `keep`
        to_remove = matches[0: max(0, len(matches) - keep)]
        for _, key in to_remove:
            try:
                del _router_cache[key]
            except KeyError:
                pass


def _set_router_cache(key, value):
    """Set an entry in the router cache and prune old versions if needed."""
    with _router_cache_lock:
        _router_cache[key] = value
    # If this cache key includes a version (date, bucket, version) then prune
    # old versions for that date/bucket to keep memory bounded.
    if isinstance(key, tuple) and len(key) == 3:
        try:
            date_str, bucket, _ver = key
            _prune_router_cache_for_date_bucket(date_str, bucket, keep=_ROUTER_CACHE_MAX_VERSIONS)
        except Exception:
            pass


def _recompute_journey_delay_map_once() -> None:
    """Compute the per-journey delay map from live feeds.

    Uses feed-supplied delay when present; otherwise falls back to
    timetable matching. Results are written atomically to
    `_journey_delay_map` and bump `_delay_map_version`.
    """
    global _journey_delay_map, _delay_map_ts, _delay_map_version
    try:
        buses = _fetch_all_live_buses()
    except Exception:
        return

    temp_map: Dict[int, int] = {}
    # We will need raw merged/router data to match vehicles; request
    # the unadjusted timetable to avoid recursion.
    today = datetime.now().date().isoformat()
    now = datetime.now()
    now_seconds = now.hour * 3600 + now.minute * 60 + now.second
    try:
        merged, router, walking = get_router_for_date(today, start_time=now_seconds, apply_delay=False)
    except Exception:
        merged = None

    for item in buses:
        # support legacy 7-tuples and new 8-tuples (with bearing) and
        # the extended shape with a trailing metadata dict
        meta = {}
        base = item
        try:
            if isinstance(item[-1], dict):
                meta = item[-1]
                base = item[:-1]
        except Exception:
            base = item

        if len(base) == 7:
            line_ref, dest, lat_v, lon_v, _op, feed_delay, origin_dep = base
        else:
            line_ref, dest, lat_v, lon_v, _op, feed_delay, origin_dep, _bearing = base
        try:
            # Ask the matching function for both delay and journey id
            # Prefer feed-supplied operator code when available in metadata
            op_for_match = meta.get('operator_ref') if meta and isinstance(meta, dict) and meta.get('operator_ref') else _op
            matched = _compute_delay_from_timetable(
                line_ref, dest, lat_v, lon_v,
                return_jid=True,
                origin_dep_secs=origin_dep,
                operator_ref=op_for_match,
                # Allow the background recompute to accept off-track
                # vehicles and large delays so the delay map reflects
                # observed feed state even when UI suppresses off-track
                # matches.
                allow_offtrack=True,
                allow_abs_delay=True,
                origin_tz_offset_secs=(meta.get('origin_tz_offset_s') if meta and isinstance(meta, dict) else 0),
                strict_tol=600,
                feed_origin_atco=(meta.get('origin_atco') if meta and isinstance(meta, dict) else None),
                feed_destination_atco=(meta.get('destination_atco') if meta and isinstance(meta, dict) else None),
            )
            if not matched:
                continue
            computed_delay, j_id = matched
            if j_id is None:
                continue
            # Prefer timetable-derived (computed_delay) when present by
            # default. However tests and operators sometimes report a
            # small, precise feed delay (e.g. 60s) that we should honour
            # in the UI. To allow that without changing default behaviour
            # introduce an opt-in env var `FEED_DELAY_THRESHOLD_S`.
            # If set >0 and the feed reports a small delay <= threshold
            # prefer the feed delay. Otherwise, prefer computed_delay
            # when available and fall back to the feed value.
            try:
                _feed_delay_threshold = int(os.environ.get('FEED_DELAY_THRESHOLD_S', '0'))
            except Exception:
                _feed_delay_threshold = 0
            if feed_delay is not None and _feed_delay_threshold > 0 and feed_delay <= _feed_delay_threshold:
                final_delay = feed_delay
            else:
                final_delay = computed_delay if computed_delay is not None else feed_delay
            if final_delay is None:
                continue
            # Coerce to int and clamp negative delays to 0 (we don't show "Early")
            try:
                d = int(final_delay)
            except Exception:
                continue
            if d < 0:
                d = 0
            # Do not persist journeys whose computed/displayable delay
            # exceeds the configured visibility threshold. This prevents
            # very-late journeys from being stored in the authoritative
            # delay map (they will be treated as absent).
            try:
                MAX_VISIBLE_DELAY_S = int(os.environ.get('MAX_VISIBLE_DELAY_S', '1200'))
            except Exception:
                MAX_VISIBLE_DELAY_S = 1200
            if d > MAX_VISIBLE_DELAY_S:
                # Skip storing this journey entirely
                continue
            # Store integer seconds, keeping the minimum delay if multiple buses map to the same journey
            j_id_int = int(j_id)
            if j_id_int in temp_map:
                temp_map[j_id_int] = min(temp_map[j_id_int], d)
            else:
                temp_map[j_id_int] = d
        except Exception:
            continue

    # Determine strict window thresholds for pruning out-of-window journeys
    try:
        MATCH_ALLOW_BEFORE_S = int(os.environ.get('MATCH_ALLOW_BEFORE_S', '300'))
    except Exception:
        MATCH_ALLOW_BEFORE_S = 300
    try:
        MATCH_ALLOW_AFTER_S = int(os.environ.get('MATCH_ALLOW_AFTER_S', '300'))
    except Exception:
        MATCH_ALLOW_AFTER_S = 300

    with _journey_delay_lock:
        # Prune any entries for journeys that are no longer present in
        # the freshly-loaded merged timetable to avoid retaining stale
        # delay values for deleted journeys. Also drop journeys whose
        # scheduled window does not overlap the current time +/- the
        # configured allow-before/after window.
        if merged is not None:
            valid_jids = set(i for i, jm in enumerate(merged.journey_metadata) if jm)
            pruned_map: Dict[int, int] = {}
            for jid, d in temp_map.items():
                if jid not in valid_jids:
                    continue
                try:
                    jt = merged.journey_times[jid]
                    start_dep = jt[0][2]
                    end_arr = jt[-1][1] if jt[-1][1] is not None else jt[-1][2]
                    # Only keep journeys whose scheduled window overlaps now +/- tolerance
                    if now_seconds >= start_dep - MATCH_ALLOW_BEFORE_S and now_seconds <= end_arr + MATCH_ALLOW_AFTER_S:
                        pruned_map[jid] = d
                except Exception:
                    # If we can't determine times, drop the entry to be safe
                    continue
            _journey_delay_map = pruned_map
        else:
            _journey_delay_map = temp_map
        _delay_map_ts = time.time()
        _delay_map_version += 1

    if os.environ.get('BUS_DISABLE_ROUTER_PREBUILD') == '1':
        # Useful for routing benchmarks: don't spawn extra CPU-heavy router builds.
        return

    # After updating the delay map version, proactively rebuild the
    # delay-aware routers for today's AM and PM buckets in a background
    # thread so the first user request doesn't pay the build latency.
    def _prebuild_routers_for_today():
        try:
            today = datetime.now().date().isoformat()
            # Representative times for AM and PM buckets
            am_seconds = 8 * 3600
            pm_seconds = 18 * 3600
            # Request with apply_delay=True so get_router_for_date will
            # build and cache the adjusted router for the current
            # _delay_map_version.
            try:
                get_router_for_date(today, start_time=am_seconds, apply_delay=True)
            except Exception as e:
                logger.debug('Prebuild AM router failed: %s', e)
            try:
                get_router_for_date(today, start_time=pm_seconds, apply_delay=True)
            except Exception as e:
                logger.debug('Prebuild PM router failed: %s', e)
        except Exception:
            # Swallow errors — this is a best-effort background task.
            logger.exception('Error in _prebuild_routers_for_today')

    try:
        t = threading.Thread(target=_prebuild_routers_for_today, daemon=True)
        # Allow disabling this best-effort prebuild thread. In dev / first-search
        # tuning, it can contend for CPU/IO and make the first search feel slow.
        prebuild_enabled = str(os.environ.get('ENABLE_ROUTER_PREBUILD') or '').lower() not in ('0', 'false', 'no')
        if prebuild_enabled:
            t.start()
    except Exception:
        # If background thread creation fails, continue without prebuilding.
        logger.exception('Failed to start prebuild thread for routers')


def _delay_updater_loop(stop_event: threading.Event):
    """Background loop to periodically refresh the delay map."""
    # Run once immediately, then sleep interval
    while not stop_event.is_set():
        # When routing is running, pause delay recomputation/prebuilds.
        if is_routing_active():
            time.sleep(0.25)
            continue
        try:
            _recompute_journey_delay_map_once()
        except Exception:
            pass
        # Sleep in small increments so we can exit promptly
        sleep_for = _DELAY_UPDATE_INTERVAL
        for _ in range(int(max(1, sleep_for))):
            if stop_event.is_set():
                break
            time.sleep(1)


def _merged_key_for_date_and_start_time(date_str: str, start_time: int | None) -> str:
    """Return a stable key identifying which merged build a route_int belongs to.

    IMPORTANT: `route_int` is an index into a particular merged build, and is
    only meaningful when interpreted against the same (date,bucket) merged.

    We mirror `get_router_for_date` bucketing exactly (noon split / 43200s).
    """
    bucket = "AM" if (start_time is not None and int(start_time) < 43200) else "PM"
    return f"{date_str}|{bucket}"


def _get_router_for_merged_key(merged_key: str, apply_delay: bool = False):
    """Resolve a merged_key back to (merged, router, walking)."""
    try:
        date_str, bucket = str(merged_key).split('|', 1)
    except Exception:
        date_str, bucket = (datetime.now().date().isoformat(), 'AM')

    # Pick representative start_time values that map to the intended bucket.
    start_time = 8 * 3600 if str(bucket).upper() == 'AM' else 18 * 3600
    return get_router_for_date(date_str, start_time=start_time, apply_delay=apply_delay)

def get_router_for_date(date_str, start_time=None, apply_delay: bool = True):
    """Return (merged, router, walking) for a given date and time bucket.

    The cache key includes the AM/PM bucket so morning and afternoon
    queries use the correct two-day merge.
    """
    global _base_cache, _router_cache
    # Optional per-request timing to diagnose slow first search.
    timing_enabled = str(os.environ.get('ROUTER_BUILD_TIMING') or '').lower() in ('1', 'true', 'yes')
    _t0 = time.perf_counter() if timing_enabled else None
    bucket = "AM" if (start_time is not None and start_time < 43200) else "PM"
    # When apply_delay is requested for today's date, include the
    # current delay-map version in the cache key so we rebuild the
    # router when the delay map changes.
    today_str = datetime.now().date().isoformat()
    # If there's no delay data to apply, don't force a versioned cache key.
    # This is important for fast startup: initialize_base() prebuilds the
    # unadjusted (date,bucket) routers, and most endpoints (e.g. /routes/line)
    # don't actually need a delay-adjusted router.
    # NOTE: _delay_map_version increments even when the delay map is empty.
    # So the *real* signal for whether delay-adjustment would change anything
    # is whether _journey_delay_map has entries.
    try:
        no_delay_to_apply = (not _journey_delay_map) or (len(_journey_delay_map) == 0)
    except Exception:
        no_delay_to_apply = not _journey_delay_map

    if apply_delay and date_str == today_str and not no_delay_to_apply:
        cache_key = (date_str, bucket, _delay_map_version)
    else:
        cache_key = (date_str, bucket)
    # Single-flight: ensure at most one build happens per cache_key.
    # Others wait for the builder to populate the cache.
    builder_event: threading.Event | None = None
    while True:
        with _router_cache_lock:
            if cache_key in _router_cache:
                if timing_enabled:
                    try:
                        logger.info('[router] cache hit %s in %.3fs', cache_key, time.perf_counter() - _t0)
                    except Exception:
                        pass
                return _router_cache[cache_key]
            ev = _router_build_events.get(cache_key)
            if ev is None:
                ev = threading.Event()
                _router_build_events[cache_key] = ev
                builder_event = ev
                break  # we are the builder
        # Someone else is building it; wait and retry.
        if timing_enabled:
            try:
                logger.info('[router] waiting for build %s', cache_key)
            except Exception:
                pass
        ev.wait()

    # We are the builder for this cache_key. Always release waiters.
    try:
        _t_build_start = time.perf_counter() if timing_enabled else None
        # First, attempt a fast lookup under the router cache lock. If the
        # requested adjusted router (today + current delay version) is present,
        # return it immediately. If it's missing but a fallback unadjusted
        # router exists, schedule a background build of the adjusted router and
        # return the fallback so requests are non-blocking. If no fallback is
        # available, fall back to the original synchronous build behaviour.
        today_str = datetime.now().date().isoformat()
        fallback_key = (date_str, bucket)
        need_sync_build = False
        schedule_bg = False
        fallback = None
        with _router_cache_lock:
            if cache_key in _router_cache:
                return _router_cache[cache_key]

        # If caller asked for a delay-aware router for today, prefer to
        # build the adjusted router in the background and return the
        # existing (non-delayed) router if present. Only schedule a
        # background build when we have a fallback to serve.
        if apply_delay and date_str == today_str:
            # Prefer the newest existing delay-aware router (older versions)
            # if available. This allows serving a previously-built adjusted
            # router while the very latest version is being built. If no
            # adjusted router is present, fall back to the unadjusted router
            # (fallback_key) if present; otherwise require a synchronous build.
            best_old_key = None
            best_old_ver = -1
            for k in list(_router_cache.keys()):
                if isinstance(k, tuple) and len(k) == 3 and k[0] == date_str and k[1] == bucket:
                    try:
                        ver = int(k[2])
                    except Exception:
                        continue
                    # Pick the newest version less than or equal to current
                    if ver <= _delay_map_version and ver > best_old_ver:
                        best_old_ver = ver
                        best_old_key = k
            if best_old_key is not None:
                # Use the best older adjusted router as fallback
                fallback = _router_cache.get(best_old_key)
                # Also schedule a background build for the current version
                if cache_key not in _background_builds:
                    _background_builds.add(cache_key)
                    schedule_bg = True
            elif fallback_key in _router_cache:
                # No adjusted router exists; use the unadjusted router as fallback
                if cache_key not in _background_builds:
                    _background_builds.add(cache_key)
                    schedule_bg = True
                fallback = _router_cache[fallback_key]
            else:
                # No fallback available; fall back to synchronous behaviour
                need_sync_build = True
        # For non-delay or non-today requests, proceed to build synchronously
        # below (but do not hold the lock while performing heavy work).

        # If we scheduled a background build, do it without blocking this
        # request (background thread will populate the cache when ready).
        if schedule_bg:
            def _bg_build_and_cache(key, dstr, stime):
                try:
                    # Obtain the base (non-delayed) merged/router/walking so we
                    # can apply delays and rebuild the adjusted router.
                    try:
                        merged, router, walking = get_router_for_date(dstr, start_time=stime, apply_delay=False)
                    except Exception:
                        # If we can't obtain the base router, abort gracefully.
                        return
                    # Apply live delays if present and rebuild
                    if _journey_delay_map:
                        try:
                            from raptor_router import RaptorRouter
                            adj_merged = copy.deepcopy(merged)
                            for j_idx, delta in list(_journey_delay_map.items()):
                                if 0 <= j_idx < len(adj_merged.journey_times):
                                    jt = adj_merged.journey_times[j_idx]
                                    new_jt = []
                                    for sid, atime, dtime in jt:
                                        at = atime + delta if atime is not None else None
                                        dt = dtime + delta if dtime is not None else None
                                        new_jt.append((sid, at, dt))
                                    adj_merged.journey_times[j_idx] = new_jt
                            adj_router = RaptorRouter(adj_merged)
                            _set_router_cache(key, (adj_merged, adj_router, walking))
                            return
                        except Exception:
                            # Fall through to caching the unadjusted router
                            pass
                    # If no delays or adjustment failed, cache the unadjusted router
                    _set_router_cache(key, (merged, router, walking))
                finally:
                    with _router_cache_lock:
                        _background_builds.discard(key)

            try:
                t = threading.Thread(target=_bg_build_and_cache, args=(cache_key, date_str, start_time), daemon=True)
                t.start()
            except Exception:
                logger.exception('Failed to start background build thread for cache_key %s', cache_key)
            # Return the prepared fallback value (non-delayed router) so callers
            # are not blocked; the background thread will populate the adjusted
            # router when ready.
            return fallback

        # At this point either we need a synchronous build (no fallback) or the
        # request is for a non-delay router. Perform the original synchronous
        # build behaviour (this may be heavy).
        if timing_enabled:
            try:
                logger.info('[router] sync build start %s (apply_delay=%s)', cache_key, apply_delay)
            except Exception:
                pass
        global _base_init_attempted
        # If initialization hasn't run yet, run it synchronously (same as before)
        if _base_cache is None and not _base_init_attempted:
            from main import initialize_base
            try:
                _t_init0 = time.perf_counter() if timing_enabled else None
                _base_cache = initialize_base()
                if timing_enabled and _t_init0 is not None:
                    try:
                        logger.info('[router] initialize_base() %.3fs', time.perf_counter() - _t_init0)
                    except Exception:
                        pass
                if _base_cache and "prebuilt_cache" in _base_cache:
                    # IMPORTANT: insert with _set_router_cache so single-flight
                    # waits and key normalization apply. Otherwise prebuilt AM/PM
                    # may not be visible and first requests rebuild.
                    try:
                        pre = _base_cache.get('prebuilt_cache') or {}
                        if isinstance(pre, dict):
                            for k, v in pre.items():
                                try:
                                    if isinstance(k, tuple) and len(k) == 2:
                                        _set_router_cache((str(k[0]), str(k[1])), v)
                                except Exception:
                                    continue
                    except Exception:
                        pass
                # If the cache was populated during init, return it
                with _router_cache_lock:
                    if cache_key in _router_cache:
                        if timing_enabled:
                            try:
                                logger.info('[router] cache became available after init %s in %.3fs', cache_key, time.perf_counter() - _t0)
                            except Exception:
                                pass
                        return _router_cache[cache_key]
            except Exception:
                _base_init_attempted = True
                raise
            finally:
                _base_init_attempted = True
        elif _base_cache is None and _base_init_attempted:
            try:
                from main import initialize_base
                if callable(initialize_base):
                    _base_cache = initialize_base()
                    if _base_cache and "prebuilt_cache" in _base_cache:
                        try:
                            pre = _base_cache.get('prebuilt_cache') or {}
                            if isinstance(pre, dict):
                                for k, v in pre.items():
                                    try:
                                        if isinstance(k, tuple) and len(k) == 2:
                                            _set_router_cache((str(k[0]), str(k[1])), v)
                                    except Exception:
                                        continue
                        except Exception:
                            pass
                    with _router_cache_lock:
                        if cache_key in _router_cache:
                            return _router_cache[cache_key]
            except Exception:
                raise RuntimeError("Backend base initialisation previously failed")
        loader = _base_cache["loader"]
        walking_raw = _base_cache["walking_raw"]
        al = _base_cache.get("atco_loader")
        from main import build_for_date
        # Build the merged data / router for this date (potentially heavy)
        _t_build0 = time.perf_counter() if timing_enabled else None
        merged, router, walking = build_for_date(
            loader, walking_raw, date_str, start_time=start_time,
            atco_loader=al)

        # Stamp a small amount of context onto merged so downstream consumers
        # (notably build_journey_plan_response geometry selection) can know the
        # requested service day and the AM/PM bucket. MergedData itself doesn't
        # define date_str/bucket fields.
        try:
            if not hasattr(merged, 'meta') or not isinstance(getattr(merged, 'meta', None), dict):
                merged.meta = {}
            bucket0 = "AM" if (start_time is not None and start_time < 43200) else "PM"
            merged.meta['date'] = str(date_str)
            merged.meta['bucket'] = bucket0
        except Exception:
            pass
        if timing_enabled and _t_build0 is not None:
            try:
                logger.info('[router] build_for_date(%s,%s) %.3fs', date_str, bucket, time.perf_counter() - _t_build0)
            except Exception:
                pass
        # If we're asked to apply today's delay map, modify a deep copy
        # of the merged timetable by adding per-journey delays and then
        # rebuild the router from that adjusted merged object.
        if apply_delay and date_str == today_str and _journey_delay_map:
            try:
                from raptor_router import RaptorRouter
                _t_delay0 = time.perf_counter() if timing_enabled else None
                adj_merged = copy.deepcopy(merged)
                # Apply delays (per journey index) to every scheduled time
                for j_idx, delta in list(_journey_delay_map.items()):
                    if 0 <= j_idx < len(adj_merged.journey_times):
                        jt = adj_merged.journey_times[j_idx]
                        new_jt = []
                        for sid, atime, dtime in jt:
                            at = atime + delta if atime is not None else None
                            dt = dtime + delta if dtime is not None else None
                            new_jt.append((sid, at, dt))
                        adj_merged.journey_times[j_idx] = new_jt
                router = RaptorRouter(adj_merged)
                if timing_enabled and _t_delay0 is not None:
                    try:
                        logger.info('[router] apply_delay+rebuild %.3fs', time.perf_counter() - _t_delay0)
                    except Exception:
                        pass
                _set_router_cache(cache_key, (adj_merged, router, walking))
                if timing_enabled and _t0 is not None:
                    try:
                        logger.info('[router] total sync build %s %.3fs', cache_key, time.perf_counter() - _t0)
                    except Exception:
                        pass
                return adj_merged, router, walking
            except Exception:
                # Fall back to unadjusted merged/router on any failure
                _set_router_cache(cache_key, (merged, router, walking))
                if timing_enabled and _t0 is not None:
                    try:
                        logger.info('[router] total sync build (delay failed) %s %.3fs', cache_key, time.perf_counter() - _t0)
                    except Exception:
                        pass
                return merged, router, walking

        _set_router_cache(cache_key, (merged, router, walking))
        if timing_enabled and _t0 is not None:
            try:
                logger.info('[router] total sync build %s %.3fs', cache_key, time.perf_counter() - _t0)
            except Exception:
                pass
        return merged, router, walking
    finally:
        # Always release any waiters for this cache_key.
        with _router_cache_lock:
            _router_build_events.pop(cache_key, None)
        try:
            if builder_event is not None:
                builder_event.set()
        except Exception:
            pass

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel


def _sanitize_route(route_result):
    """Return a JSON-serializable copy of the RAPTOR route dict.

    The raw dict uses integer stop-index keys and stores a
    ``MergedData`` reference in the ``"day"`` field of each leg.
    This helper converts keys to strings and drops the ``"day"``
    field so the result can be returned over the API.
    """
    if not route_result:
        return route_result
    clean = {}
    for key, value in route_result.items():
        str_key = str(key) if not isinstance(key, str) else key
        if isinstance(value, dict):
            # Remove the non-serializable "day" field (MergedData ref)
            clean[str_key] = {k: v for k, v in value.items()
                              if k != "day"}
        else:
            clean[str_key] = value
    return clean


def format_route_text(route_result, merged):
    """Format a human-readable route summary.

    This function intentionally uses plain ASCII text for separators
    and markers to avoid encoding / mojibake issues when printed
    to terminals or returned over the API.
    """
    from time_utils import seconds_to_time
    import math

    if not route_result:
        return "\n  No route found."

    # Walking-only route (only _meta present)
    if set(route_result.keys()) == {"_meta"}:
        meta = route_result["_meta"]
        start_point = meta.get("start_point", ())
        destination = meta.get("destination", ())
        start_walk = meta.get("start_walk_seconds", 0)
        end_walk = meta.get("end_walk_seconds", 0)
        total_arrival = meta.get("total_arrival")

        out = []
        out.append("\n" + "=" * 60)
        out.append("  Route found - only walking")
        out.append("=" * 60)
        if start_point:
            out.append(f"\n  - Start  ({start_point[0]:.5f}, {start_point[1]:.5f})")
        walk_min = (start_walk + end_walk) / 60
        out.append(f"    - Walk {walk_min:.0f} min ({start_walk + end_walk}s)")
        if destination:
            out.append(f"  - Destination  ({destination[0]:.5f}, {destination[1]:.5f})")
        if total_arrival is not None:
            out.append(f"    Arrive at {seconds_to_time(int(total_arrival))}")
        out.append("\n" + "=" * 60)
        return "\n".join(out)

    # Multi-stop transit route
    meta = route_result.get("_meta", {})
    start_walk = meta.get("start_walk_seconds", 0)
    end_walk = meta.get("end_walk_seconds", 0)
    total_arrival = meta.get("total_arrival")
    start_point = meta.get("start_point", ())
    destination = meta.get("destination", ())

    # Defensive: only consider entries whose values are dicts. Some router
    # implementations may include unexpected non-dict items which would make
    # indexing like info["prev_stop"] raise TypeError.
    route_data = {k: v for k, v in route_result.items()
                  if k != "_meta" and isinstance(v, dict)}
    all_prevs = {info.get("prev_stop") for info in route_data.values()
                 if isinstance(info, dict) and info.get("prev_stop") is not None}
    destinations = [s for s in route_data if s not in all_prevs]
    if not destinations:
        destinations = list(route_data.keys())

    stop = destinations[0]
    visited = set()
    legs = []
    while stop is not None and stop not in visited:
        visited.add(stop)
        legs.append((stop, route_data[stop]))
        stop = route_data[stop]["prev_stop"]
    legs.reverse()

    out = []
    out.append("\n" + "=" * 60)
    out.append(f"  Route found - {len(legs)} stop(s)")
    out.append("=" * 60)

    if start_point and legs:
        first_arrival = legs[0][1]["arrival_time"]
        depart_time = first_arrival - start_walk
        out.append(f"\n  - Start  ({start_point[0]:.5f}, {start_point[1]:.5f})")
        out.append(f"    Depart at {seconds_to_time(int(depart_time))}")
        walk_min = start_walk / 60
        out.append(f"    - Walk {walk_min:.0f} min ({start_walk}s)")

    # Resolve town names for stops (if an AtcoLoader is available)
    try:
        stop_codes = [s for s, _ in legs]
        atco_codes = {merged.get_atco_code(s) for s in stop_codes if merged.get_atco_code(s)}
    except Exception:
        atco_codes = set()
    town_map = {}
    if getattr(merged, "atco", None) and atco_codes:
        try:
            town_map = merged.atco.get_stop_towns_bulk(atco_codes) or {}
        except Exception:
            town_map = {}

    def _label_with_town(idx: int) -> str:
        base = merged.stop_metadata[idx] if idx < len(merged.stop_metadata) else f"stop#{idx}"
        try:
            code = merged.get_atco_code(idx)
            town = town_map.get(code)
            if town and town.strip() and town not in base:
                return f"{base}, {town}"
        except Exception:
            pass
        return base

    for i, (stop_int, info) in enumerate(legs):
        stop_label = _label_with_town(stop_int)
        arrival = seconds_to_time(int(info["arrival_time"])) if info["arrival_time"] != float("inf") else "--:--:--"
        # Support both new 'mode' key and legacy 'type' key in route dicts
        transport = info.get("mode") or info.get("type") or "origin"

        if i == 0:
            out.append(f"  - {stop_label}")
            out.append(f"    Arrive at {arrival}")
        else:
            if transport == "walking":
                prev_stop_int, prev_info = legs[i - 1]
                walk_secs = info["arrival_time"] - prev_info["arrival_time"]
                walk_min = walk_secs / 60
                out.append(f"    - Walk {walk_min:.0f} min ({int(walk_secs)}s)")
            else:
                jinfo = info.get("journey_info")
                line_name = jinfo.get("line_name", "") if jinfo else ""
                if line_name and ":" in line_name:
                    line_name = line_name.split(":")[-1]
                j_origin = info.get("journey_origin", "")
                j_dest = info.get("journey_destination", "")
                board_dep = info.get("board_departure")

                # Use the same per-journey delay that the delay-
                # adjusted router applied so we can recover the
                # original scheduled time exactly.
                j_id = info.get("journey")
                delay_s = _journey_delay_map.get(j_id) if j_id is not None else None

                desc_parts = []
                if transport:
                    desc_parts.append(transport)
                if line_name:
                    desc_parts.append(f"line {line_name}")
                if j_origin and j_dest:
                    desc_parts.append(f"{j_origin} -> {j_dest}")

                delay_tag = ""
                if delay_s is not None and delay_s >= 60 and board_dep is not None:
                    sched_dep = seconds_to_time(int(board_dep - delay_s))
                    rt_dep = seconds_to_time(int(board_dep))
                    desc_parts.append(f"departs {sched_dep}")
                    desc_parts.append(f"expected {rt_dep}")
                    mins = round(delay_s / 60)
                    delay_tag = f" [Delayed {mins} min]"
                elif delay_s is not None:
                    if board_dep is not None:
                        desc_parts.append(f"departs {seconds_to_time(int(board_dep))}")
                    delay_tag = " [On time]"
                else:
                    if board_dep is not None:
                        desc_parts.append(f"departs {seconds_to_time(int(board_dep))}")

                desc = " - ".join(desc_parts) if desc_parts else transport
                out.append(f"    - {desc}{delay_tag}")

            out.append(f"  - {stop_label}")
            out.append(f"    Arrive at {arrival}")

    if destination and legs:
        walk_min = end_walk / 60
        out.append(f"    - Walk {walk_min:.0f} min ({end_walk}s)")
        out.append(f"  - Destination  ({destination[0]:.5f}, {destination[1]:.5f})")
        if total_arrival is not None:
            out.append(f"    Arrive at {seconds_to_time(int(total_arrival))}")

    out.append("\n" + "=" * 60)
    return "\n".join(out)


def build_journey_plan_response(route_result, merged, stop_coords, request_start_seconds=None, include_geometry: bool = False):
    """Convert raw RAPTOR router result into a structured journey plan.

    Args:
        route_result: dict returned by RaptorRouter.route()
        merged: MergedData instance for the current date/time bucket
        stop_coords: dict {stop_int: (lat, lon)}

    Returns:
        dict with keys: success, legs, meta, routeGeometries
    """
    from time_utils import seconds_to_time
    import math

    # Try to augment stop_coords with ATCO metadata for nodes missing from the walking graph
    atco_coords_dict = {}
    try:
        if globals().get('_base_cache') and _base_cache.get("atco_loader"):
            atco_coords_dict = _base_cache["atco_loader"].get_all_stop_coords()
    except Exception:
        pass

    def _get_stop_coord(s_idx):
        # 1. Direct from passed stop_coords (typically the walking graph)
        if isinstance(stop_coords, dict) and s_idx in stop_coords:
            return stop_coords[s_idx]
        # 2. Fall back to canonical ATCO coordinates
        try:
            a_code = merged.get_atco_code(s_idx)
            if a_code in atco_coords_dict:
                return atco_coords_dict[a_code]
        except Exception:
            pass
        return None

    # Best-effort: resolve a canonical route_id for a bus leg when routers
    # didn't provide journey_info.route_id. This allows geometry to use
    # stored timetable fragment geometry instead of falling back to OSRM/linear.
    # We do this by looking up known variants for the leg's line and selecting
    # a variant that contains both from/to ATCO codes in stop order.
    def _resolve_route_id_for_leg(line_name: str | None, from_atco: str | None, to_atco: str | None):
        if not line_name or not from_atco or not to_atco:
            return None

        def _get_line_variants_sync(line_key: str):
            """Best-effort sync access to /routes/line/{line} variants.

            build_journey_plan_response is synchronous; /routes/line/{line} is
            implemented as an async endpoint handler. Calling async code from
            here would create an un-awaited coroutine and silently break
            route_id resolution (leading to OSRM/linear geometry).

            We therefore use the same cached data structure that the endpoint
            populates: _route_line_cache.
            """
            try:
                lk = (line_key or '').strip().upper()
                if not lk:
                    return []
                data = _route_line_cache.get(lk)
                if isinstance(data, dict):
                    v = data.get('variants')
                    if isinstance(v, list):
                        return v
            except Exception:
                return []
            return []

        def _resolve_alias_route_id(rid: str) -> str:
            """Deprecated: DB alias resolution removed (in-memory only)."""
            return rid

        # Small in-process cache to avoid repeatedly scanning variants.
        cache = globals().setdefault('_line_variants_cache', {})
        key = (str(line_name),)
        if key in cache:
            variants = cache[key]
        else:
            variants = []
            try:
                variants = _get_line_variants_sync(str(line_name))
            except Exception:
                variants = []
            # Cache even empty lists for this process lifetime.
            cache[key] = variants

        best = None
        best_hops = None
        try:
            for var in variants:
                if not isinstance(var, dict):
                    continue
                rid = var.get('route_id')
                stops = var.get('stops') or []
                if not rid or not isinstance(stops, list) or len(stops) < 2:
                    continue
                atcos = [s.get('atco_code') for s in stops if isinstance(s, dict)]
                if from_atco not in atcos or to_atco not in atcos:
                    continue
                from_positions = [idx for idx, code in enumerate(atcos) if code == from_atco]
                to_positions = [idx for idx, code in enumerate(atcos) if code == to_atco]
                if not from_positions or not to_positions:
                    continue
                i = None
                j = None
                for fp in from_positions:
                    tj = next((tp for tp in to_positions if tp > fp), None)
                    if tj is not None:
                        i, j = fp, tj
                        break
                if i is None or j is None:
                    continue
                if j <= i:
                    continue
                hops = j - i
                if best is None or (best_hops is not None and hops < best_hops):
                    best = rid
                    best_hops = hops
        except Exception:
            return None
        return best

    if not route_result:
        return {
            "success": True,
            "legs": [],
            "journeys": [],
            "meta": {},
            "routeGeometries": [],
        }

    meta = route_result.get("_meta", {})
    # Derive a stable AM/PM bucket for track selection. This must mirror the
    # router-cache keying used elsewhere (get_router_for_date).
    # When request_start_seconds is unavailable, fall back to merged metadata.
    try:
        _bucket = None
        if request_start_seconds is not None:
            # convention: < 12:00 => AM else PM
            _bucket = 'AM' if int(request_start_seconds) < 12 * 3600 else 'PM'
        else:
            _bucket = getattr(merged, 'bucket', None)
        if _bucket not in ('AM', 'PM'):
            _bucket = None
    except Exception:
        _bucket = None
    # Date string should come from router construction (get_router_for_date)
    # which annotates merged.meta['date'].
    try:
        _date_str = None
        try:
            mm = getattr(merged, 'meta', None)
            if isinstance(mm, dict):
                _date_str = mm.get('date') or mm.get('date_str')
        except Exception:
            _date_str = None
        if not _date_str and isinstance(meta, dict):
            _date_str = meta.get('date')
    except Exception:
        _date_str = None

    # Trace the context we *think* we're using for geometry selection.
    if os.environ.get('ROUTE_GEOM_TRACE') == '1':
        try:
            print('[journey_geom] ctx', {
                'meta_date': meta.get('date') if isinstance(meta, dict) else None,
                'merged_meta_date': (getattr(merged, 'meta', {}) or {}).get('date') if isinstance(getattr(merged, 'meta', None), dict) else None,
                'computed_date': _date_str,
                'computed_bucket': _bucket,
                'request_start_seconds': request_start_seconds,
                'merged_has_date_str': bool(hasattr(merged, 'date_str')),
                'merged_has_bucket': bool(hasattr(merged, 'bucket')),
            })
        except Exception:
            pass

    # --- Walking-only route (only _meta key present) ---
    if set(route_result.keys()) == {"_meta"}:
        start = meta.get("start_point", ())
        dest = meta.get("destination", ())
        total_walk = (meta.get("start_walk_seconds", 0)
                      + meta.get("end_walk_seconds", 0))
        total_arrival = meta.get("total_arrival")

        from_loc = ({"lat": start[0], "lon": start[1]}
                     if len(start) >= 2 else None)
        to_loc = ({"lat": dest[0], "lon": dest[1]}
                   if len(dest) >= 2 else None)

        legs = [{
            "mode": "walking",
            "from_stop": from_loc,
            "to_stop": to_loc,
            "duration_seconds": total_walk,
            "departure_time": None,
            "arrival_time": (seconds_to_time(int(total_arrival))
                             if total_arrival else None),
            "departure_day_offset": 0,
            "arrival_day_offset": (int(total_arrival) // 86400) if total_arrival is not None else 0,
        }]

        coords = []
        if len(start) >= 2:
            coords.append([start[0], start[1]])
        if len(dest) >= 2:
            coords.append([dest[0], dest[1]])

        geometries = ([{
            "id": "walk-0",
            "name": "Walking",
            "coords": coords,
            "color": "#888888",
        }] if coords else [])

        # Compute a human-friendly duration for the walking-only route
        walk_dur_secs = total_walk
        if request_start_seconds is not None and total_arrival is not None:
            walk_dur_secs = int(total_arrival - int(request_start_seconds))
        if walk_dur_secs >= 3600:
            _wh = walk_dur_secs // 3600
            _wm = (walk_dur_secs % 3600) // 60
            walk_dur_str = f"{_wh}h {_wm} mins" if _wm > 0 else f"{_wh}h"
        else:
            walk_dur_str = f"{round(walk_dur_secs / 60)} mins"

        return {
            "success": True,
            "legs": legs,
            "journeys": [{"legs": legs}],
            "meta": {
                "start_walk_seconds": meta.get("start_walk_seconds", 0),
                "end_walk_seconds": meta.get("end_walk_seconds", 0),
                "total_arrival": (seconds_to_time(int(total_arrival))
                                  if total_arrival else None),
                "total_duration_seconds": walk_dur_secs,
                "total_duration": walk_dur_str,
            },
            "routeGeometries": geometries,
        }

    # --- Multi-stop transit route ---
    # Be defensive: router implementations should return a dict mapping
    # stop-index -> info-dict, but some implementations or edge-cases
    # may include unexpected non-dict values. Only process entries
    # whose values are dict-like to avoid TypeErrors.
    # Router implementations differ:
    # - Some return a dict mapping stop_int -> info-dict for ONLY the chosen best path.
    # - Others return a dict mapping stop_int -> list/structure of multiple route options.
    # This function expects the first form (single best path). If the second form
    # sneaks in, we still try to behave sensibly.
    route_data = {
        k: v
        for k, v in route_result.items()
        if k not in ("_meta", "_route_id", "_route_int") and isinstance(v, dict)
    }

    all_prevs = {
        info.get("prev_stop")
        for info in route_data.values()
        if isinstance(info, dict) and info.get("prev_stop") is not None
    }
    destinations = [s for s in route_data if s not in all_prevs]
    if not destinations:
        destinations = list(route_data.keys())

    if not destinations:
        # No usable route data (e.g. unexpected router output).
        return {
            "success": True,
            "legs": [],
            "journeys": [],
            "meta": {},
            "routeGeometries": [],
        }

    stop = destinations[0]
    visited = set()
    ordered = []
    while stop is not None and stop not in visited:
        visited.add(stop)
        ordered.append((stop, route_data[stop]))
        stop = route_data[stop]["prev_stop"]
    ordered.reverse()

    def _stop_name(idx):
        if idx < len(merged.stop_metadata):
            return merged.stop_metadata[idx]
        return f"stop#{idx}"

    def _stop_point(idx: int):
        """Return a stop dict including coordinates and stable identifiers.

        The frontend needs a stop id (ATCO code) to request a bus-leg geometry
        segment. Provide it consistently on every from_stop / to_stop object.
        """
        try:
            atco = merged.get_atco_code(idx)
        except Exception:
            atco = None
        lat = lon = None
        try:
            coord = _get_stop_coord(idx)
            if coord:
                lat, lon = coord
        except Exception:
            lat = lon = None
        out = {
            "name": _display_name(idx),
            "lat": lat,
            "lon": lon,
        }
        if atco:
            out["id"] = atco
            out["atco_code"] = atco
        try:
            cls = classification_lookup.get(idx)
            if cls:
                out["classification"] = cls
        except Exception:
            pass
        return out

    # Try to resolve town names for stops using the MergedData's
    # AtcoLoader (if available).  We'll use this to append ", Town"
    # to displayed stop names when a town is present in the DB.
    try:
        ordered_stop_indices = [s for s, _ in ordered]
        atco_codes_for_ordered = {merged.get_atco_code(s) for s in ordered_stop_indices if merged.get_atco_code(s)}
    except Exception:
        atco_codes_for_ordered = set()
    town_map = {}
    if getattr(merged, "atco", None) and atco_codes_for_ordered:
        try:
            town_map = merged.atco.get_stop_towns_bulk(atco_codes_for_ordered) or {}
        except Exception:
            town_map = {}

    # Build a fast lookup of stop_index -> classification so callers
    # can attach human-friendly station classes (hub/interchange/local/request_stop)
    # to every stop returned in the journey legs. This uses the
    # station_classifier module which operates on the MergedData object.
    try:
        # Prefer a cached per-merged lookup to avoid expensive recomputation
        classification_lookup = _classification_for_merged(merged)
    except Exception:
        classification_lookup = {}

    def _display_name(idx: int) -> str:
        base = _stop_name(idx)
        try:
            code = merged.get_atco_code(idx)
            town = town_map.get(code)
            if town and town.strip() and town not in base:
                return f"{base}, {town}"
        except Exception:
            pass
        return base

    def _time_str(secs):
        if secs is None or secs == math.inf:
            return None
        return seconds_to_time(int(secs))

    def _mode_from_info(info: dict, default: str = "unknown") -> str:
        """Resolve mode from canonical journey type when possible."""
        try:
            j_id = info.get("journey")
            if j_id is not None:
                j_mode = merged.journey_type(int(j_id))
                if j_mode == BUS:
                    return "bus"
                if j_mode == TRAIN:
                    return "train"
        except Exception:
            pass

        mode = (info.get("mode") or info.get("type") or "").lower()
        return mode or default

    _COLOR = {"walking": "#888888", "bus": "#1a73e8", "train": "#e53935"}
    legs = []
    geometries = []
    geo_idx = 0

    # Incrementing index of the leg we’re currently building for the
    # ordered stop sequence. This lets us label `routeGeometries` entries
    # deterministically so the frontend can align geometry segments to legs
    # without relying on list position or heuristic matching.
    leg_idx = 0

    start_point = meta.get("start_point", ())
    start_walk = meta.get("start_walk_seconds", 0)
    end_walk = meta.get("end_walk_seconds", 0)
    total_arrival = meta.get("total_arrival")
    destination_point = meta.get("destination", ())

    # Boarding buffers (seconds) used to compute an adjusted arrival
    # at the first stop: match router boarding buffers (bus/train)
    BUS_BOARD_BUFFER = 60
    TRAIN_BOARD_BUFFER = 180

    # Track an explicit initial departure (seconds since midnight) when
    # we compute it for the start walking leg. Initialise to None so
    # it's available later when computing total duration.
    initial_departure_secs = None

    # -- Start walking leg --
    if start_point and len(start_point) >= 2 and start_walk > 0 and ordered:
        first_int = ordered[0][0]
        first_coord = _get_stop_coord(first_int)
        first_name = _display_name(first_int)
        to_loc = {"name": first_name}
        if first_coord:
            to_loc["lat"] = first_coord[0]
            to_loc["lon"] = first_coord[1]
        # Attach classification when we can resolve the merged stop index
        try:
            cls = classification_lookup.get(first_int)
            if cls:
                to_loc["classification"] = cls
        except Exception:
            pass

        # Adjust the arrival at the first stop to be the vehicle's
        # departure minus a boarding buffer when the next leg is a
        # transit leg. This models the user arriving early enough to
        # board (e.g. 60s for bus, 180s for train).
        first_arrival_secs = ordered[0][1]["arrival_time"]
        if len(ordered) > 1:
            next_info = ordered[1][1]
            next_mode = _mode_from_info(next_info, default="bus")
            board_dep = next_info.get("board_departure")
            if board_dep is not None and board_dep < float("inf"):
                if next_mode == "bus":
                    buf = BUS_BOARD_BUFFER
                elif next_mode == "train":
                    buf = TRAIN_BOARD_BUFFER
                else:
                    buf = BUS_BOARD_BUFFER
                # ensure non-negative
                first_arrival_secs = max(0, int(board_dep) - int(buf))

        # Compute when the user should depart from the start point
        initial_departure_secs = None
        if first_arrival_secs is not None:
            try:
                initial_departure_secs = int(first_arrival_secs) - int(start_walk)
            except Exception:
                initial_departure_secs = None

        legs.append({
            "mode": "walking",
            "from_stop": {"name": "Start", "lat": start_point[0],
                          "lon": start_point[1]},
            "to_stop": to_loc,
            "duration_seconds": start_walk,
            "departure_time": _time_str(initial_departure_secs) if initial_departure_secs is not None else None,
            "arrival_time": _time_str(first_arrival_secs),
            # integer day offsets (0 = same day, 1 = next day, ...)
            "departure_day_offset": (int(initial_departure_secs) // 86400) if initial_departure_secs is not None else 0,
            "arrival_day_offset": (int(first_arrival_secs) // 86400) if first_arrival_secs is not None else 0,
        })
        wc = [[start_point[0], start_point[1]]]
        if first_coord:
            wc.append([first_coord[0], first_coord[1]])
        geometries.append({
            "id": f"walk-{geo_idx}",
            "name": f"Walk to {first_name}",
            "coords": wc,
            "color": "#888888",
            "mode": "walking",
            "leg_idx": leg_idx,
            "from_stop_id": None,
            "to_stop_id": (to_loc.get("id") if isinstance(to_loc, dict) else None),
            "from_stop_name": "Start",
            "to_stop_name": (to_loc.get("name") if isinstance(to_loc, dict) else None),
        })
        geo_idx += 1
        leg_idx += 1

    # -- Transit / walking legs between stops --
    for i in range(1, len(ordered)):
        prev_int, prev_info = ordered[i - 1]
        curr_int, curr_info = ordered[i]
        transport = _mode_from_info(curr_info)
        prev_coord = _get_stop_coord(prev_int)
        curr_coord = _get_stop_coord(curr_int)

    # Include a stable stop id (ATCO code) so the frontend can request
    # bus geometry segments.
        from_loc = _stop_point(prev_int)
        to_loc = _stop_point(curr_int)

        leg = {
            "mode": transport,
            "from_stop": from_loc,
            "to_stop": to_loc,
            "arrival_time": _time_str(curr_info["arrival_time"]),
            "arrival_day_offset": (int(curr_info["arrival_time"]) // 86400) if curr_info.get("arrival_time") is not None else 0,
        }

        line_name = ""
        if transport == "walking":
            walk_secs = curr_info["arrival_time"] - prev_info["arrival_time"]
            leg["duration_seconds"] = int(walk_secs)
            leg["departure_time"] = _time_str(prev_info["arrival_time"])
            leg["line_name"] = None
        else:
            j_id = curr_info.get("journey")
            intermediate_stops = []
            if j_id is not None and getattr(merged, "journey_times", None) and getattr(merged, "journey_stop_index", None):
                try:
                    jt = merged.journey_times[j_id]
                    jsi = merged.journey_stop_index[j_id]
                    start_idx = jsi.get(prev_int)
                    end_idx = jsi.get(curr_int)
                    
                    if start_idx is not None and end_idx is not None:
                        # Allow normal forward loop
                        if start_idx < end_idx:
                            for idx in range(start_idx + 1, end_idx):
                                stop_int = jt[idx][0]
                                intermediate_stops.append(_stop_point(stop_int))
                except Exception:
                    pass
            leg["intermediate_stops"] = intermediate_stops

            j_info = curr_info.get("journey_info")
            if j_info and isinstance(j_info, dict):
                line_name = j_info.get("line_name", "")
                if line_name and ":" in line_name:
                    line_name = line_name.split(":")[-1]
            leg["line_name"] = line_name or None
            leg["journey_origin"] = curr_info.get("journey_origin", "") or None
            leg["journey_destination"] = (
                curr_info.get("journey_destination", "") or None)
            board_dep = curr_info.get("board_departure")
            arr_secs = curr_info["arrival_time"]

            # ── Real-time delay annotation ───────────────────────
            # The router was built with apply_delay=True so
            # board_dep and arr_secs already include the per-journey
            # delay from _journey_delay_map.  To recover the correct
            # scheduled vs realtime split we must use the SAME delay
            # value that was applied (per-journey), NOT a line-level
            # median which may differ.
            j_id = curr_info.get("journey")
            delay_s = _journey_delay_map.get(j_id) if j_id is not None else None

            if delay_s is not None and delay_s != 0:
                # Scheduled = undo the shift that the delay-adjusted
                # router already applied.
                sched_dep = (board_dep - delay_s) if board_dep is not None else None
                sched_arr = (arr_secs - delay_s) if arr_secs < math.inf else None

                leg["departure_time"] = _time_str(sched_dep)
                leg["arrival_time"] = _time_str(sched_arr) if sched_arr is not None else leg["arrival_time"]
                leg["scheduled_departure_time"] = leg["departure_time"]
                leg["scheduled_arrival_time"] = leg["arrival_time"]

                leg["delay_seconds"] = delay_s
                leg["status"] = _bus_delay_status(delay_s)

                # Realtime = the value already produced by the delay-
                # adjusted router (i.e. board_dep / arr_secs as-is).
                leg["realtime_departure_time"] = _time_str(board_dep) if board_dep is not None else leg["departure_time"]
                leg["realtime_arrival_time"] = _time_str(arr_secs) if arr_secs < math.inf else leg["arrival_time"]
            else:
                # No delay or unknown — scheduled == realtime
                leg["departure_time"] = _time_str(board_dep)
                leg["arrival_time"] = _time_str(arr_secs) if arr_secs < math.inf else leg.get("arrival_time")
                leg["scheduled_departure_time"] = leg["departure_time"]
                leg["scheduled_arrival_time"] = leg["arrival_time"]
                leg["delay_seconds"] = 0 if delay_s == 0 else None
                leg["status"] = "On time" if delay_s is not None else None
                leg["realtime_departure_time"] = leg["departure_time"]
                leg["realtime_arrival_time"] = leg["arrival_time"]

            dur_start = board_dep if board_dep else prev_info["arrival_time"]
            if (arr_secs < math.inf and dur_start < math.inf):
                leg["duration_seconds"] = int(arr_secs - dur_start)
            else:
                leg["duration_seconds"] = None

            # Attach integer day offsets for departure/arrival so the
            # frontend can render multi-day annotations. If a specific
            # board_dep wasn't available use the previous stop arrival
            # time as the departure reference.
            try:
                dep_secs_val = board_dep if board_dep is not None else prev_info.get("arrival_time")
                leg["departure_day_offset"] = (int(dep_secs_val) // 86400) if dep_secs_val is not None else 0
            except Exception:
                leg["departure_day_offset"] = 0
            try:
                leg["arrival_day_offset"] = (int(arr_secs) // 86400) if arr_secs is not None and arr_secs != float("inf") else 0
            except Exception:
                leg["arrival_day_offset"] = 0

        legs.append(leg)

        # -- geometry for this leg --
        color = _COLOR.get(transport, "#666666")
        if transport == "walking":
            geo_name = "Walk"
        elif line_name:
            geo_name = f"{transport.title()} {line_name}"
        else:
            geo_name = transport.title() if transport else "Unknown"
        coords = []

        route_id = None
        route_int = None
        track_coords = None
        geom_source = None
        trace = os.environ.get('ROUTE_GEOM_TRACE') == '1'
        if transport != "walking":
            try:
                j_info = curr_info.get("journey_info") or {}
                if isinstance(j_info, dict) and j_info.get("route_id"):
                    route_id = j_info.get("route_id")
                # Prefer route_int for direct in-memory geometry lookup.
                try:
                    j_id_tmp = curr_info.get('journey')
                    if j_id_tmp is not None and hasattr(merged, 'journey_to_route') and j_id_tmp < len(merged.journey_to_route):
                        r_tmp = merged.journey_to_route[j_id_tmp]
                        if r_tmp is not None and int(r_tmp) >= 0:
                            route_int = int(r_tmp)
                except Exception:
                    route_int = None
            except Exception as exc:
                pass

            # Geometry context: persist the exact merged identity + dense identifiers
            # (after we've resolved route_int) so debugging and downstream geometry
            # selection are self-consistent.
            try:
                leg.setdefault("geom_context", {})
                if isinstance(leg.get("geom_context"), dict):
                    _ctx_bucket = getattr(merged, 'bucket', None) or _bucket
                    if _ctx_bucket not in ('AM', 'PM'):
                        _ctx_bucket = _bucket
                    leg["geom_context"].update({
                        "date": _date_str,
                        "bucket": _ctx_bucket,
                        "merged_key": (f"{_date_str}|{_ctx_bucket}" if _date_str and _ctx_bucket else None),
                        "route_int": route_int,
                        "from_stop_int": prev_int,
                        "to_stop_int": curr_int,
                    })

                if os.environ.get('ROUTE_GEOM_TRACE') == '1':
                    try:
                        print('[journey_geom] leg_geom_context', {
                            'leg_idx': leg_idx,
                            'transport': transport,
                            'route_int': route_int,
                            'ctx': leg.get('geom_context') if isinstance(leg, dict) else None,
                        })
                    except Exception:
                        pass
            except Exception:
                pass

            if (not route_id) and transport == 'bus':
                # Router didn't provide route_id, attempt best-effort resolution.
                try:
                    prev_atco = merged.get_atco_code(prev_int)
                except Exception:
                    prev_atco = None
                try:
                    curr_atco = merged.get_atco_code(curr_int)
                except Exception:
                    curr_atco = None
                try:
                    line_name = leg.get('line_name')
                except Exception:
                    line_name = None
                route_id = _resolve_route_id_for_leg(line_name, prev_atco, curr_atco)

                try:
                    prev_atco = prev_atco or merged.get_atco_code(prev_int)
                except Exception:
                    prev_atco = None

                try:
                    curr_atco = curr_atco or merged.get_atco_code(curr_int)
                except Exception:
                    curr_atco = None

                # (handled below) if route_id resolved we will attempt slicing

    # If we have a geometry candidate (prefer route_int) and we know stop ids,
    # try to obtain a segment between the two stops.
        if transport != "walking":
            try:
                prev_atco = None
                curr_atco = None
                try:
                    prev_atco = merged.get_atco_code(prev_int)
                except Exception:
                    prev_atco = None
                try:
                    curr_atco = merged.get_atco_code(curr_int)
                except Exception:
                    curr_atco = None

                if trace:
                    try:
                        print('[journey_geom] leg', {
                            'leg_idx': leg_idx,
                            'transport': transport,
                            'line_name': (leg.get('line_name') if isinstance(leg, dict) else None),
                            'route_id': route_id,
                            'route_int': route_int,
                            'from_stop_int': prev_int,
                            'to_stop_int': curr_int,
                            'from_atco': prev_atco,
                            'to_atco': curr_atco,
                            'from_name': (from_loc.get('name') if isinstance(from_loc, dict) else None),
                            'to_name': (to_loc.get('name') if isinstance(to_loc, dict) else None),
                        })
                    except Exception:
                        pass

                if prev_atco and curr_atco:
                    local_coords = {}
                    if prev_coord:
                        local_coords[prev_atco] = prev_coord
                    if curr_coord:
                        local_coords[curr_atco] = curr_coord

                    seg = []
                    if route_int is not None:
                        try:
                            # Prefer stop-to-stop fragment tracks if present.
                            # These are indexed by (from_stop_int,to_stop_int)
                            # and avoid the "jumbled full route polyline" problem.
                            frag = None
                            try:
                                # Prefer a lazy accessor when available; otherwise
                                # fall back to the raw attribute.
                                if hasattr(merged, 'get_route_link_tracks'):
                                    link_map = merged.get_route_link_tracks(route_int)
                                else:
                                    links = getattr(merged, 'route_link_tracks', None)
                                    link_map = links[route_int] if (links and route_int < len(links)) else None

                                if trace:
                                    try:
                                        print('[journey_geom] link_map_status', {
                                            'route_int': route_int,
                                            'has_get_route_link_tracks': bool(hasattr(merged, 'get_route_link_tracks')),
                                            'link_map_is_none': (link_map is None),
                                            'link_map_len': (len(link_map) if link_map else 0),
                                        })
                                    except Exception:
                                        pass

                                if link_map:
                                    # We only have ATCO strings here; resolve to stop_int.
                                    # NOTE: merged.stop_metadata is a list of stop display names,
                                    # not an ATCO->stop_int mapping, so we map within route_stops.
                                    fs = None
                                    ts = None
                                    route_stops = []
                                    try:
                                        route_stops = merged.route_stops[route_int] if route_int < len(merged.route_stops) else []
                                    except Exception:
                                        route_stops = []
                                    if trace:
                                        try:
                                            print('[journey_geom] route_stops_status', {
                                                'route_int': route_int,
                                                'route_stops_len': (len(route_stops) if route_stops else 0),
                                                'have_prev_atco': bool(prev_atco),
                                                'have_curr_atco': bool(curr_atco),
                                            })
                                        except Exception:
                                            pass
                                    i = None
                                    j = None
                                    try:
                                        if route_stops and (prev_atco or curr_atco):
                                            resolved = _resolve_route_stop_occurrences(
                                                merged,
                                                route_stops,
                                                prev_atco,
                                                curr_atco,
                                            )
                                            fs = resolved.get('from_stop_int')
                                            ts = resolved.get('to_stop_int')
                                            i = resolved.get('from_pos')
                                            j = resolved.get('to_pos')
                                    except Exception:
                                        fs = None
                                        ts = None
                                        i = None
                                        j = None

                                    if trace:
                                        try:
                                            print('[journey_geom] fragment_endpoint_resolution', {
                                                'route_int': route_int,
                                                'prev_atco': prev_atco,
                                                'curr_atco': curr_atco,
                                                'fs': fs,
                                                'ts': ts,
                                                'fs_in_route_stops': (fs in route_stops) if (route_stops and fs is not None) else False,
                                                'ts_in_route_stops': (ts in route_stops) if (route_stops and ts is not None) else False,
                                            })
                                        except Exception:
                                            pass
                                    if fs is not None and ts is not None:
                                        frag = None
                                        frag_strategy = None

                                        # Primary: stitch adjacent fragments along route stop sequence.
                                        # This handles cases where section tracks are keyed by
                                        # intermediate timing points rather than the leg endpoints.
                                        # route_stops already resolved above.
                                        if route_stops and i is not None and j is not None and fs != ts:
                                            step = 1 if j > i else -1
                                            stitched = []
                                            ok = True
                                            k = i
                                            # stitch consecutive stop pairs along the route
                                            while k != j:
                                                a = route_stops[k]
                                                b = route_stops[k + step]
                                                seg2 = link_map.get((a, b))
                                                if not seg2:
                                                    # allow reverse if needed
                                                    rev2 = link_map.get((b, a))
                                                    if rev2:
                                                        seg2 = list(reversed(rev2))
                                                if not seg2:
                                                    ok = False
                                                    break
                                                if stitched and seg2 and stitched[-1] == seg2[0]:
                                                    stitched.extend(seg2[1:])
                                                else:
                                                    stitched.extend(seg2)
                                                k += step
                                            if ok and len(stitched) >= 2:
                                                frag = stitched
                                                frag_strategy = 'stitched_adjacent_pairs'

                                        if trace:
                                            try:
                                                print('[journey_geom] route_int_fragment_ctx', {
                                                    'route_int': route_int,
                                                    'from_stop_int': fs,
                                                    'to_stop_int': ts,
                                                    'route_stops_len': (len(route_stops) if route_stops else 0),
                                                    'route_stops_i': (route_stops.index(fs) if (route_stops and fs in route_stops) else None),
                                                    'route_stops_j': (route_stops.index(ts) if (route_stops and ts in route_stops) else None),
                                                    'link_map_len': (len(link_map) if link_map else 0),
                                                    'strategy': frag_strategy,
                                                })
                                            except Exception:
                                                pass

                                        # Secondary: exact stop-pair fragment (either direction)
                                        if not frag:
                                            frag = link_map.get((fs, ts))
                                            if not frag:
                                                # Try reverse direction.
                                                rev = link_map.get((ts, fs))
                                                if rev:
                                                    frag = list(reversed(rev))
                                                    frag_strategy = 'exact_pair_reversed'
                                            else:
                                                frag_strategy = 'exact_pair'

                                        if trace and (not frag):
                                            try:
                                                # sample a few available keys for debugging
                                                some_keys = []
                                                try:
                                                    for _k in link_map.keys():
                                                        some_keys.append(_k)
                                                        if len(some_keys) >= 5:
                                                            break
                                                except Exception:
                                                    some_keys = []
                                                print('[journey_geom] fragment_pair_missing', {
                                                    'route_int': route_int,
                                                    'fs': fs,
                                                    'ts': ts,
                                                    'sample_keys': some_keys,
                                                })
                                            except Exception:
                                                pass

                                        # Tertiary: 1-hop near endpoints.
                                        if not frag and route_stops:
                                            if fs in route_stops:
                                                try:
                                                    i = route_stops.index(fs)
                                                    for step in (1, -1):
                                                        if 0 <= i + step < len(route_stops):
                                                            b = route_stops[i + step]
                                                            seg2 = link_map.get((fs, b))
                                                            if not seg2:
                                                                rev2 = link_map.get((b, fs))
                                                                if rev2:
                                                                    seg2 = list(reversed(rev2))
                                                            if seg2 and len(seg2) >= 2:
                                                                frag = seg2
                                                                frag_strategy = 'near_endpoint_from'
                                                                break
                                                except Exception:
                                                    pass
                                            if (not frag) and ts in route_stops:
                                                try:
                                                    j = route_stops.index(ts)
                                                    for step in (1, -1):
                                                        if 0 <= j + step < len(route_stops):
                                                            a = route_stops[j + step]
                                                            seg2 = link_map.get((a, ts))
                                                            if not seg2:
                                                                rev2 = link_map.get((ts, a))
                                                                if rev2:
                                                                    seg2 = list(reversed(rev2))
                                                            if seg2 and len(seg2) >= 2:
                                                                frag = seg2
                                                                frag_strategy = 'near_endpoint_to'
                                                                break
                                                except Exception:
                                                    pass
                            except Exception:
                                frag = None

                            if frag and len(frag) >= 2:
                                tracks_ll = [[t[0], t[1]] for t in frag]
                                seg = tracks_ll
                                if trace:
                                    try:
                                        print('[journey_geom] route_int_fragment', {
                                            'route_int': route_int,
                                            'frag_len': len(frag),
                                            'strategy': (locals().get('frag_strategy') if 'frag_strategy' in locals() else None),
                                        })
                                    except Exception:
                                        pass
                            # IMPORTANT: do not fall back to slicing a full-route polyline here.
                            # Full-route polylines can be branched/jumbled and lead to
                            # "teleport" segments on loops. If we can't obtain link
                            # fragments, treat geometry as unavailable so callers can
                            # handle it (e.g. OSRM or linear fallback) rather than
                            # drawing misleading map lines.
                            if trace and (not seg):
                                try:
                                    print('[journey_geom] no_link_fragments', {
                                        'route_int': route_int,
                                        'link_map_len': (len(link_map) if link_map else 0),
                                    })
                                except Exception:
                                    pass
                        except Exception:
                            seg = []
                used_full_route_polyline = False
                if seg and len(seg) >= 2:
                    track_coords = seg
                    if trace:
                        try:
                            print('[journey_geom] sliced', {
                                'len': len(seg),
                                'first': seg[0],
                                'last': seg[-1],
                            })
                        except Exception:
                            pass
                elif trace:
                    try:
                        print('[journey_geom] slice_failed', {
                            'have_prev_atco': bool(prev_atco),
                            'have_curr_atco': bool(curr_atco),
                            'have_prev_coord': bool(prev_coord),
                            'have_curr_coord': bool(curr_coord),
                        })
                    except Exception:
                        pass
            except Exception:
                pass

        if track_coords:
            coords = track_coords
            # If we reached this point because we stitched stop-to-stop link fragments,
            # label it explicitly.
            if transport == 'bus':
                geom_source = "route_link_tracks"
            else:
                geom_source = geom_source or "computed"
        else:
            # Per-leg fallback: for missing tracks, fall back to OSRM (best-effort)
            # and then to a straight line. This is intentionally per-leg so a
            # single missing track doesn't degrade the whole route.
            try:
                if prev_coord and curr_coord:
                    mode_hint = "walking" if transport == "walking" else "driving"

                    # If this is a transit leg (bus/train) and we know the route's
                    # stop sequence, pass it through so /route/leg-geometry can
                    # fall back to stop-derived geometry (all intermediate stops)
                    # instead of a misleading 2-point linear segment.
                    stop_seq_atcos: str | None = None
                    try:
                        if transport != "walking" and route_int is not None:
                            # Slice only the stop sub-sequence between the leg
                            # endpoints (hop-on -> hop-off) so we don't draw the
                            # entire route as the fallback geometry.
                            rs = merged.route_stops[route_int] if route_int < len(getattr(merged, 'route_stops', []) or []) else []
                            if rs and len(rs) >= 2:
                                # prev_int/curr_int are the merged stop_ints for this leg
                                if prev_int in rs and curr_int in rs and prev_int != curr_int:
                                    i0 = rs.index(prev_int)
                                    i1 = rs.index(curr_int)
                                    if i1 >= i0:
                                        sub = rs[i0:i1 + 1]
                                    else:
                                        # Reverse travel along stop order
                                        sub = list(reversed(rs[i1:i0 + 1]))
                                else:
                                    sub = rs

                                atcos = []
                                for s_int in sub:
                                    try:
                                        c = merged.get_atco_code(s_int)
                                    except Exception:
                                        c = None
                                    if c:
                                        atcos.append(str(c).strip())
                                if len(atcos) >= 2:
                                    stop_seq_atcos = ",".join(atcos)
                    except Exception:
                        stop_seq_atcos = None

                    lg = route_leg_geometry(
                        prev_coord[0], prev_coord[1],
                        curr_coord[0], curr_coord[1],
                        mode=mode_hint,
                        route_id=route_id,
                        route_int=route_int,
                        merged_key=(
                            f"{_date_str}|{(getattr(merged, 'bucket', None) or _bucket)}"
                            if _date_str and (getattr(merged, 'bucket', None) or _bucket) in ('AM', 'PM')
                            else None
                        ),
                        merged=merged,
                        from_stop_id=prev_atco if 'prev_atco' in locals() else None,
                        to_stop_id=curr_atco if 'curr_atco' in locals() else None,
                        stop_ids=stop_seq_atcos,
                    )
                    if isinstance(lg, dict) and isinstance(lg.get("coords"), list) and len(lg.get("coords")) >= 2:
                        coords = lg.get("coords")
                        geom_source = lg.get("source")
                        try:
                            if isinstance(lg.get('diag'), dict):
                                # Stash on the leg; we'll copy it into the final
                                # leg['geometry'] payload when we embed geometry.
                                leg['_geometry_diag'] = lg.get('diag')
                        except Exception:
                            pass
            except Exception:
                pass

            if not coords:
                if prev_coord:
                    coords.append([prev_coord[0], prev_coord[1]])
                if curr_coord:
                    coords.append([curr_coord[0], curr_coord[1]])
                geom_source = geom_source or "linear"

        # If bus link fragments were unavailable, surface a clear note for debugging.
        try:
            if transport == 'bus' and geom_source in (None, 'linear', 'osrm'):
                if 'link_map' in locals() and isinstance(locals().get('link_map'), dict) and len(locals().get('link_map')) == 0:
                    leg.setdefault('geometry_note', 'no_link_fragments')
        except Exception:
            pass

        # Attach geometry source to the leg so clients can debug mixed sources.
        try:
            if geom_source:
                leg["geometry_source"] = geom_source
        except Exception:
            pass

        # Optionally embed geometry directly into the leg. This keeps the
        # /journey/* endpoints self-contained for map rendering (no secondary
        # /route/leg-geometry calls needed).
        try:
            if include_geometry and coords and len(coords) >= 2:
                leg["geometry"] = {
                    "coords": coords,
                    "source": geom_source or "linear",
                }
                try:
                    if isinstance(leg.get('_geometry_diag'), dict):
                        leg['geometry']['diag'] = leg.get('_geometry_diag')
                except Exception:
                    pass
        except Exception:
            pass

        # Cleanup internal-only fields.
        try:
            leg.pop('_geometry_diag', None)
        except Exception:
            pass

        geometries.append({
            "id": f"{transport}-{geo_idx}",
            "name": geo_name,
            "coords": coords,
            "color": color,
            "mode": transport,
            "leg_idx": leg_idx,
            "from_stop_id": (from_loc.get("id") if isinstance(from_loc, dict) else None),
            "to_stop_id": (to_loc.get("id") if isinstance(to_loc, dict) else None),
            "from_stop_name": (from_loc.get("name") if isinstance(from_loc, dict) else None),
            "to_stop_name": (to_loc.get("name") if isinstance(to_loc, dict) else None),
            "source": geom_source,
        })
        geo_idx += 1
        leg_idx += 1

    # -- End walking leg --
    if (destination_point and len(destination_point) >= 2
            and end_walk > 0 and ordered):
        last_int = ordered[-1][0]
        last_coord = _get_stop_coord(last_int)
        last_name = _display_name(last_int)
        from_loc = {"name": last_name}
        if last_coord:
            from_loc["lat"] = last_coord[0]
            from_loc["lon"] = last_coord[1]
        try:
            lcls = classification_lookup.get(last_int)
            if lcls:
                from_loc["classification"] = lcls
        except Exception:
            pass
        dep_secs = ordered[-1][1]["arrival_time"] if ordered and ordered[-1][1].get("arrival_time") is not None else None
        legs.append({
            "mode": "walking",
            "from_stop": from_loc,
            "to_stop": {"name": "Destination",
                        "lat": destination_point[0],
                        "lon": destination_point[1]},
            "duration_seconds": end_walk,
            "departure_time": _time_str(dep_secs),
            "arrival_time": _time_str(total_arrival),
            "departure_day_offset": (int(dep_secs) // 86400) if dep_secs is not None else 0,
            "arrival_day_offset": (int(total_arrival) // 86400) if total_arrival is not None else 0,
        })
        wc = []
        if last_coord:
            wc.append([last_coord[0], last_coord[1]])
        wc.append([destination_point[0], destination_point[1]])
        geometries.append({
            "id": f"walk-{geo_idx}",
            "name": "Walk to destination",
            "coords": wc,
            "color": "#888888",
            "mode": "walking",
            "leg_idx": leg_idx,
            "from_stop_id": (from_loc.get("id") if isinstance(from_loc, dict) else None),
            "to_stop_id": None,
            "from_stop_name": (from_loc.get("name") if isinstance(from_loc, dict) else None),
            "to_stop_name": "Destination",
        })

    # Compute the total real-time delay for the route.
    # The router already used delay-adjusted journey_times, so
    # total_arrival already includes the cumulative delay. When
    # computing the scheduled total we must subtract each unique
    # per-journey delay once. Previously we summed per-leg
    # delays which double-counted delays when a single vehicle
    # (journey) produced multiple legs — fix by summing unique
    # journey delays only.
    seen_journey_delays: dict = {}
    has_any_delay = False
    # During leg construction we may not have stored the journey id
    # on the leg itself, so iterate the route_data (ordered) to find
    # unique journey ids and their delay_seconds where present.
    for idx, (stop_int, info) in enumerate(ordered):
        j_id = info.get('journey')
        if j_id is None:
            continue
        # Delay per-journey is stored in _journey_delay_map keyed by journey id
        ds = _journey_delay_map.get(j_id)
        if ds is not None and ds != 0:
            seen_journey_delays[j_id] = ds
            has_any_delay = True

    total_delay_s = sum(seen_journey_delays.values()) if seen_journey_delays else 0
    scheduled_total = None
    rt_total_arrival = None

    # New total-time calculation: if the caller provided the request start
    # time (seconds since midnight) compute the total travel duration as
    # final arrival minus start time.  This is simpler and avoids
    # aggregating delays across legs.  If request_start_seconds is not
    # provided fall back to the previous behaviour (subtract unique
    # per-journey delays to compute a scheduled arrival time).
    # Compute total_duration_seconds (arrival − request start) and a
    # human-friendly total_duration string.  These are the canonical
    # "how long does this journey take" fields for the frontend.
    total_duration_secs = None
    total_duration_str = None

    # Prefer using the computed initial departure time when available to
    # compute the canonical total journey duration. Fall back to the
    # request_start_seconds when initial departure isn't present.
    start_ref = None
    if initial_departure_secs is not None:
        start_ref = int(initial_departure_secs)
    elif request_start_seconds is not None:
        start_ref = int(request_start_seconds)

    if total_arrival is not None and start_ref is not None:
        total_duration_secs = int(total_arrival - int(start_ref))
        # Human-friendly duration string (e.g. "14 mins" or "1h 10 mins")
        if total_duration_secs >= 3600:
            hrs = total_duration_secs // 3600
            mins = (total_duration_secs % 3600) // 60
            total_duration_str = f"{hrs}h {mins} mins" if mins > 0 else f"{hrs}h"
        else:
            total_duration_str = f"{round(total_duration_secs / 60)} mins"
        rt_total_arrival = _time_str(int(total_arrival))
        scheduled_total = _time_str(int(total_arrival))
    else:
        if total_arrival is not None and has_any_delay:
            scheduled_total = _time_str(int(total_arrival - total_delay_s))
            rt_total_arrival = _time_str(int(total_arrival))
        elif total_arrival is not None:
            scheduled_total = _time_str(int(total_arrival))

    # Build the response object but persist a minimal logged_journey
    # first for router implementations that did not already attach one.
    resp = {
        "success": True,
        "legs": legs,
        "journeys": [{"legs": legs}],
        "meta": {
            "start_walk_seconds": start_walk,
            "end_walk_seconds": end_walk,
            "total_arrival": scheduled_total,
            "realtime_total_arrival": rt_total_arrival,
            "total_delay_seconds": total_delay_s if has_any_delay else None,
            "total_duration_seconds": total_duration_secs,
            "total_duration": total_duration_str,
            "start_point": list(start_point) if start_point else None,
            "destination": (list(destination_point)
                            if destination_point else None),
            # Expose a dense route_int for direct in-memory geometry lookup.
            "route_int": (route_result.get("_route_int") if isinstance(route_result, dict) else None),
            "initial_departure_time": _time_str(initial_departure_secs) if 'initial_departure_secs' in locals() and initial_departure_secs is not None else None,
            "initial_departure_day_offset": (int(initial_departure_secs) // 86400) if 'initial_departure_secs' in locals() and initial_departure_secs is not None else 0,
        },
        "routeGeometries": geometries,
    }

    # Defensive: some legacy callers expect a `journeys` array, but older
    # logic in this function only produced top-level `legs`. Ensure both are
    # always consistent.
    try:
        if not resp.get("journeys"):
            resp["journeys"] = [{"legs": legs}]
    except Exception:
        resp["journeys"] = [{"legs": legs}]

    # Note: routers do not need to surface a logged_journey_id; clients should
    # use the returned dense `route_int` meta for in-memory track lookup.
    # Persisting logged journeys in the DB is intentionally disabled to keep
    # request-time flows side-effect free and DB-independent.

    return resp


@app.post("/journey/plan")
async def journey_plan(request: JourneyPlanRequest):
    """Plan a journey between two locations.

    Accepts fromStop/toStop as {lat, lon} objects, runs the
    RAPTOR router, and returns structured legs plus
    routeGeometries for map polyline rendering.
    """
    try:
        def _route_has_mode(route_result, mode_name: str) -> bool:
            if not isinstance(route_result, dict):
                return False
            for key, info in route_result.items():
                if key == "_meta" or not isinstance(info, dict):
                    continue
                if str(info.get("mode") or info.get("type") or "").lower() == mode_name:
                    return True
            return False

        def _is_near_train_stop(point, merged_obj, atco_coords, max_meters: float = 500.0) -> bool:
            if not point or len(point) < 2:
                return False
            lat, lon = float(point[0]), float(point[1])
            lat_tol = max_meters / 111000.0
            lon_scale = max(0.1, math.cos(math.radians(lat)))
            lon_tol = max_meters / (111000.0 * lon_scale)

            for s_idx in range(len(getattr(merged_obj, "stop_to_routes", []))):
                try:
                    if merged_obj.stop_type(s_idx) != TRAIN:
                        continue
                    atco = merged_obj.get_atco_code(s_idx)
                    if not atco:
                        continue
                    coord = atco_coords.get(atco)
                    if not coord:
                        continue
                    s_lat, s_lon = float(coord[0]), float(coord[1])
                    if abs(s_lat - lat) <= lat_tol and abs(s_lon - lon) <= lon_tol:
                        return True
                except Exception:
                    continue
            return False

        start_point = (request.fromStop.lat, request.fromStop.lon)
        destination = (request.toStop.lat, request.toStop.lon)
        date_str = request.date
        time_str = request.departureTime
        max_transfers = request.maxTransfers
        mode = request.mode
        allowed_modes = ({mode} if mode in ("bus", "train")
                         else {"bus", "train"})
        start_seconds = seconds_since_midnight(time_str)

        # Run potentially-heavy router construction and routing in a thread.
        # While we route, signal background services to pause CPU-heavy work.
        with routing_activity():
            merged, router, walking = await asyncio.to_thread(
                get_router_for_date, date_str, start_time=start_seconds
            )
            result = await asyncio.to_thread(
                router.route,
                # pass the same keyword args through to the threaded call
                n_transfer_limit=max_transfers,
                walking=walking,
                start_time=start_seconds,
                start_point=start_point,
                destination=destination,
                allowed_modes=allowed_modes,
            )
            
            # If the request is station-to-station and mixed-mode routing found no
            # train leg, try train-only routing so obvious rail options are not
            # hidden behind frequent local bus alternatives.
            if mode == "both" and not _route_has_mode(result, "train"):
                atco_coords = {}
                try:
                    if globals().get('_base_cache') and _base_cache.get("atco_loader"):
                        atco_coords = _base_cache["atco_loader"].get_all_stop_coords() or {}
                except Exception:
                    atco_coords = {}

                if atco_coords and _is_near_train_stop(start_point, merged, atco_coords) and _is_near_train_stop(destination, merged, atco_coords):
                    train_result = await asyncio.to_thread(
                        router.route,
                        n_transfer_limit=max_transfers,
                        walking=walking,
                        start_time=start_seconds,
                        start_point=start_point,
                        destination=destination,
                        allowed_modes={"train"},
                    )
                    if train_result and set(train_result.keys()) != {"_meta"}:
                        result = train_result

        stop_coords = getattr(walking, "_coords", {})
        return build_journey_plan_response(
            result, merged, stop_coords, request_start_seconds=start_seconds,
            include_geometry=bool(getattr(request, 'includeGeometry', False)))
    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        logger.exception('Error in /journey/plan: %s', exc)
        # Return traceback in error during dev to help debugging (non-prod)
        return {"success": False, "error": str(exc), "trace": tb,
                "legs": None, "meta": None, "routeGeometries": None}


@app.post("/journey/compare")
async def compare_routers(request: JourneyPlanRequest):
    """Run the main RAPTOR router and the ECO router concurrently and
    return both results for comparison.

    Accepts the same payload as `/journey/plan` and returns a JSON
    object containing `main` and `eco` keys with sanitized routes,
    human-readable summaries and timings in seconds.
    """
    try:
        start_point = (request.fromStop.lat, request.fromStop.lon)
        destination = (request.toStop.lat, request.toStop.lon)
        date_str = request.date
        time_str = request.departureTime
        max_transfers = request.maxTransfers
        mode = request.mode
        allowed_modes = ({mode} if mode in ("bus", "train")
                         else {"bus", "train"})
        start_seconds = seconds_since_midnight(time_str)

        # While we run multiple routers, signal background services to pause.
        with routing_activity():
            # Build / fetch merged data + main router + walking helper in a thread
            merged, main_router, walking = await asyncio.to_thread(
                get_router_for_date, date_str, start_time=start_seconds
            )

            # Lazily construct optional routers (eco, lazy, greedy).
            # optional routers: eco, lazy, greedy
            eco_router = None
            computed = None
            lazy_router = None
            greedy_router = None
            try:
                from eco_router import RaptorRouter as EcoRaptor
                eco_router = EcoRaptor(merged)
            except Exception:
                eco_router = None

            try:
                from lazy_router import RaptorRouter as LazyRaptor
                lazy_router = LazyRaptor(merged)
            except Exception:
                lazy_router = None
            try:
                from greedy_router import RaptorRouter as GreedyRaptor
                greedy_router = GreedyRaptor(merged)
            except Exception:
                greedy_router = None

            # Debug: record which optional routers were successfully constructed
            available_routers = [name for name, obj in (
                ("eco", eco_router),
                ("lazy", lazy_router), ("greedy", greedy_router)
            ) if obj is not None]
            logger.debug("compare_routers: available optional routers: %s", available_routers)

            # Helper wrapper to call router.route with timing
            def _run_router(rtr):
                import time as _t
                t0 = _t.time()
                res = rtr.route(
                    n_transfer_limit=max_transfers,
                    walking=walking,
                    start_time=start_seconds,
                    start_point=start_point,
                    destination=destination,
                    allowed_modes=allowed_modes,
                )
                t1 = _t.time()
                return res, (t1 - t0)

            # Run routers in parallel threads. Build a list of (name, router)
            routers = [("main", main_router)]
            if eco_router is not None:
                routers.append(("eco", eco_router))
            if lazy_router is not None:
                routers.append(("lazy", lazy_router))
            if greedy_router is not None:
                routers.append(("greedy", greedy_router))

            tasks = [asyncio.to_thread(_run_router, rtr) for (_name, rtr) in routers]
            results = await asyncio.gather(*tasks)

            # Map names to (result, time)
            router_results = {}
            for (name, _), (res, timing) in zip(routers, results):
                router_results[name] = (res, timing)

            main_res, main_time = router_results.get("main", (None, None))
            eco_res, eco_time = router_results.get("eco", (None, None))
            lazy_res, lazy_time = router_results.get("lazy", (None, None))
            greedy_res, greedy_time = router_results.get("greedy", (None, None))

            stop_coords = getattr(walking, "_coords", {})

            # Build full journey-plan responses (same shape as /journey/plan)
            include_geom = bool(getattr(request, 'includeGeometry', False))
            main_plan = build_journey_plan_response(main_res, merged, stop_coords, request_start_seconds=start_seconds, include_geometry=include_geom) if main_res is not None else None
            eco_plan = build_journey_plan_response(eco_res, merged, stop_coords, request_start_seconds=start_seconds, include_geometry=include_geom) if eco_res is not None else None
            lazy_plan = build_journey_plan_response(lazy_res, merged, stop_coords, request_start_seconds=start_seconds, include_geometry=include_geom) if lazy_res is not None else None
            greedy_plan = build_journey_plan_response(greedy_res, merged, stop_coords, request_start_seconds=start_seconds, include_geometry=include_geom) if greedy_res is not None else None

            return {
                "success": True,
                "available_routers": available_routers,
                "main": {
                    "route": main_plan,
                    "route_text": format_route_text(main_res, merged) if main_res is not None else None,
                    "time_seconds": main_time,
                },
                "eco": {
                    "route": eco_plan,
                    "route_text": format_route_text(eco_res, merged) if eco_res is not None else None,
                    "time_seconds": eco_time,
                },
                "lazy": {
                    "route": lazy_plan,
                    "route_text": format_route_text(lazy_res, merged) if lazy_res is not None else None,
                    "time_seconds": lazy_time,
                },
                "greedy": {
                    "route": greedy_plan,
                    "route_text": format_route_text(greedy_res, merged) if greedy_res is not None else None,
                    "time_seconds": greedy_time,
                },
            }
    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        logger.exception('Error in /journey/compare: %s', exc)
        return {"success": False, "error": str(exc), "trace": tb, "main": None, "eco": None}

@app.post("/api/route")
async def get_route(request: RouteRequest):
    """Legacy journey-planning endpoint — retained for backwards compatibility.

    Prefer ``POST /journey/plan`` for new callers; it returns a richer,
    stable response shape.  This endpoint now delegates to
    ``build_journey_plan_response`` so it no longer leaks raw internal
    RAPTOR data structures (fixes P7).
    """
    try:
        date_str = request.date
        time_str = request.time
        start_point = (request.start_lat, request.start_lon)
        destination = (request.end_lat, request.end_lon)
        max_transfers = request.max_transfers
        allowed_modes = {request.mode} if request.mode in ("bus", "train") else {"bus", "train"}
        start_seconds = seconds_since_midnight(time_str)
        # Run router construction and routing in a thread to avoid blocking
        merged, router, walking = await asyncio.to_thread(
            get_router_for_date, date_str, start_time=start_seconds
        )
        result = await asyncio.to_thread(
            router.route,
            n_transfer_limit=max_transfers,
            walking=walking,
            start_time=start_seconds,
            start_point=start_point,
            destination=destination,
            allowed_modes=allowed_modes,
        )
        stop_coords = getattr(walking, "_coords", {})
        return build_journey_plan_response(result, merged, stop_coords, request_start_seconds=start_seconds)
    except Exception as e:
        return {"success": False, "error": str(e),
                "legs": None, "meta": None, "routeGeometries": None}


@app.post("/api/route_by_address")
async def route_by_address(request: AddressRouteRequest):
    """Resolve textual start/end locations using Nominatim then run the normal router.

    Returns the same shape as `/api/route` (success + route + route_text) on success.
    """
    try:
        # Geocode start
        start_q = (request.start or "").strip()
        end_q = (request.end or "").strip()
        if not start_q or not end_q:
            return {"success": False, "error": "start and end must be non-empty strings"}

        start_hits = geocode_locations(start_q, limit=1)
        if not start_hits:
            return {"success": False, "error": f"Could not geocode start: {start_q}"}
        end_hits = geocode_locations(end_q, limit=1)
        if not end_hits:
            return {"success": False, "error": f"Could not geocode end: {end_q}"}

        start_point = (float(start_hits[0]["lat"]), float(start_hits[0]["lon"]))
        destination = (float(end_hits[0]["lat"]), float(end_hits[0]["lon"]))

        date_str = request.date
        time_str = request.time
        max_transfers = request.max_transfers
        allowed_modes = {request.mode} if request.mode in ("bus", "train") else {"bus", "train"}
        start_seconds = seconds_since_midnight(time_str)

        # Build a JourneyPlanRequest and delegate to the compare endpoint
        # so address-based requests return the same multi-router response
        # (main, eco, lazy, greedy) as `/journey/compare`.
        jreq = JourneyPlanRequest(
            fromStop=StopLocation(lat=start_point[0], lon=start_point[1]),
            toStop=StopLocation(lat=destination[0], lon=destination[1]),
            departureTime=time_str,
            date=date_str,
            maxTransfers=max_transfers,
            mode=request.mode,
        )
        # Reuse the compare_routers handler to perform multi-router comparison.
        resp = await compare_routers(jreq)
        return resp
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@app.get("/api/geocode")
async def api_geocode(q: str = "", limit: int = 5, county: Optional[str] = None):
    """Return Nominatim candidates for a textual query.

    Example: /api/geocode?q=Lancaster&limit=5
    """
    try:
        if not q:
            return JSONResponse(status_code=400, content={"error": "q query param required"})
        results = geocode_locations(q, limit=limit, county=county)
        return {"success": True, "candidates": results}
    except Exception as exc:
        return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})


@app.get("/weather")
async def route_weather(lat: float | None = None, lon: float | None = None):
    if lat is None or lon is None:
        return JSONResponse(
            status_code=400,
            content={"error": "lat and lon are required"},
        )
    
    fetch_url = f"https://transport.scc.lancs.ac.uk/weather?lat={lat}&lon={lon}"

    # Use requests (which bundles a CA cert store via certifi) so TLS
    # verification works inside virtualenvs and containers.
    import requests

    try:
        resp = requests.get(fetch_url, headers={"User-Agent": "transport-backend/1.0"}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        return JSONResponse(
            status_code=502,
            content={"error": str(exc)},
        )
    
    weather_obj = data.get("weather", {})

    weather_arr = weather_obj.get("weather", [])
    wind = weather_obj.get("wind", {})
    main = weather_obj.get("main", {})

    return {
        "weather": weather_arr,
        "wind": wind,
        "main": main,
    }

# Parse the lt8:trainServices element into its services
def parse_train_services(root: ElementTree):
    services = []
    for service in root:
        # lt8:service
        service_data = {}
        for child in service:
            _, _, tag = child.tag.rpartition("}")
            match tag:
                case "std": service_data["scheduledTime"] = seconds_since_midnight(child.text + ":00")
                case "etd":
                    # Return the correct status for this service
                    if child.text == "On time":
                        service_data["status"] = "On time"
                        service_data["departureTime"] = seconds_to_time(service_data["scheduledTime"])
                    elif child.text == "Delayed":
                        service_data["status"] = f"Delayed"
                        service_data["departureTime"] = "Unknown Delay"
                    elif child.text == "Cancelled":
                        service_data["status"] = f"Cancelled"
                        service_data["departureTime"] = "No Departure"
                    else:
                        etd = seconds_since_midnight(child.text + ":00")
                        delay_min = (etd - service_data["scheduledTime"]) // 60
                        service_data["delayMins"] = delay_min
                        service_data["status"] = f"Delayed {delay_min} mins"
                        service_data["departureTime"] = seconds_to_time(etd)

                case "destination":
                    service_data["destination"] = child[0][0].text # lt4:location>lt4:locationName
    
        services.append(service_data)

    return services

# Parse NRCC messages to display as alerts
def parse_nrcc_messages(root: ElementTree):
    # For now, every message is a warning
    return [{ "message": msg.text, "severity": "warning" } for msg in root]

@app.get("/rail/departures/{station_code}")
async def route_rail_departures(station_code):
    if _live_endpoints_disabled():
        return JSONResponse(status_code=200, content={"locationName": station_code, "services": [], "messages": []})
    if station_code is None:
        return JSONResponse(
            status_code=400,
            content={"error": "station_code is required"},
        )
    
    fetch_url = f"https://transport.scc.lancs.ac.uk/rail/departures/{station_code}"

    import requests
    try:
        resp = requests.get(fetch_url, headers={"User-Agent": "transport-backend/1.0"}, timeout=10)
        resp.raise_for_status()
        tree = ElementTree.fromstring(resp.content)
    except Exception as exc:
        return JSONResponse(
            status_code=502,
            content={"error": str(exc)},
        )

    facilities_fetch_url = f"https://transport.scc.lancs.ac.uk/rail/facilities/{station_code}"

    try:
        resp = requests.get(facilities_fetch_url, headers={"User-Agent": "transport-backend/1.0"}, timeout=10)
        resp.raise_for_status()
        facilities_data = resp.json()
    except Exception as exc:
        return JSONResponse(
            status_code=502,
            content={"error": str(exc)},
        )

    location_name = None
    services = []
    messages = []

    for child in tree:
        _, _, tag = child.tag.rpartition("}")
        match tag:
            case "locationName":
                location_name = child.text
            
            case "trainServices":
                services = parse_train_services(child)

            case "nrccMessages":
                messages = parse_nrcc_messages(child)

    # Update rail messages (used by /alerts)
    if messages:
        rail_alerts_cache[station_code] = messages

    # Add station information to services
    def add_service_data(s):
        s["stationName"] = location_name + " Station"
        
        s["lat"] = facilities_data["location"]["latitude"]
        s["lon"] = facilities_data["location"]["longitude"]
        return s

    return [add_service_data(s) for s in services]

if __name__ == "__main__":
    import uvicorn
    # Try to detect and terminate any existing process listening on the
    # intended port so a stale server doesn't prevent starting the app.
    # This is intended as a developer convenience; it uses `lsof` which
    # is commonly available on macOS and Linux. If `lsof` is missing the
    # attempt is skipped and startup proceeds normally.
    import subprocess
    import shutil
    import time
    import signal
    import os
    import logging

    def _kill_process_on_port(port: int = 5050, timeout: float = 2.0) -> None:
        logger = logging.getLogger(__name__)
        lsof = shutil.which("lsof")
        if not lsof:
            logger.debug("lsof not found; skipping auto-kill on port %s", port)
            return
        try:
            # lsof -ti tcp:<port> outputs PIDs (one per line) or exits non-zero
            out = subprocess.check_output([lsof, "-ti", f"tcp:{port}"], stderr=subprocess.DEVNULL)
            pids = [int(p) for p in out.decode().strip().split() if p.strip()]
        except subprocess.CalledProcessError:
            # No process found listening on the port
            return
        except Exception as exc:  # pragma: no cover - environment-specific
            logger.warning("Error checking port %s occupancy: %s", port, exc)
            return

        for pid in pids:
            try:
                if pid == os.getpid():
                    # Don't kill ourselves
                    continue
                logger.info("Terminating process %s occupying port %s", pid, port)
                os.kill(pid, signal.SIGTERM)
            except Exception:
                # Ignore individual kill failures and continue
                pass

        # Wait briefly for processes to exit
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                out = subprocess.check_output([lsof, "-ti", f"tcp:{port}"], stderr=subprocess.DEVNULL)
                if not out.strip():
                    break
            except subprocess.CalledProcessError:
                break
            except Exception:
                break
            time.sleep(0.05)

    _kill_process_on_port(5050)

    uvicorn.run(app, host="localhost", port=5050)



