"""Transport Backend — FastAPI server.

Exposes transport functionality (health check, live buses, journey
planning, stop search, station classification) as a JSON API consumed
by the frontend.
"""

from contextlib import asynccontextmanager
import json
import logging
import os
import sys
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode
from urllib.request import Request as UrllibRequest, urlopen
from xml.etree import ElementTree

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from bus_live import BusLive, get_bus_live
from main import build_for_date
import asyncio
from time_utils import seconds_since_midnight, seconds_to_time
from ws_server import broker as ws_broker, websocket_endpoint as ws_live_endpoint
from station_classifier import classify_all, classify_to_lookup
from modes import name_to_int, all_transit_modes
import copy
import threading
import time
import psycopg
from urllib.error import URLError
import atexit

logger = logging.getLogger(__name__)

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
        # Poll for live updates every 20s and use a 20s HTTP timeout for feed fetches
        ws_broker.configure(bus_live_factory=lambda: BusLive(timeout=20), poll_interval=20.0)
        await ws_broker.start_polling()
    except Exception as exc:  # pragma: no cover
        logger.warning("WebSocket broker startup failed: %s", exc)
    # Start background delay updater thread (today-only updates)
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
    """Debug helper: inspect whether in-memory route_tracks exist for route_id.

    Response:
      {
        "route_id": str,
        "found": bool,
        "coords_len": int,
        "coords_sample": [[lat,lon],...],
        "suggestions": [route_id,...]
      }

    This endpoint is for local debugging only.
    """
    if not route_id:
        raise HTTPException(status_code=400, detail='route_id is required')

    tracks = []
    try:
        tracks = _fetch_route_tracks(route_id) or []
    except Exception:
        tracks = []

    coords_len = len(tracks) if isinstance(tracks, list) else 0
    coords_sample = []
    try:
        n = int(sample)
        if n < 0:
            n = 0
        coords_sample = (tracks[:n] if isinstance(tracks, list) else [])
    except Exception:
        coords_sample = []

    # Optional: when a VJ-style route_id is provided, show candidate family keys
    # (RS*/JPS*) that share the same base and whether they actually have tracks.
    candidates = []
    try:
        if isinstance(route_id, str) and ':VJ' in route_id:
            vj_base = route_id.split(':VJ', 1)[0]
            # 1) Candidates from in-memory metadata (best-effort)
            from_meta = []
            try:
                cand_ids = _suggest_similar_route_ids(route_id, limit=80)
                for rid2 in cand_ids:
                    if isinstance(rid2, str) and (
                        rid2.startswith(vj_base + ':RS') or rid2.startswith(vj_base + ':JPS')
                    ):
                        from_meta.append(rid2)
            except Exception:
                from_meta = []

            # 2) Candidates from DB (authoritative for what keys exist)
            from_db = []
            try:
                like_rs = vj_base + ':RS%'
                like_jps = vj_base + ':JPS%'
                conn = _get_db_connection()
                cur = conn.cursor()
                # Prefer section tracks for RS discovery.
                try:
                    cur.execute(
                        "SELECT DISTINCT route_id FROM bus_route_section_tracks WHERE route_id LIKE %s LIMIT 60",
                        (like_rs,),
                    )
                    from_db.extend([r[0] for r in (cur.fetchall() or []) if r and r[0]])
                except Exception:
                    pass
                try:
                    cur.execute(
                        "SELECT DISTINCT route_id FROM bus_route_tracks WHERE route_id LIKE %s LIMIT 60",
                        (like_rs,),
                    )
                    from_db.extend([r[0] for r in (cur.fetchall() or []) if r and r[0]])
                except Exception:
                    pass
                try:
                    cur.execute(
                        "SELECT DISTINCT route_id FROM bus_route_tracks WHERE route_id LIKE %s LIMIT 60",
                        (like_jps,),
                    )
                    from_db.extend([r[0] for r in (cur.fetchall() or []) if r and r[0]])
                except Exception:
                    pass
                try:
                    conn.close()
                except Exception:
                    pass
            except Exception:
                from_db = []

            # De-dup (DB first because it's the source of truth)
            family = []
            seen = set()
            for rid2 in (from_db + from_meta):
                if not isinstance(rid2, str) or rid2 in seen:
                    continue
                if not (rid2.startswith(vj_base + ':RS') or rid2.startswith(vj_base + ':JPS')):
                    continue
                seen.add(rid2)
                family.append(rid2)
                if len(family) >= 20:
                    break

            for rid2 in family:
                t2 = []
                try:
                    t2 = _fetch_route_tracks(rid2) or []
                except Exception:
                    t2 = []
                candidates.append({
                    'route_id': rid2,
                    'found': bool(isinstance(t2, list) and len(t2) >= 2),
                    'coords_len': (len(t2) if isinstance(t2, list) else 0),
                })
    except Exception:
        candidates = []

    resp = {
        'route_id': route_id,
        'found': bool(isinstance(tracks, list) and len(tracks) >= 2),
        'coords_len': coords_len,
        'coords_sample': coords_sample,
        'suggestions': [],
        'vj_candidates': candidates,
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
    "http://localhost:3000,http://localhost:5075,http://localhost:5076,"
    "http://127.0.0.1:3000,http://127.0.0.1:5075,http://127.0.0.1:5076"
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
    """Return route geometry for an in-memory route_id.

    Query params:
      - route_id: canonical route id (file-prefixed where applicable)

    Response:
      {"coords": [[lat, lon], ...], "source": "track"} or {"error": ...}
    """
    if not route_id:
        return {"error": "missing_route_id"}

    # Prefer a route_id that includes the file-prefix tag when present in
    # the in-memory datasets. _fetch_route_tracks only consults in-memory
    # route_tracks (MergedData.route_tracks) and will return [] if not
    # found.
    tracks = _fetch_route_tracks(route_id)
    if not tracks:
        return {"error": "route_tracks_not_found"}
    return {"coords": tracks, "source": "track"}


# --- Geometry assembly endpoint helpers -------------------------------
def _get_db_connection():
    from main import BUS_DB_PATH
    return psycopg.connect(BUS_DB_PATH)


def _fetch_logged_journey_from_db(ljid: str):
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


def _fetch_journey_times_external(journey_id: str):
    """Return ordered list of (atco_code, arrival_time) for an external journey_id from bus_journey_times."""
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT atco_code, arrival_time FROM bus_journey_times WHERE journey_id = %s ORDER BY arrival_time", (journey_id,))
        rows = cur.fetchall()
        conn.close()
        return [(r[0], r[1]) for r in rows]
    except Exception:
        return []


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
    def _journey_origin_atcos(jt):
        out = []
        try:
            for i in range(min(3, len(jt))):
                atco = merged.get_atco_code(jt[i][0])
                if atco:
                    out.append(str(atco).strip())
        except Exception:
            return []
        return out

    def _journey_dest_atcos(jt):
        out = []
        try:
            for i in range(1, min(4, len(jt) + 1)):
                atco = merged.get_atco_code(jt[-i][0])
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


@app.get("/route/leg-geometry")
def route_leg_geometry(from_lat: float, from_lon: float,
                       to_lat: float, to_lon: float,
                       mode: str = "driving",
                       route_id: str | None = None,
                       from_stop_id: str | None = None,
                       to_stop_id: str | None = None):
    """Return geometry for a single journey leg.

    Query params:
      from_lat, from_lon – start point
      to_lat, to_lon     – end point
      mode               – 'walking' | 'bus' | 'train' | 'driving' (default)
            route_id           – optional canonical timetable route_id
            from_stop_id       – optional ATCO code for the leg start stop
            to_stop_id         – optional ATCO code for the leg end stop

    If route_id is provided and this is a bus leg, we will *prefer* the
    timetable's stored route tracks when available.

    Walking legs use the OSRM *foot* profile; all others use *driving*.
    Response: {"coords": [[lat, lon], ...], "source": "route_tracks"|"osrm"|"linear"} or {"error": "..."}
    """
    # Prefer stored timetable tracks when we have enough context.
    #
    # IMPORTANT: The frontend often calls this endpoint with mode=driving
    # even for bus legs (it uses the driving OSRM profile for road snapping).
    # When a canonical route_id is provided, we still want to prefer the
    # timetable's own stored route_tracks.
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

        if route_id and mode != 'walking':
            tracks = _fetch_route_tracks(route_id)
            if trace:
                try:
                    tlen = len(tracks) if isinstance(tracks, list) else None
                except Exception:
                    tlen = None
                print('[route_leg_geometry] fetched tracks', {
                    'route_id': route_id,
                    'tracks_type': type(tracks).__name__,
                    'tracks_len': tlen,
                })

            # Treat tracks as usable only if it has at least 2 points.
            if isinstance(tracks, list) and len(tracks) >= 2:
                # When stop ids are available, return a subsegment between the
                # two stops so the frontend doesn't render the *entire* route.
                if from_stop_id and to_stop_id:
                    # Build a minimal coord map for the requested stops.
                    # - Prefer in-memory walking coords when available.
                    # - Fall back to the provided query endpoints.
                    stop_coords = {}
                    try:
                        if globals().get('_base_cache'):
                            wc = _base_cache.get('walking_raw', {}).get('coords')
                            if wc:
                                a = wc.get(from_stop_id)
                                b = wc.get(to_stop_id)
                                if a:
                                    stop_coords[from_stop_id] = a
                                if b:
                                    stop_coords[to_stop_id] = b
                    except Exception:
                        pass
                    if from_stop_id not in stop_coords:
                        stop_coords[from_stop_id] = (from_lat, from_lon)
                    if to_stop_id not in stop_coords:
                        stop_coords[to_stop_id] = (to_lat, to_lon)

                    seg = _subsegment_from_tracks(route_id, [from_stop_id, to_stop_id], stop_coords)
                    if seg and len(seg) >= 2:
                        if trace:
                            print('[route_leg_geometry] returning route_tracks subsegment', {'len': len(seg)})
                        return {"coords": seg, "source": "route_tracks"}
                # Fallback: return full track.
                if trace:
                    print('[route_leg_geometry] returning full route_tracks', {'len': len(tracks)})
                return {"coords": tracks, "source": "route_tracks"}
    except Exception:
        # Best-effort only; fall through to OSRM/linear.
        if trace:
            import traceback
            print('[route_leg_geometry] exception in route_tracks path')
            traceback.print_exc()
        pass

    osrm_base = os.environ.get('OSRM_URL', 'http://localhost:5012')
    profile = 'foot' if mode == 'walking' else 'driving'
    try:
        coords_lonlat = [f"{from_lon},{from_lat}", f"{to_lon},{to_lat}"]
        coords = _query_osrm_for_coords_profile(osrm_base, coords_lonlat,
                                                 profile=profile)
        if coords and len(coords) >= 2:
            return {"coords": coords, "source": "osrm"}
        # Fall back to a straight line between the two points
        return {"coords": [[from_lat, from_lon], [to_lat, to_lon]],
                "source": "linear"}
    except Exception:
        return {"coords": [[from_lat, from_lon], [to_lat, to_lon]],
                "source": "linear"}


def _fetch_route_tracks(route_id: str):
    """Return the route track as list of [lat, lon] or [] on failure.

    Prefer in-memory route_tracks from any loaded MergedData (prebuilt cache
    and router cache) so consumers use the timetable's in-memory copy that
    the loader populated. Fall back to DB lookups only if no in-memory
    candidate is found.
    """
    # Some live/matching flows use a VJ-specific identifier (e.g. ...:VJ1234:45600)
    # while timetable `route_metadata[*].route_id` keys route_tracks by RS variant
    # (e.g. ...:RS5). When given a VJ id, we should fall back to the RS id
    # sharing the same "base" prefix so we can still return the authoritative
    # timetable track.
    vj_base = None
    try:
        if isinstance(route_id, str) and ':VJ' in route_id:
            vj_base = route_id.split(':VJ', 1)[0]
    except Exception:
        vj_base = None

    def _db_candidate_route_ids_for_vj_base(vj_base: str, limit: int = 200):
        """Return candidate route_ids (RS/JPS families) from DB for a VJ base prefix.

        This is needed because many datasets store route_tracks keyed by RS ids
        only (no VJ/JPS keys), so a pure in-memory scan that only considers
        JPS keys can miss the actual RS key with geometry.
        """
        if not vj_base:
            return []
        # Limit for safety; we only need a handful to find a non-empty track.
        lim = max(1, min(int(limit or 200), 1000))
        rs_like = vj_base + ':RS%'
        jps_like = vj_base + ':JPS%'
        out = []
        try:
            conn = _get_db_connection()
            cur = conn.cursor()
            # Prefer section tracks (more specific), but either table is fine.
            try:
                cur.execute(
                    "SELECT DISTINCT route_id FROM bus_route_section_tracks WHERE route_id LIKE %s LIMIT %s",
                    (rs_like, lim),
                )
                out.extend([r[0] for r in (cur.fetchall() or []) if r and r[0]])
            except Exception:
                pass
            try:
                cur.execute(
                    "SELECT DISTINCT route_id FROM bus_route_tracks WHERE route_id LIKE %s LIMIT %s",
                    (rs_like, lim),
                )
                out.extend([r[0] for r in (cur.fetchall() or []) if r and r[0]])
            except Exception:
                pass
            # Also include JPS family if present in DB.
            try:
                cur.execute(
                    "SELECT DISTINCT route_id FROM bus_route_tracks WHERE route_id LIKE %s LIMIT %s",
                    (jps_like, lim),
                )
                out.extend([r[0] for r in (cur.fetchall() or []) if r and r[0]])
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass
        except Exception:
            return []

        # De-dup while preserving order.
        seen = set()
        uniq = []
        for rid in out:
            if rid in seen:
                continue
            seen.add(rid)
            uniq.append(rid)
        return uniq

    def _matches_vj_family(rid2: str) -> bool:
        if not vj_base or not isinstance(rid2, str):
            return False
        # Known timetable families for route_tracks keys.
        return (
            rid2.startswith(vj_base + ':RS')
            or rid2.startswith(vj_base + ':JPS')
        )

    db_vj_candidates = []
    # Precompute DB-backed RS/JPS candidates for VJ ids. This helps when the
    # in-memory MergedData doesn't contain a route_metadata entry for the
    # route_id family we need (common with RS-only tracks).
    try:
        if vj_base:
            db_vj_candidates = _db_candidate_route_ids_for_vj_base(vj_base)
    except Exception:
        db_vj_candidates = []

    # 1) Try in-memory caches (prebuilt_cache in _base_cache, then _router_cache)
    try:
        # Check prebuilt merged timetables (fast path)
        if globals().get('_base_cache'):
            try:
                prebuilt = _base_cache.get('prebuilt_cache')
                if prebuilt:
                    for k, v in prebuilt.items():
                        try:
                            merged = v[0]
                        except Exception:
                            continue
                        if not merged:
                            continue
                        # MergedData does not expose DenseMapper instances
                        # (map_routes/map_journeys) like the raw BusData.
                        # Resolve an int route index by scanning metadata.
                        r_int = None
                        candidate_ints = []
                        try:
                            for i, meta in enumerate(getattr(merged, 'route_metadata', []) or []):
                                if meta and isinstance(meta, dict) and meta.get('route_id') == route_id:
                                    r_int = i
                                    break
                                if meta and isinstance(meta, dict) and vj_base:
                                    rid2 = meta.get('route_id')
                                    if _matches_vj_family(rid2):
                                        candidate_ints.append(i)
                        except Exception:
                            r_int = None

                        # If not an exact hit, choose the first candidate that has non-empty tracks.
                        if r_int is None and candidate_ints:
                            try:
                                for i in candidate_ints:
                                    if i < len(merged.route_tracks) and merged.route_tracks[i]:
                                        r_int = i
                                        break
                                if r_int is None:
                                    r_int = candidate_ints[0]
                            except Exception:
                                r_int = candidate_ints[0]
                        if r_int is not None and r_int < len(merged.route_tracks):
                            tracks = merged.route_tracks[r_int]
                            if tracks:
                                return [[t[0], t[1]] for t in tracks]

                        # If we still didn't find anything, try resolving via DB-backed
                        # candidate ids (usually RS keys) and look those up in this merged.
                        if r_int is None and db_vj_candidates:
                            try:
                                meta_list = getattr(merged, 'route_metadata', []) or []
                                for cand in db_vj_candidates:
                                    for i, meta in enumerate(meta_list):
                                        if meta and isinstance(meta, dict) and meta.get('route_id') == cand:
                                            if i < len(merged.route_tracks) and merged.route_tracks[i]:
                                                return [[t[0], t[1]] for t in merged.route_tracks[i]]
                                            break
                            except Exception:
                                pass
            except Exception:
                # best-effort only
                pass

        # Check any routers cached in _router_cache (may include other dates)
        try:
            # _router_cache and its lock may be defined later in the file;
            # reference globals to avoid NameError during module import time.
            rcache = globals().get('_router_cache')
            rlock = globals().get('_router_cache_lock')
            if rcache is not None:
                # Iterate safely under lock when available
                if rlock:
                    with rlock:
                        items = list(rcache.values())
                else:
                    items = list(rcache.values())
                for val in items:
                    try:
                        merged = val[0]
                    except Exception:
                        continue
                    if not merged:
                        continue
                    r_int = None
                    candidate_ints = []
                    try:
                        for i, meta in enumerate(getattr(merged, 'route_metadata', []) or []):
                            if meta and isinstance(meta, dict) and meta.get('route_id') == route_id:
                                r_int = i
                                break
                            if meta and isinstance(meta, dict) and vj_base:
                                rid2 = meta.get('route_id')
                                if _matches_vj_family(rid2):
                                    candidate_ints.append(i)
                    except Exception:
                        r_int = None

                    if r_int is None and candidate_ints:
                        try:
                            for i in candidate_ints:
                                if i < len(merged.route_tracks) and merged.route_tracks[i]:
                                    r_int = i
                                    break
                            if r_int is None:
                                r_int = candidate_ints[0]
                        except Exception:
                            r_int = candidate_ints[0]
                    if r_int is not None and r_int < len(merged.route_tracks):
                        tracks = merged.route_tracks[r_int]
                        if tracks:
                            return [[t[0], t[1]] for t in tracks]

                    if r_int is None and db_vj_candidates:
                        try:
                            meta_list = getattr(merged, 'route_metadata', []) or []
                            for cand in db_vj_candidates:
                                for i, meta in enumerate(meta_list):
                                    if meta and isinstance(meta, dict) and meta.get('route_id') == cand:
                                        if i < len(merged.route_tracks) and merged.route_tracks[i]:
                                            return [[t[0], t[1]] for t in merged.route_tracks[i]]
                                        break
                        except Exception:
                            pass
        except Exception:
            pass
    except Exception:
        # In-memory lookup failed — fall back to DB below
        pass

    # 1b) Best-effort: lazily build today's router once so route_tracks become
    # available in memory even when this helper is called early in a process.
    # This is slower than the cache paths above, so we only do it once.
    try:
        if not globals().get('_route_tracks_bootstrapped'):
            globals()['_route_tracks_bootstrapped'] = True
            try:
                from datetime import date as _date
                # Build a router for today; this populates _router_cache.
                get_router_for_date(_date.today().isoformat())
            except Exception:
                pass

            # Retry router_cache scan after bootstrap
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
                        continue
                    if not merged:
                        continue
                    r_int = None
                    candidate_ints = []
                    try:
                        for i, meta in enumerate(getattr(merged, 'route_metadata', []) or []):
                            if meta and isinstance(meta, dict) and meta.get('route_id') == route_id:
                                r_int = i
                                break
                            if meta and isinstance(meta, dict) and vj_base:
                                rid2 = meta.get('route_id')
                                if _matches_vj_family(rid2):
                                    candidate_ints.append(i)
                    except Exception:
                        r_int = None

                    if r_int is None and candidate_ints:
                        try:
                            for i in candidate_ints:
                                if i < len(merged.route_tracks) and merged.route_tracks[i]:
                                    r_int = i
                                    break
                            if r_int is None:
                                r_int = candidate_ints[0]
                        except Exception:
                            r_int = candidate_ints[0]
                    if r_int is not None and r_int < len(merged.route_tracks):
                        tracks = merged.route_tracks[r_int]
                        if tracks:
                            return [[t[0], t[1]] for t in tracks]
    except Exception:
        pass

    # 2) In this deployment we only use in-memory route_tracks.
    # If no in-memory candidate found above, fall back to DB so we can
    # still serve geometry for historical dataset prefixes (common when
    # load_busdata_for_date filters away those route_ids).
    try:
        # Helper: fetch polyline points for an exact route_id from DB.
        def _db_fetch_tracks_exact(rid: str):
            if not rid:
                return []
            conn = _get_db_connection()
            cur = conn.cursor()
            cur.execute(
                "SELECT lat, lon FROM bus_route_tracks WHERE route_id = %s ORDER BY seq",
                (rid,),
            )
            rows = cur.fetchall() or []
            try:
                conn.close()
            except Exception:
                pass
            if not rows:
                return []
            return [[float(a), float(b)] for (a, b) in rows if a is not None and b is not None]

        # 2a) Try exact route_id
        direct = _db_fetch_tracks_exact(route_id)
        if direct and len(direct) >= 2:
            return direct

        # 2b) If VJ id, try DB-discovered RS/JPS candidates first.
        if db_vj_candidates:
            for cand in db_vj_candidates:
                t = _db_fetch_tracks_exact(cand)
                if t and len(t) >= 2:
                    return t

        # 2c) As a final best-effort, try to resolve a prefixed candidate
        # (handles callers that pass an un-prefixed route_id).
        try:
            pref = _find_prefixed_route_candidate(route_id)
            if pref and pref != route_id:
                t = _db_fetch_tracks_exact(pref)
                if t and len(t) >= 2:
                    return t
        except Exception:
            pass
    except Exception:
        # DB fallback is best-effort only.
        pass

    return []


def _find_prefixed_route_candidate(route_id: str):
    """Return a candidate route_id from the DB that includes a file-prefix
    (i.e. matches '%::<route_id') or None if not found. This helps ensure
    lookups include the originating file tag when present in the tables.
    """
    if not route_id:
        return None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        # Prefer a route_id present in section_tracks (more specific)
        try:
            pattern = f"%::{route_id}"
            logger.debug("_find_prefixed_route_candidate: checking bus_route_section_tracks LIKE %s", pattern)
            cur.execute("SELECT route_id FROM bus_route_section_tracks WHERE route_id LIKE %s LIMIT 1", (pattern,))
            r = cur.fetchone()
            if r and r[0]:
                logger.debug("_find_prefixed_route_candidate: found section_tracks candidate %s for %s", r[0], route_id)
                conn.close()
                return r[0]
        except Exception:
            logger.exception("_find_prefixed_route_candidate: error querying bus_route_section_tracks LIKE %s", f"%::{route_id}")
        try:
            pattern2 = f"%::{route_id}"
            logger.debug("_find_prefixed_route_candidate: checking bus_route_tracks LIKE %s", pattern2)
            cur.execute("SELECT route_id FROM bus_route_tracks WHERE route_id LIKE %s LIMIT 1", (pattern2,))
            r = cur.fetchone()
            if r and r[0]:
                logger.debug("_find_prefixed_route_candidate: found bus_route_tracks candidate %s for %s", r[0], route_id)
                conn.close()
                return r[0]
        except Exception:
            logger.exception("_find_prefixed_route_candidate: error querying bus_route_tracks LIKE %s", f"%::{route_id}")
        conn.close()
    except Exception:
        logger.exception("_find_prefixed_route_candidate: error opening DB connection to resolve %s", route_id)
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


def _subsegment_from_tracks(route_id: str, stop_atcos: list, walking_coords: dict):
    """Return a subsegment of route_tracks for route_id that spans the stops in stop_atcos.

    Strategy:
      - Load full route track points (lat, lon).
      - For each stop ATCO, find nearest track index using walking_coords mapping.
      - Take min..max index range (inclusive) and return that slice.
    Returns list of [lat, lon] or [] if not possible.
    """
    if not stop_atcos:
        return []
    tracks = _fetch_route_tracks(route_id)
    if not tracks:
        return []
    # Build list of indices for each stop
    indices = []
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
        # find nearest track point
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
        return tracks[start:end+1]
    else:
        return list(reversed(tracks[end:start+1]))


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
    """Query Nominatim and return candidates filtered to Lancashire.

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

    results = []
    for idx, item in enumerate(payload):
        try:
            lat = float(item.get("lat"))
            lon = float(item.get("lon"))
        except (TypeError, ValueError):
            continue

        # Lenient county post-filter: check county, state_district,
        # display_name and all address values for the county string.
        # This catches unitary authorities (Blackpool, Lancaster)
        # whose Nominatim `county` field differs from "Lancashire"
        # but whose `state_district` is "Lancashire".
        if county:
            addr = item.get("address", {}) or {}
            display = (item.get("display_name") or "").lower()
            addr_combined = " ".join(
                str(v) for v in addr.values() if v
            ).lower()
            county_lc = county.lower()
            if county_lc not in addr_combined and county_lc not in display:
                continue

        name = item.get("display_name") or item.get("name") or query
        results.append({
            "id": f"loc:{len(results)}",
            "name": name,
            "lat": lat,
            "lon": lon,
            "atco_code": None,
            "type": "location",
        })
        if len(results) >= limit:
            break
    return results


@app.get("/search/stops")
async def search_stops(
    q: str = "",
    limit: int = 10,
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
    Falls back to original variants if filtering removes everything.
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
    # nothing near the point
    return variants, False


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
        merged, _router, _walking = get_router_for_date(date_str)
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

    # NaPTAN coordinate lookup
    atco = _base_cache.get("atco_loader") if _base_cache else None
    coord_map: Dict[str, tuple] = {}
    if atco:
        t2 = time.time()
        try:
            coord_map = atco.get_all_stop_coords()
        except Exception:
            coord_map = {}
        t3 = time.time()
        try:
            logger.info(f"routes_for_line: atco.get_all_stop_coords took {int((t3-t2)*1000)}ms for line={line_key}")
        except Exception:
            pass

    # ── Collect route indices whose line_name matches ──────────────
    # line_name may be prefixed like "PC0002407:425:100" — match the
    # part after the last colon, which is what the frontend shows.
    matching_routes: list[int] = []
    for r_idx, meta in enumerate(merged.route_metadata):
        if meta is None:
            continue
        raw_line = (meta.get("line_name") or "").strip()
        rline = raw_line.split(":")[-1].upper()
        if rline == line_key:
            matching_routes.append(r_idx)

    # ── Build variants from *journey-level* stop sequences ───────
    # Using route_stops directly can produce interleaved inbound/outbound
    # lists (e.g. 98 stops zigzagging across the map).  Instead, for each
    # matching route we pick the representative journey with the most stops
    # — a single trip A→B whose stops are in correct geographic order.
    #
    # Build route_idx → list[journey_idx] mapping.
    route_journeys: dict[int, list[int]] = {r: [] for r in matching_routes}
    for j_idx, r_idx in enumerate(merged.journey_to_route):
        if r_idx in route_journeys:
            route_journeys[r_idx].append(j_idx)

    def _journey_stops(j_idx: int) -> list[dict]:
        """Extract ordered stop dicts from a single journey."""
        stops = []
        for entry in merged.journey_times[j_idx]:
            s_int = entry[0]
            atco_code = merged.get_atco_code(s_int)
            if not atco_code:
                continue
            coords = coord_map.get(atco_code)
            if not coords:
                continue
            lat, lon = coords
            name = merged.stop_metadata[s_int] if s_int < len(merged.stop_metadata) else ""
            stops.append({
                "name": name or atco_code,
                "lat": lat,
                "lon": lon,
                "atco_code": atco_code,
            })
        return stops

    import math

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
        j_list = route_journeys[r_idx]
        if not j_list:
            continue

        # Pick the best representative journey.  We want:
        #  - enough stops to trace the route (>= 10)
        #  - tight stop spacing (low mean gap) — avoids circular/merged
        #    journeys whose stops zigzag across the map.
        # Strategy: build stops for a few candidate journeys, pick the
        # one with the lowest mean consecutive gap.
        # To keep it fast, sample up to 8 candidates per route.
        candidates = sorted(
            j_list, key=lambda j: len(merged.journey_times[j]), reverse=True
        )[:8]

        best_stops = None
        best_gap = float("inf")
        for j in candidates:
            s = _journey_stops(j)
            if len(s) < min_stops:
                continue
            gap = _mean_gap(s)
            if gap < best_gap:
                best_gap = gap
                best_stops = s

        if best_stops and len(best_stops) >= 2:
            meta = merged.route_metadata[r_idx] or {}
            route_id = meta.get("route_id", f"route_{r_idx}")
            variants.append({"route_id": route_id, "stops": best_stops})

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
    # Attach stored route tracks (if present) as a 'geometry' field so
    # frontend consumers (RouteLineLayer) can draw road-following polylines
    # immediately instead of straight stop-to-stop lines. Use the existing
    # helper _fetch_route_tracks which returns [[lat, lon], ...].
    try:
        osrm_base = os.environ.get('OSRM_URL', 'http://localhost:5012')
        for v in result.get('variants', []):
            rid = v.get('route_id')
            if not rid:
                continue
            try:
                # 1) prefer stored route tracks
                t_var_start = time.time()
                tracks = _fetch_route_tracks(rid)
                if tracks and isinstance(tracks, list) and len(tracks) >= 2:
                    v['geometry'] = tracks
                    v['geometry_source'] = 'track'
                    continue

                # 2) if no stored tracks, attempt OSRM reconstruction from the
                #    variant's stop sequence (if present). This yields a road-
                #    following geometry that the frontend can render immediately.
                stops = v.get('stops') or []
                coords_lonlat = []
                for s in stops:
                    lat = s.get('lat')
                    lon = s.get('lon')
                    if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
                        coords_lonlat.append(f"{lon},{lat}")
                # dedupe while preserving order
                seen = set()
                dedup = []
                for s in coords_lonlat:
                    if s not in seen:
                        seen.add(s)
                        dedup.append(s)
                if len(dedup) >= 2:
                    try:
                        coords_from_osrm = _query_osrm_for_coords(osrm_base, dedup)
                        t_var_end = time.time()
                        try:
                            logger.info(f"routes_for_line: osrm query for rid={rid} took {int((t_var_end-t_var_start)*1000)}ms")
                        except Exception:
                            pass
                        if coords_from_osrm and len(coords_from_osrm) >= 2:
                            v['geometry'] = coords_from_osrm
                            v['geometry_source'] = 'osrm'
                    except Exception:
                        # ignore per-variant OSRM failures
                        pass
                # Final fallback: if no road-following geometry found, expose
                # the plain stop coordinates so the frontend can at least draw
                # a stop-to-stop polyline. This guarantees a visual even when
                # stored tracks and OSRM reconstruction are unavailable.
                if 'geometry' not in v and stops:
                    try:
                        v['geometry'] = [[s['lat'], s['lon']] for s in stops if isinstance(s.get('lat'), (int, float)) and isinstance(s.get('lon'), (int, float))]
                        if v['geometry'] and len(v['geometry']) >= 2:
                            v['geometry_source'] = 'stops'
                    except Exception:
                        # ignore and leave geometry absent
                        pass
            except Exception:
                # ignore per-variant failures — don't break the whole response
                continue
    except Exception:
        # defensive: if anything goes wrong attaching geometries, ignore
        pass
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

    # Helper to build journey stops (reuse code from routes_for_line)
    def _journey_stops(j_idx: int) -> list[dict]:
        stops = []
        # try to use atco loader coords via base cache
        atco = _base_cache.get("atco_loader") if _base_cache else None
        coord_map = {}
        if atco:
            try:
                coord_map = atco.get_all_stop_coords()
            except Exception:
                coord_map = {}
        for entry in merged.journey_times[j_idx]:
            s_int = entry[0]
            atco_code = merged.get_atco_code(s_int)
            if not atco_code:
                continue
            coords = coord_map.get(atco_code)
            if not coords:
                continue
            lat, lon = coords
            name = merged.stop_metadata[s_int] if s_int < len(merged.stop_metadata) else ""
            stops.append({"name": name or atco_code, "lat": lat, "lon": lon, "atco_code": atco_code})
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
        # find journeys belonging to this route
        j_list = [j for j, rid in enumerate(merged.journey_to_route) if rid == r_idx]
        if not j_list:
            continue
        candidates = sorted(j_list, key=lambda j: len(merged.journey_times[j]), reverse=True)[:8]
        best_stops = None
        best_gap = float('inf')
        for j in candidates:
            s = _journey_stops(j)
            if len(s) < min_stops:
                continue
            gap = _mean_gap(s)
            if gap < best_gap:
                best_gap = gap
                best_stops = s
        if best_stops and len(best_stops) >= 2:
            meta = merged.route_metadata[r_idx] or {}
            route_id = meta.get("route_id", f"route_{r_idx}")
            variants.append({"route_id": route_id, "stops": best_stops})

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

    # Fallback: if no variants found via merged journeys, query DB directly
    if not unique:
        try:
            import psycopg
            from main import BUS_DB_PATH
            conn = psycopg.connect(BUS_DB_PATH)
            cur = conn.cursor()
            cur.execute(
                "SELECT DISTINCT route_id FROM bus_route_stops WHERE atco_code = %s",
                (atco_code,)
            )
            rows = [r[0] for r in cur.fetchall()]
            routes = []
            if rows:
                # For each route, fetch ordered stop list and resolve coords via stop_coords
                for rid in rows:
                    cur.execute(
                        "SELECT atco_code, stop_order FROM bus_route_stops WHERE route_id = %s ORDER BY stop_order",
                        (rid,)
                    )
                    stop_rows = cur.fetchall()
                    # Resolve coords
                    atcos = [r[0] for r in stop_rows]
                    stops = []
                    if atcos:
                        # Query stop_coords for all atcos
                        placeholders = ','.join(['%s'] * len(atcos))
                        cur.execute(f"SELECT atco_code, lat, lon FROM stop_coords WHERE atco_code IN ({placeholders})", tuple(atcos))
                        coord_map = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
                        for atc, so in stop_rows:
                            coords = coord_map.get(atc)
                            if coords:
                                stops.append({"atco_code": atc, "lat": coords[0], "lon": coords[1], "stop_order": so})
                            else:
                                stops.append({"atco_code": atc, "stop_order": so})
                    routes.append({"route_id": rid, "stops": stops})
            cur.close()
            conn.close()
            return {"atco": atco_code, "routes": routes}
        except Exception:
            # If DB fallback fails, return empty list
            return {"atco": atco_code, "routes": []}

    return {"atco": atco_code, "routes": unique}


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


def _compute_delay_from_timetable(line_ref, dest, lat_v, lon_v, return_jid: bool = False, origin_dep_secs: int = None, operator_ref: str = None, strict_tol: int = 600, feed_origin_atco: str = None, feed_destination_atco: str = None, origin_tz_offset_secs: int = 0, allow_offtrack: bool = False, allow_abs_delay: bool = False):
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

    # Debug toggle: enable verbose matcher logging when MATCH_DEBUG=1 or BUS_LIVE_PROVENANCE is truthy
    try:
        DEBUG_MATCH = (str(os.environ.get('MATCH_DEBUG') or '') == '1') or (str(os.environ.get('BUS_LIVE_PROVENANCE') or '').lower() in ('1', 'true', 'yes'))
    except Exception:
        DEBUG_MATCH = False

    line_q = (line_ref or "").strip()
    dest_q = (dest or "").strip().lower()

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
                slat, slon = walking_mod.get_loc_coords(sid)
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

    # Build a map of normalized stop-name -> list of merged stop indices
    # so we can resolve feed destination free-text to ATCO codes when no
    # explicit feed-provided ATCO is available.
    stop_name_map = {}
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
        total_stops = len(merged.stop_metadata or [])
        for si in range(total_stops):
            try:
                sc = walking.get_loc_coords(si)
                if not sc or len(sc) != 2:
                    continue
                slat, slon = sc
                d = _hav(lat_v, lon_v, slat, slon)
                if d <= SEARCH_STOP_RADIUS_M:
                    nearby_stops_set.add(si)
            except Exception:
                continue
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

    for j_id, jmeta in enumerate(merged.journey_metadata):
        if not jmeta:
            continue
        line_name = jmeta.get("line_name") or ""
        simple_line = line_name.split(":")[-1] if line_name else ""
        if line_q and simple_line != line_q:
            continue
        # Gather endpoint ATCOs for staged matching.
        try:
            jt = merged.journey_times[j_id]
            if not jt:
                continue
            dest_atcos = []
            for i in range(1, min(4, len(jt) + 1)):
                atco = merged.get_atco_code(jt[-i][0])
                if atco: dest_atcos.append(str(atco).strip())
            
            origin_atcos = []
            for i in range(min(3, len(jt))):
                atco = merged.get_atco_code(jt[i][0])
                if atco: origin_atcos.append(str(atco).strip())
        except Exception:
            continue

        origin_atco_match = bool(feed_origin_atco_n and feed_origin_atco_n in origin_atcos)
        dest_atco_match = bool(feed_destination_atco_n and feed_destination_atco_n in dest_atcos)

        # If no explicit destination ATCO is provided, fall back to free-text
        # destination matching (previous behaviour) but do NOT treat that as a
        # destination-atco latch. It's just a weak hint.
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
            start_dep = jt[0][2]   # departure time of first stop
            end_arr = jt[-1][1] if jt[-1][1] is not None else jt[-1][2]
        except Exception:
            continue

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

    # Quick spatial pre-filter: ensure the candidate route/track has
    # at least one vertex within MATCH_MAX_TRACK_DIST_M of the
    # vehicle position. This prevents considering journeys in a
    # different town that happen to share the same short line
    # number / free-text destination.
        try:
            nearby_min = 1e9
            # Prefer cached route_tracks when present
            if r_int < len(merged.route_tracks) and merged.route_tracks[r_int]:
                pts = merged.route_tracks[r_int]
                for px, py in pts:
                    try:
                        d = _hav(lat_v, lon_v, px, py)
                        if d < nearby_min:
                            nearby_min = d
                    except Exception:
                        continue
            else:
                # Fall back to route_stops coordinates
                route_stops = merged.route_stops[r_int] if r_int < len(merged.route_stops) else []
                for sid in route_stops:
                    try:
                        coords = walking.get_loc_coords(sid)
                        if coords and len(coords) == 2:
                            d = _hav(lat_v, lon_v, coords[0], coords[1])
                            if d < nearby_min:
                                nearby_min = d
                    except Exception:
                        continue
            if nearby_min > MATCH_MAX_TRACK_DIST_M:
                # route too far from vehicle — skip candidate
                continue
        except Exception:
            # If anything goes wrong with the prefilter, don't block
            # matching — fall back to the original permissive behaviour.
            pass

        # Ensure the vehicle is near a stop that actually belongs to the
        # candidate journey. This helps avoid matching to a journey whose
        # geometry happens to pass nearby but does not serve the stop the
        # vehicle is at.
        try:
            nearby = walking.reachable_stops((lat_v, lon_v))
            if nearby:
                nearest_stop_int, walk_secs = nearby[0]
                jsi = merged.journey_stop_index[j_id] if j_id < len(merged.journey_stop_index) else {}
                if nearest_stop_int not in jsi:
                    # vehicle's nearest stop is not served by this journey
                    continue
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

    # Apply staged filtering requested by user.
    if not candidates:
        return None

    cand_ids = [c[0] for c in candidates]
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

    # Stage 3 latch: destination ATCO (for those not already latched)
    if not latched and feed_destination_atco_n:
        for j_id in cand_ids:
            f = staged_flags.get(j_id) or {}
            if f.get('dest_atco'):
                latched.add(j_id)

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

    _track_cache = {}

    # Spatial gating threshold (metres): if a vehicle is further than this
    # distance from the route track, consider it off-track and skip the
    # candidate. Make this configurable via env var so operators can tune
    # for noisy GPS or coarse route geometry.
    try:
        MATCH_MAX_TRACK_DIST_M = int(os.environ.get('MATCH_MAX_TRACK_DIST_M', '2000'))
    except Exception:
        MATCH_MAX_TRACK_DIST_M = 2000

    # Configurable stricter gating thresholds (seconds/metres). These
    # default to conservative values but can be tuned via env vars.
    try:
        MATCH_ALLOW_BEFORE_S = int(os.environ.get('MATCH_ALLOW_BEFORE_S', '300'))
    except Exception:
        MATCH_ALLOW_BEFORE_S = 300
    try:
        MATCH_ALLOW_AFTER_S = int(os.environ.get('MATCH_ALLOW_AFTER_S', '300'))
    except Exception:
        MATCH_ALLOW_AFTER_S = 300
    try:
        MATCH_DEST_IGNORE_M = int(os.environ.get('MATCH_DEST_IGNORE_M', '30'))
    except Exception:
        MATCH_DEST_IGNORE_M = 30

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
        # Get or compute track + cumulative distances. Prefer a track
        # built from the candidate's scheduled stop ATCO coordinates
        # (the journey's `jt`) so expected_time is computed from the
        # journey_times + ATCO coords rather than from the route
        # polyline geometry. Fall back to route-level tracks/stops when
        # journey stop coordinates are unavailable.
        if r_int not in _track_cache:
            # First: attempt to build a journey-specific track from jt
            track = []
            try:
                for sid, atime, dtime in jt:
                    try:
                        slat, slon = walking.get_loc_coords(sid)
                        track.append((slat, slon))
                    except Exception:
                        continue
            except Exception:
                track = []

            # If the journey-based track is not usable, fall back to
            # route-level geometry (existing behaviour).
            if not track:
                track = merged.route_tracks[r_int] if r_int < len(merged.route_tracks) else []
                if not track:
                    route_stops = merged.route_stops[r_int] if r_int < len(merged.route_stops) else []
                    track = []
                    for sid in route_stops:
                        try:
                            slat, slon = walking.get_loc_coords(sid)
                            track.append((slat, slon))
                        except Exception:
                            continue

            if not track:
                # Fatal gate: no route/journey track available -> skip this candidate.
                # Cache the failure to avoid repeated attempts for the same route.
                _track_cache[r_int] = None
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
        # progress-margin to ensure the vehicle projects sensibly onto the
        # route geometry. This prevents matching to distant routes that
        # merely share a short line name.
        try:
            offtrack = False
            if MATCH_MAX_TRACK_DIST_M is not None and dist_m > MATCH_MAX_TRACK_DIST_M:
                offtrack = True

            # Require the projected progress to fall near the route segment
            # (allow a small margin for endpoints). If the vehicle projects
            # far before the first vertex or beyond the last, consider it
            # off-track.
            try:
                PROGRESS_MARGIN = float(os.environ.get('MATCH_PROGRESS_MARGIN', '0.02'))
            except Exception:
                PROGRESS_MARGIN = 0.02
            if progress < -PROGRESS_MARGIN or progress > (1.0 + PROGRESS_MARGIN):
                offtrack = True

            if offtrack and not allow_offtrack:
                if str(os.environ.get('BUS_LIVE_PROVENANCE') or '').lower() in ('1', 'true', 'yes'):
                    logger.info("matcher: candidate j_id=%s rejected for being off-track (dist_m=%.1f prog=%.3f > dist_thresh=%s margin=%s)", j_id, dist_m, progress, MATCH_MAX_TRACK_DIST_M, PROGRESS_MARGIN)
                continue
        except Exception:
            # On failure of the on-track check, fall back to permissive
            # behaviour so we don't silently drop valid candidates.
            pass

        # Additional segment-level on-track check: require the vehicle to
        # be reasonably close to the track at the projected point. This
        # is a stricter criterion than MATCH_MAX_TRACK_DIST_M and helps
        # avoid matching to nearby-but-not-served routes. Configurable
        # via MATCH_SEGMENT_DIST_M (default 50m).
        try:
            try:
                MATCH_SEGMENT_DIST_M = int(os.environ.get('MATCH_SEGMENT_DIST_M', '500'))
            except Exception:
                MATCH_SEGMENT_DIST_M = 500
            if MATCH_SEGMENT_DIST_M is not None and dist_m > MATCH_SEGMENT_DIST_M:
                if not allow_offtrack:
                    if str(os.environ.get('BUS_LIVE_PROVENANCE') or '').lower() in ('1', 'true', 'yes'):
                        logger.info("matcher: candidate j_id=%s rejected by segment-distance (dist_m=%.1f > %d)", j_id, dist_m, MATCH_SEGMENT_DIST_M)
                    continue
        except Exception:
            pass

        # Nearest-stop inclusion check: ensure the vehicle's nearest reachable
        # stop (by walking module) is actually served by this candidate journey.
        # This avoids matching to journeys that pass nearby but do not serve the
        # same boarding stop. Configurable via MATCH_REQUIRE_NEAREST_STOP (default true).
        try:
            try:
                MATCH_REQUIRE_NEAREST_STOP = os.environ.get('MATCH_REQUIRE_NEAREST_STOP', '1')
                MATCH_REQUIRE_NEAREST_STOP = str(MATCH_REQUIRE_NEAREST_STOP).strip() not in ('0', 'false', 'no')
            except Exception:
                MATCH_REQUIRE_NEAREST_STOP = True
            if MATCH_REQUIRE_NEAREST_STOP and not allow_offtrack:
                try:
                    nearby = walking.reachable_stops((lat_v, lon_v))
                except Exception:
                    nearby = []
                if nearby:
                    nearest_stop_int = nearby[0][0]
                    # If the nearest reachable stop is physically far away,
                    # reject this candidate — the vehicle is not plausibly
                    # at that stop. Threshold configurable via
                    # MATCH_NEAREST_STOP_MAX_M (default 200m).
                    try:
                        try:
                            MATCH_NEAREST_STOP_MAX_M = int(os.environ.get('MATCH_NEAREST_STOP_MAX_M', '4000'))
                        except Exception:
                            MATCH_NEAREST_STOP_MAX_M = 4000
                        try:
                            s_lat, s_lon = walking.get_loc_coords(nearest_stop_int)
                            nearest_stop_dist_m = _hav(lat_v, lon_v, s_lat, s_lon)
                        except Exception:
                            nearest_stop_dist_m = None
                        if nearest_stop_dist_m is not None and MATCH_NEAREST_STOP_MAX_M is not None and nearest_stop_dist_m > MATCH_NEAREST_STOP_MAX_M:
                            if str(os.environ.get('BUS_LIVE_PROVENANCE') or '').lower() in ('1', 'true', 'yes'):
                                logger.info("matcher: candidate j_id=%s rejected because nearest stop is too far (%.1f m > %d m)", j_id, nearest_stop_dist_m, MATCH_NEAREST_STOP_MAX_M)
                            continue
                    except Exception:
                        # If anything goes wrong computing distances, continue permissively
                        pass
                    # Build a set of stop ints served by this journey
                    try:
                        journey_stop_ints = [sid for sid, at, dt in jt]
                    except Exception:
                        journey_stop_ints = []
                    # Require that the journey serves at least one of the
                    # stops within the precomputed nearby_stops_set. This
                    # enforces the "only map to journeys that pass through
                    # stops within X metres" rule.
                    try:
                        if nearby_stops_set and not (set(journey_stop_ints) & nearby_stops_set):
                            if str(os.environ.get('BUS_LIVE_PROVENANCE') or '').lower() in ('1', 'true', 'yes'):
                                logger.info("matcher: candidate j_id=%s rejected because it serves none of the nearby stops", j_id)
                            continue
                    except Exception:
                        pass

                    if nearest_stop_int not in journey_stop_ints:
                        if str(os.environ.get('BUS_LIVE_PROVENANCE') or '').lower() in ('1', 'true', 'yes'):
                            logger.info("matcher: candidate j_id=%s rejected because nearest_stop %s not in journey stops", j_id, nearest_stop_int)
                        continue
                else:
                    # If walking cannot find any nearby stops, be permissive
                    # and allow other checks to decide.
                    pass
        except Exception:
            pass

        # Origin proximity check removed by request: do not reject
        # candidates solely because they appear near the route start and
        # the nearest stop differs. This makes the matcher permissive for
        # vehicles that may have slightly shifted GPS positions at origin.

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
                dest_coords = walking.get_loc_coords(dest_stop_int)
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

    # If the chosen journey's scheduled start time is more than 30 minutes in the future,
    # reject the bus entirely (it likely mapped incorrectly or shouldn't be tracked yet).
    if best_start_dep is not None and best_start_dep - now_seconds > 1800:
        if str(os.environ.get('BUS_LIVE_PROVENANCE') or '').lower() in ('1', 'true', 'yes'):
            logger.info("matcher: rejecting matched bus for journey %s because its start_dep %d is > 30 mins in future", best_jid, best_start_dep)
        return (None, None) if return_jid else None

    # Clamp negative delays to zero at the source of computation so
    # callers don't have to special-case early/negative values.
    if best_delay is not None and best_delay < 0:
        best_delay = 0
    if return_jid:
        return (best_delay, best_jid)
    return best_delay


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
        return []

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
        # If we have any aligned origin matches, they win outright.
        if aligned:
            latched = aligned
        # Otherwise, restrict to journeys that at least contain the origin ATCO.
        elif origin_only:
            latched = origin_only

    # Stage 3 latch: destination ATCO (for those not already latched)
    if not latched and feed_destination_atco_n:
        for j_id in candidates:
            f = staged_flags.get(j_id) or {}
            if f.get('dest_atco'):
                latched.add(j_id)

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

    return sorted(latched) if latched else sorted(candidates)


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


def _fetch_all_live_buses() -> list:
    """Return all live bus records from all operators (cached)."""
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
    latTol: float = 0.0003,
    lonTol: float = 0.0003,
):
    """Get live bus positions for a specific operator."""
    from fastapi.responses import JSONResponse

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
        )
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"error": str(exc)},
        )

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
    # Attempt to load today's stop metadata and walking helper so we can
    # filter out vehicles that are already at (or within 50m of) their
    # destination stop. Failure to load merged/router/walking should not
    # block the endpoint — in that case we simply skip the proximity filter.
    try:
        from datetime import datetime as _dt
        today_date = _dt.now().date().isoformat()
        now_secs = _dt.now().hour * 3600 + _dt.now().minute * 60 + _dt.now().second
        _merged, _rtr, _walking = get_router_for_date(today_date, start_time=now_secs, apply_delay=False)
        # Build a map of normalized stop-name -> list of (stop_idx, lat, lon)
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
                # ignore stops we cannot resolve
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
        matched_jid = None
        # Diagnostic fields only computed when bus_provenance is enabled
        reject_reasons = None
        match_reason = None
        if delay_s is None:
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
                )
                if matched:
                    # matched is expected to be a (delay_s, jid_int) tuple when return_jid=True.
                    computed, matched_jid = matched

                    # (None, None) means "no match" (not a contract violation).
                    if computed is None or matched_jid is None:
                        computed = None
                        matched_jid = None
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

                        # Only flag invalid_match_missing_jid when the matcher claims
                        # it found a delay but did not provide a jid.
                        if bus_provenance and computed is not None and matched_jid is None:
                            reject_reasons = reject_reasons or []
                            if 'invalid_match_missing_jid' not in reject_reasons:
                                reject_reasons.append('invalid_match_missing_jid')
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
        if matched_jid is None and not show_unmatched:
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
        # If our strict timetable matcher returned a journey id, expose the
        # external journey_id string from the merged data so the frontend
        # can deterministically identify this vehicle's journey. Also expose
        # diagnostic fields when available so clients can see why a vehicle
        # was rejected by the strict matcher.
        try:
            _jm_debug = None
            if matched_jid is not None and _merged and matched_jid < len(_merged.journey_metadata):
                jm = _merged.journey_metadata[matched_jid] or {}
                if bus_provenance:
                    try:
                        if isinstance(jm, dict):
                            _jm_debug = {
                                'matched_jid': matched_jid,
                                'journey_meta_is_none': _merged.journey_metadata[matched_jid] is None,
                                'journey_meta_keys': sorted(list(jm.keys())),
                            }
                        else:
                            _jm_debug = {
                                'matched_jid': matched_jid,
                                'journey_meta_type': str(type(jm)),
                            }
                    except Exception:
                        _jm_debug = {'matched_jid': matched_jid}
                if jm.get("journey_id"):
                    entry["logged_journey_id"] = jm.get("journey_id")
                elif bus_provenance:
                    # In provenance mode, surface a best-effort identifier so the
                    # frontend can still treat the vehicle as mapped when we have
                    # a timetable match but no merged journey_id string.
                    #
                    # NOTE: This is intentionally best-effort and marked as such.
                    if meta and isinstance(meta, dict):
                        fallback_id = (
                            meta.get("vehicle_journey_code")
                            or meta.get("dated_journey_ref")
                            or meta.get("framed_journey_ref")
                        )
                        if fallback_id:
                            entry["logged_journey_id"] = str(fallback_id)
                            entry["logged_journey_id_source"] = "feed_fallback"
        except Exception:
            # best-effort only
            pass

        # In provenance mode, treat "matched" as a stronger contract:
        # if we say it's matched, we must also provide a journey identifier
        # so clients (and the frontend) can treat it as mapped.
        if bus_provenance:
            try:
                if (match_reason == 'matched' or matched_jid is not None) and not entry.get('logged_journey_id'):
                    # We couldn't build a stable id for this match; downgrade.
                    match_reason = 'unmatched'
                    if reject_reasons is None:
                        reject_reasons = []
                    if 'matched_but_missing_logged_journey_id' not in reject_reasons:
                        reject_reasons.append('matched_but_missing_logged_journey_id')
                    # Add extra context so we can debug why the merged timetable
                    # lacks a journey_id for this matched_jid.
                    if matched_jid is None:
                        if 'missing_matched_jid' not in reject_reasons:
                            reject_reasons.append('missing_matched_jid')
                    else:
                        if 'missing_merged_journey_id' not in reject_reasons:
                            reject_reasons.append('missing_merged_journey_id')
                        if _jm_debug and not entry.get('journey_id_debug'):
                            entry['journey_id_debug'] = _jm_debug
            except Exception:
                # Don't break the endpoint for provenance bookkeeping.
                pass
        # Expose diagnostic fields only when provenance mode is enabled
        if bus_provenance:
            entry['match_reason'] = match_reason or ('matched' if matched_jid is not None else 'unmatched')
            entry['reject_reasons'] = reject_reasons or []
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
    latTol: float = 0.25,
    lonTol: float = 0.25,
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
        t.start()
    except Exception:
        # If background thread creation fails, continue without prebuilding.
        logger.exception('Failed to start prebuild thread for routers')


def _delay_updater_loop(stop_event: threading.Event):
    """Background loop to periodically refresh the delay map."""
    # Run once immediately, then sleep interval
    while not stop_event.is_set():
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

def get_router_for_date(date_str, start_time=None, apply_delay: bool = True):
    """Return (merged, router, walking) for a given date and time bucket.

    The cache key includes the AM/PM bucket so morning and afternoon
    queries use the correct two-day merge.
    """
    global _base_cache, _router_cache
    bucket = "AM" if (start_time is not None and start_time < 43200) else "PM"
    # When apply_delay is requested for today's date, include the
    # current delay-map version in the cache key so we rebuild the
    # router when the delay map changes.
    today_str = datetime.now().date().isoformat()
    if apply_delay and date_str == today_str:
        cache_key = (date_str, bucket, _delay_map_version)
    else:
        cache_key = (date_str, bucket)
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
    global _base_init_attempted
    # If initialization hasn't run yet, run it synchronously (same as before)
    if _base_cache is None and not _base_init_attempted:
        from main import initialize_base
        try:
            _base_cache = initialize_base()
            if _base_cache and "prebuilt_cache" in _base_cache:
                _router_cache.update(_base_cache["prebuilt_cache"])
            # If the cache was populated during init, return it
            with _router_cache_lock:
                if cache_key in _router_cache:
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
                    _router_cache.update(_base_cache["prebuilt_cache"])
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
    merged, router, walking = build_for_date(
        loader, walking_raw, date_str, start_time=start_time,
        atco_loader=al)
    # If we're asked to apply today's delay map, modify a deep copy
    # of the merged timetable by adding per-journey delays and then
    # rebuild the router from that adjusted merged object.
    if apply_delay and date_str == today_str and _journey_delay_map:
        try:
            from raptor_router import RaptorRouter
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
            _set_router_cache(cache_key, (adj_merged, router, walking))
            return adj_merged, router, walking
        except Exception:
            # Fall back to unadjusted merged/router on any failure
            _set_router_cache(cache_key, (merged, router, walking))
            return merged, router, walking

    _set_router_cache(cache_key, (merged, router, walking))
    return merged, router, walking

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


def build_journey_plan_response(route_result, merged, stop_coords, request_start_seconds=None):
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

    if not route_result:
        return {
            "success": True,
            "legs": [],
            "journeys": [],
            "meta": {},
            "routeGeometries": [],
        }

    meta = route_result.get("_meta", {})

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
        if k not in ("_meta", "_route_id") and isinstance(v, dict)
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

        The frontend needs a stop id (ATCO code) to request a route_tracks
        subsegment for bus legs. Provide it consistently on every from_stop /
        to_stop object.
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

    _COLOR = {"walking": "#888888", "bus": "#1a73e8", "train": "#e53935"}
    legs = []
    geometries = []
    geo_idx = 0

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
            next_mode = (next_info.get("mode") or "").lower()
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
        })
        geo_idx += 1

    # -- Transit / walking legs between stops --
    for i in range(1, len(ordered)):
        prev_int, prev_info = ordered[i - 1]
        curr_int, curr_info = ordered[i]
        transport = curr_info.get("mode") or curr_info.get("type") or "unknown"
        prev_coord = _get_stop_coord(prev_int)
        curr_coord = _get_stop_coord(curr_int)

        # Include a stable stop id (ATCO code) so the frontend can request
        # bus route_tracks subsegments.
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

        track_coords = None
        if transport != "walking":
            try:
                j_info = curr_info.get("journey_info") or {}
                if isinstance(j_info, dict) and j_info.get("route_id"):
                    route_id = j_info.get("route_id")
                    
                    try:
                        prev_atco = merged.get_atco_code(prev_int)
                    except Exception:
                        prev_atco = None
                        
                    try:
                        curr_atco = merged.get_atco_code(curr_int)
                    except Exception:
                        curr_atco = None
                        
                    if prev_atco and curr_atco:
                        local_coords = {}
                        if prev_coord:
                            local_coords[prev_atco] = prev_coord
                        if curr_coord:
                            local_coords[curr_atco] = curr_coord
                            
                        seg = _subsegment_from_tracks(route_id, [prev_atco, curr_atco], local_coords)
                        if seg and len(seg) >= 2:
                            track_coords = seg
            except Exception as exc:
                pass

        if track_coords:
            coords = track_coords
        else:
            if prev_coord:
                coords.append([prev_coord[0], prev_coord[1]])
            if curr_coord:
                coords.append([curr_coord[0], curr_coord[1]])

        geometries.append({
            "id": f"{transport}-{geo_idx}",
            "name": geo_name,
            "coords": coords,
            "color": color,
        })
        geo_idx += 1

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
            # Expose a canonical route_id (if the router attached one)
            # so clients can lookup in-memory route_tracks. Routers no
            # longer expose logged_journey_id — downstream callers should
            # use this route_id to retrieve tracks from the in-memory
            # timetable (MergedData.route_tracks).
            "route_id": (route_result.get("_route_id") if isinstance(route_result, dict) else None),
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

    # Note: routers do not need to surface a logged_journey_id; callers
    # should use the returned `route_id` meta to lookup in-memory tracks.
    # We still persist a minimal logged_journey for auditing where the
    # router did not attach one, but we do not expose its id to clients.
    try:
        if isinstance(route_result, dict) and not route_result.get('_logged_journey_id'):
            import uuid
            from main import BUS_DB_PATH
            from bus_loader import BusLoader

            lj = {
                'id': uuid.uuid4().hex,
                'created_at': None,
                'start_point': tuple(start_point) if start_point else None,
                'destination_point': tuple(destination_point) if destination_point else None,
                'total_arrival': meta.get('total_arrival'),
                'legs': [],
            }
            # Build minimal legs with from_stop/to_stop coords (if available)
            for leg in legs:
                l = {}
                if 'journey_origin' in leg or 'journey_destination' in leg or leg.get('line_name'):
                    l['journey_metadata'] = {
                        'line_name': leg.get('line_name'),
                        'destination_display': leg.get('journey_destination')
                    }
                fs = leg.get('from_stop') or {}
                ts = leg.get('to_stop') or {}
                if isinstance(fs, dict) and 'lat' in fs and 'lon' in fs:
                    l['from_stop'] = {'lat': fs['lat'], 'lon': fs['lon']}
                if isinstance(ts, dict) and 'lat' in ts and 'lon' in ts:
                    l['to_stop'] = {'lat': ts['lat'], 'lon': ts['lon']}
                lj['legs'].append(l)

            try:
                bl = BusLoader(BUS_DB_PATH)
                bl.insert_logged_journey(lj)
                # Do NOT annotate route_result or response with the logged_journey id.
            except Exception:
                pass
    except Exception:
        pass

    return resp


@app.post("/journey/plan")
async def journey_plan(request: JourneyPlanRequest):
    """Plan a journey between two locations.

    Accepts fromStop/toStop as {lat, lon} objects, runs the
    RAPTOR router, and returns structured legs plus
    routeGeometries for map polyline rendering.
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

        # Run potentially-heavy router construction and routing in a thread
        # so we don't block the ASGI event loop (keeps WebSocket/live updates responsive).
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
        stop_coords = getattr(walking, "_coords", {})
        return build_journey_plan_response(
            result, merged, stop_coords, request_start_seconds=start_seconds)
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

        # Build / fetch merged data + main router + walking helper in a thread
        merged, main_router, walking = await asyncio.to_thread(
            get_router_for_date, date_str, start_time=start_seconds
        )

        # Lazily construct optional routers (eco, cosy, lazy, greedy).
        # optional routers: eco, cosy, lazy, greedy
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
            from cosy_router import RaptorRouter as CosyRaptor
            cosy_router = CosyRaptor(merged)
        except Exception:
            cosy_router = None
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
            ("eco", eco_router), ("cosy", cosy_router),
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
        if cosy_router is not None:
            routers.append(("cosy", cosy_router))
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
        cosy_res, cosy_time = router_results.get("cosy", (None, None))
        lazy_res, lazy_time = router_results.get("lazy", (None, None))
        greedy_res, greedy_time = router_results.get("greedy", (None, None))

        stop_coords = getattr(walking, "_coords", {})

        # Build full journey-plan responses (same shape as /journey/plan)
        main_plan = build_journey_plan_response(main_res, merged, stop_coords, request_start_seconds=start_seconds) if main_res is not None else None
        eco_plan = build_journey_plan_response(eco_res, merged, stop_coords, request_start_seconds=start_seconds) if eco_res is not None else None
        cosy_plan = build_journey_plan_response(cosy_res, merged, stop_coords, request_start_seconds=start_seconds) if cosy_res is not None else None
        lazy_plan = build_journey_plan_response(lazy_res, merged, stop_coords, request_start_seconds=start_seconds) if lazy_res is not None else None
        greedy_plan = build_journey_plan_response(greedy_res, merged, stop_coords, request_start_seconds=start_seconds) if greedy_res is not None else None

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
            "cosy": {
                "route": cosy_plan,
                "route_text": format_route_text(cosy_res, merged) if cosy_res is not None else None,
                "time_seconds": cosy_time,
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
        # (main, eco, cosy, lazy, greedy) as `/journey/compare`.
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
        # FIXME: Hardcoded Lancaster Station
        s["lat"] = 54.0486361
        s["lon"] = -2.80811389
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



