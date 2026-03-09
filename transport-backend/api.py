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

logger = logging.getLogger(__name__)

# Add the current directory to the path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))


# Routing request/response models
class RouteRequest(BaseModel):
    start_lat: float
    start_lon: float
    end_lat: float
    end_lon: float
    date: str
    time: str  # HH:MM:SS
    max_transfers: int = 5
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
    max_transfers: int = 5
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
        _base_cache = initialize_base()
    except Exception as exc:  # pragma: no cover
        logger.warning(
            "Backend initialisation failed  — endpoints requiring "
            "transport data will be unavailable: %s", exc,
        )
    # Configure and start the WebSocket/STOMP live-updates broker
    try:
        ws_broker.configure(bus_live_factory=lambda: BusLive(timeout=10))
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

# -- WebSocket/STOMP live updates endpoint --------------------------------
app.add_api_websocket_route("/ws/live", ws_live_endpoint)

# -- CORS ------------------------------------------------------------------
# Read allowed origins from the CORS_ORIGINS env var (comma-separated).
# Falls back to localhost dev ports so local development works out of the box.
_default_origins = (
    "http://localhost:3000,http://localhost:5075,"
    "http://127.0.0.1:3000,http://127.0.0.1:5075"
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


@app.get("/alerts")
async def get_alerts():
    """Return active service disruption alerts.

    Currently returns an empty list — a real implementation would query
    a live feed (e.g. Traveline or Bods Disruptions API) and cache the
    results.  The response shape is intentionally stable so the frontend
    ``fetchServiceAlerts()`` hook can use it without changes.
    """
    return []


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
    for word in words:
        if len(word) < 4:
            corrected_words.append(word)
            continue
        word_matches = get_close_matches(
            word.lower(), _LANCASHIRE_PLACES_LOWER, n=1, cutoff=threshold
        )
        if word_matches:
            idx = _LANCASHIRE_PLACES_LOWER.index(word_matches[0])
            corrected_words.append(_LANCASHIRE_PLACES[idx])
            changed = True
        else:
            corrected_words.append(word)
    if changed:
        return " ".join(corrected_words)

    return q


def _looks_like_street(s: str) -> bool:
    """Heuristic: return True if the suffix looks like a street/address.

    Checks for house numbers, common street-type tokens (street, rd,
    lane, avenue, drive, etc.) or short numeric/postcode-like tokens.
    Used to decide whether the text after a comma should be treated as
    a town/city filter (False) or as part of a street address (True).
    """
    if not s:
        return False
    s = s.strip().lower()
    import re
    # If it contains a number (house number, postcode fragment), treat as street/address
    if re.search(r"\d", s):
        return True

    # Common street-type tokens
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

    # Very short tails (1-3 chars) are more likely postcode fragments or abbreviations — treat as street-like
    if 0 < len(s) <= 3:
        return True

    return False


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
            if atco_loader:
                try:
                    # Use the atco_loader's DB to search stop_coords by name
                    conn = atco_loader._connect()
                    cur = conn.cursor()
                    cur.execute(
                        "SELECT atco_code, name, town, lat, lon FROM stop_coords "
                        "WHERE LOWER(name) LIKE LOWER(%s) LIMIT %s",
                        (f"%{q}%", limit),
                    )
                    rows = cur.fetchall()
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
            else:
                # Fallback to legacy loader when ATCO loader unavailable
                loader = _base_cache.get("loader") if _base_cache else None
                if loader:
                    stop_results = loader.search_stops(q, limit)
                    for stop in stop_results:
                        stop["type"] = "stop"
        except Exception as exc:
            logger.warning("Stop DB lookup failed: %s", exc)

        try:
            location_results = geocode_locations(q, limit)
        except Exception as exc:
            logger.warning("Geocoding lookup failed: %s", exc)

        # Backfill missing coordinates for stop_results when possible.
        # First try the ATCO/NaPTAN `stop_coords` table via atco_loader.
        try:
            missing_atcos = [s.get("atco_code") for s in (stop_results or []) if s.get("atco_code") and (s.get("lat") is None or s.get("lon") is None)]
            if missing_atcos:
                atco_loader = _base_cache.get("atco_loader") if _base_cache else None
                if atco_loader:
                    try:
                        conn = atco_loader._connect()
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
                    candidates = geocode_locations(query_text, 1)
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


@app.get("/routes/line/{line}")
async def routes_for_line(line: str):
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

    if line_key in _route_line_cache:
        return _route_line_cache[line_key]

    date_str = datetime.now().strftime("%Y-%m-%d")
    try:
        merged, _router, _walking = get_router_for_date(date_str)
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"error": "Backend not initialized"},
        )

    # NaPTAN coordinate lookup
    atco = _base_cache.get("atco_loader") if _base_cache else None
    coord_map: Dict[str, tuple] = {}
    if atco:
        try:
            coord_map = atco.get_all_stop_coords()
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
    # Only cache positive results. Caching empty variant lists can cause
    # stale-empty responses when the router/atco caches are built later
    # (for example shortly after server start). Allow empty results to be
    # recomputed on subsequent calls so late-initialised data can populate
    # the response.
    if unique:
        _route_line_cache[line_key] = result
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


def _compute_delay_from_timetable(line_ref, dest, lat_v, lon_v, return_jid: bool = False, origin_dep_secs: int = None):
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

    line_q = (line_ref or "").strip()
    dest_q = (dest or "").strip().lower()

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

        # Also check all vertices (handles single-point tracks, endpoints)
        for i in range(len(track)):
            d = _hav(plat, plon, track[i][0], track[i][1])
            if d < best_dist:
                best_dist = d
                best_along = cum_dists[i]

        return (best_dist, best_along / total_len)

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

    # ── 1. collect candidate journeys ──
    candidates = []
    for j_id, jmeta in enumerate(merged.journey_metadata):
        if not jmeta:
            continue
        line_name = jmeta.get("line_name") or ""
        simple_line = line_name.split(":")[-1] if line_name else ""
        if line_q and simple_line != line_q:
            continue
        dest_display = (jmeta.get("destination_display") or "").lower()
        if dest_q and dest_q not in dest_display:
            continue
        try:
            jt = merged.journey_times[j_id]
            if not jt:
                continue
            start_dep = jt[0][2]   # departure time of first stop
            # Safe end_arr: prefer arrival, fallback to departure
            end_arr = jt[-1][1] if jt[-1][1] is not None else jt[-1][2]
        except Exception:
            continue

        # Day guard: skip journeys entirely outside today
        try:
            if end_arr is None or start_dep is None:
                continue
            if end_arr < 0 or start_dep >= 86400:
                continue
        except Exception:
            continue

        r_int = merged.journey_to_route[j_id] if j_id < len(merged.journey_to_route) else -1
        if r_int < 0:
            continue

        # ── OriginAimedDepartureTime gate ──
        # When the live feed tells us what time the vehicle was *supposed*
        # to depart the origin, reject any candidate journey whose first-
        # stop departure doesn't match within a tight tolerance (120 s).
        # This is the strongest single discriminator because a vehicle
        # knows which service it is running.
        if origin_dep_secs is not None:
            if abs(start_dep - origin_dep_secs) > 120:
                continue

        candidates.append((j_id, start_dep, end_arr, r_int))

    if not candidates:
        return None

    # ── 2. score each candidate ──
    best_delay = None
    best_score = None
    best_jid = None

    _track_cache = {}

    for j_id, start_dep, end_arr, r_int in candidates:
        # Get or compute track + cumulative distances
        if r_int not in _track_cache:
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
                _track_cache[r_int] = None
                continue
            cum = _cum_distances(track)
            _track_cache[r_int] = (track, cum)
        else:
            cached = _track_cache[r_int]
            if cached is None:
                continue
            track, cum = cached

        # Project vehicle onto track (edge-interpolated)
        dist_m, progress = _project_onto_track(lat_v, lon_v, track, cum)

        # ── spatial gate: must be within 800 m of track ──
        if dist_m > 800:
            continue

        # ── origin check when vehicle appears near route start ──
        try:
            jt = merged.journey_times[j_id]
        except Exception:
            continue
        if progress < 0.15 and jt:
            try:
                first_stop_int = jt[0][0]
                first_atco = merged.get_atco_code(first_stop_int)
                nearby = walking.reachable_stops((lat_v, lon_v))
                if nearby:
                    nearest_stop_int, walk_secs = nearby[0]
                    nearest_atco = merged.get_atco_code(nearest_stop_int)
                    if first_atco and nearest_atco and first_atco != nearest_atco:
                        try:
                            fs_lat, fs_lon = walking.get_loc_coords(first_stop_int)
                            if _hav(lat_v, lon_v, fs_lat, fs_lon) > 300:
                                continue
                        except Exception:
                            pass
                else:
                    try:
                        fs_lat, fs_lon = walking.get_loc_coords(first_stop_int)
                        if _hav(lat_v, lon_v, fs_lat, fs_lon) > 300:
                            continue
                    except Exception:
                        pass
            except Exception:
                pass

        # ── temporal gates ──
        journey_dur = max(end_arr - start_dep, 1)

        # Bus near start but journey hasn't departed yet
        if progress < 0.05 and now_seconds < start_dep - 300:
            continue

        # Bus near end and well past last arrival
        if progress > 0.95 and now_seconds > end_arr + 300:
            continue

        # Journey finished and bus isn't near the end
        if now_seconds > end_arr + 300 and progress < 0.85:
            continue

        # Too far in time (> 2 hours from window)
        if now_seconds < start_dep - 7200 or now_seconds > end_arr + 7200:
            continue

        # Journey fully elapsed and not near terminus
        if journey_dur > 0 and now_seconds > end_arr + max(600, journey_dur * 0.5):
            continue

        # ── 3. interpolate expected_time ──
        stop_progs = _stop_progress_on_track(jt, walking, track, cum)
        if not stop_progs:
            continue

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
            continue

        delay = int(now_seconds - expected_time)

        # ── sanity clamp: reject absurd delays ──
        # For a 60-min journey, allow at most 30 min delay (or 20 min
        # minimum).  The old threshold of max(dur*1.5, 3600) was far too
        # generous and let 60+ min "delays" through.
        max_plausible = max(int(journey_dur * 0.5), 1200)
        if abs(delay) > max_plausible:
            continue

        # ── deterministic, time-stable scoring ──
        # Primary: distance to track (lower = better).
        # Secondary: absolute delay – the best match is the journey whose
        #   interpolated expected_time is closest to now.  This is critical
        #   when multiple journeys share the same route (same dist_m): the
        #   old j_id tiebreaker picked the *earliest* departure, producing
        #   huge phantom delays.
        # Tertiary: j_id for absolute determinism when everything ties.
        score = (dist_m, abs(delay), j_id)

        if best_score is None or score < best_score:
            best_score = score
            best_delay = delay
            best_jid = j_id

    if return_jid:
        return (best_delay, best_jid)
    return best_delay


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
        bl = BusLive(timeout=10)
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
    for line_ref, dest, lat_v, lon_v, _op, delay_s, origin_dep in buses:
        # Exact short line name match
        short = (line_ref or "").split(":")[-1].strip()
        if short != line_q:
            continue
        if delay_s is not None:
            delays.append(delay_s)
        else:
            try:
                computed = _compute_delay_from_timetable(line_ref, dest, lat_v, lon_v, origin_dep_secs=origin_dep)
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
    for line_ref, dest, lat_v, lon_v, _operator, delay_s, origin_dep in results:
        computed = None
        if delay_s is None:
            try:
                computed = _compute_delay_from_timetable(line_ref, dest, lat_v, lon_v, origin_dep_secs=origin_dep)
            except Exception:
                computed = None
        final_delay = delay_s if delay_s is not None else computed
        out.append({
            "line": line_ref,
            "destination": dest,
            "lat": lat_v,
            "lon": lon_v,
            "operator": _operator,
            "delay_minutes": round(final_delay / 60, 1) if final_delay is not None else None,
            "status": _bus_delay_status(final_delay),
        })
    return out

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
_base_cache = None

# Real-time delay map: {journey_id: delay_seconds}
_journey_delay_map: Dict[int, int] = {}
_journey_delay_lock = threading.Lock()
_delay_map_ts: float = 0.0
_delay_map_version: int = 0
# Update interval in seconds (default 180s == 3min)
_DELAY_UPDATE_INTERVAL = int(os.environ.get('DELAY_UPDATE_INTERVAL', '180'))


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

    for line_ref, dest, lat_v, lon_v, _op, feed_delay, origin_dep in buses:
        try:
            # Ask the matching function for both delay and journey id
            matched = _compute_delay_from_timetable(line_ref, dest, lat_v, lon_v, return_jid=True, origin_dep_secs=origin_dep)
            if not matched:
                continue
            computed_delay, j_id = matched
            if j_id is None:
                continue
            final_delay = feed_delay if feed_delay is not None else computed_delay
            if final_delay is None:
                continue
            # Store integer seconds
            temp_map[int(j_id)] = int(final_delay)
        except Exception:
            continue

    with _journey_delay_lock:
        _journey_delay_map = temp_map
        _delay_map_ts = time.time()
        _delay_map_version += 1


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
    with _router_cache_lock:
        if cache_key in _router_cache:
            return _router_cache[cache_key]
        if _base_cache is None:
            from main import initialize_base
            _base_cache = initialize_base()
            # Merge prebuilt cache from init
            if "prebuilt_cache" in _base_cache:
                _router_cache.update(_base_cache["prebuilt_cache"])
            # Check again after merging prebuilt
            if cache_key in _router_cache:
                return _router_cache[cache_key]
        loader = _base_cache["loader"]
        walking_raw = _base_cache["walking_raw"]
        al = _base_cache.get("atco_loader")
        from main import build_for_date
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
                _router_cache[cache_key] = (adj_merged, router, walking)
                return adj_merged, router, walking
            except Exception:
                # Fall back to unadjusted merged/router on any failure
                _router_cache[cache_key] = (merged, router, walking)
                return merged, router, walking

        _router_cache[cache_key] = (merged, router, walking)
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

    route_data = {k: v for k, v in route_result.items() if k != "_meta"}
    all_prevs = {info["prev_stop"] for info in route_data.values()
                 if info["prev_stop"] is not None}
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


def build_journey_plan_response(route_result, merged, stop_coords):
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

    if not route_result:
        return {
            "success": True,
            "legs": [],
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

        return {
            "success": True,
            "legs": legs,
            "meta": {
                "start_walk_seconds": meta.get("start_walk_seconds", 0),
                "end_walk_seconds": meta.get("end_walk_seconds", 0),
                "total_arrival": (seconds_to_time(int(total_arrival))
                                  if total_arrival else None),
            },
            "routeGeometries": geometries,
        }

    # --- Multi-stop transit route ---
    route_data = {k: v for k, v in route_result.items() if k != "_meta"}

    all_prevs = {info["prev_stop"] for info in route_data.values()
                 if info["prev_stop"] is not None}
    destinations = [s for s in route_data if s not in all_prevs]
    if not destinations:
        destinations = list(route_data.keys())

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

    # -- Start walking leg --
    if start_point and len(start_point) >= 2 and start_walk > 0 and ordered:
        first_int = ordered[0][0]
        first_coord = stop_coords.get(first_int)
        first_name = _display_name(first_int)
        to_loc = {"name": first_name}
        if first_coord:
            to_loc["lat"] = first_coord[0]
            to_loc["lon"] = first_coord[1]
        legs.append({
            "mode": "walking",
            "from_stop": {"name": "Start", "lat": start_point[0],
                          "lon": start_point[1]},
            "to_stop": to_loc,
            "duration_seconds": start_walk,
            "departure_time": None,
            "arrival_time": _time_str(ordered[0][1]["arrival_time"]),
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
        prev_name = _display_name(prev_int)
        curr_name = _display_name(curr_int)
        prev_coord = stop_coords.get(prev_int)
        curr_coord = stop_coords.get(curr_int)

        from_loc = {"name": prev_name}
        to_loc = {"name": curr_name}
        if prev_coord:
            from_loc["lat"] = prev_coord[0]
            from_loc["lon"] = prev_coord[1]
        if curr_coord:
            to_loc["lat"] = curr_coord[0]
            to_loc["lon"] = curr_coord[1]

        leg = {
            "mode": transport,
            "from_stop": from_loc,
            "to_stop": to_loc,
            "arrival_time": _time_str(curr_info["arrival_time"]),
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
        last_coord = stop_coords.get(last_int)
        last_name = _display_name(last_int)
        from_loc = {"name": last_name}
        if last_coord:
            from_loc["lat"] = last_coord[0]
            from_loc["lon"] = last_coord[1]
        legs.append({
            "mode": "walking",
            "from_stop": from_loc,
            "to_stop": {"name": "Destination",
                        "lat": destination_point[0],
                        "lon": destination_point[1]},
            "duration_seconds": end_walk,
            "departure_time": _time_str(
                ordered[-1][1]["arrival_time"]),
            "arrival_time": _time_str(total_arrival),
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

    # Compute the total real-time delay from all bus legs.
    # Because the router already used delay-adjusted journey_times,
    # total_arrival already includes the cumulative delay.
    #   scheduled total = total_arrival - total_delay
    #   realtime  total = total_arrival  (as-is)
    total_delay_s = 0
    has_any_delay = False
    for leg in legs:
        ds = leg.get("delay_seconds")
        if ds is not None and ds != 0:
            total_delay_s += ds
            has_any_delay = True

    scheduled_total = None
    rt_total_arrival = None
    if total_arrival is not None and has_any_delay:
        scheduled_total = _time_str(int(total_arrival - total_delay_s))
        rt_total_arrival = _time_str(int(total_arrival))
    elif total_arrival is not None:
        scheduled_total = _time_str(int(total_arrival))

    return {
        "success": True,
        "legs": legs,
        "meta": {
            "start_walk_seconds": start_walk,
            "end_walk_seconds": end_walk,
            "total_arrival": scheduled_total,
            "realtime_total_arrival": rt_total_arrival,
            "total_delay_seconds": total_delay_s if has_any_delay else None,
            "start_point": list(start_point) if start_point else None,
            "destination": (list(destination_point)
                            if destination_point else None),
        },
        "routeGeometries": geometries,
    }


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

        merged, router, walking = get_router_for_date(
            date_str, start_time=start_seconds)
        result = router.route(
            n_transfer_limit=max_transfers,
            walking=walking,
            start_time=start_seconds,
            start_point=start_point,
            destination=destination,
            allowed_modes=allowed_modes,
        )
        stop_coords = getattr(walking, "_coords", {})
        return build_journey_plan_response(
            result, merged, stop_coords)
    except Exception as exc:
        return {"success": False, "error": str(exc),
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

        # Build / fetch merged data + main router + walking helper
        merged, main_router, walking = get_router_for_date(
            date_str, start_time=start_seconds)

        # Lazy construct ECO router around the same merged data
        try:
            from eco_router import RaptorRouter as EcoRaptor
            eco_router = EcoRaptor(merged)
        except Exception:
            eco_router = None

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

        # Run both routers in parallel threads (Eco optional)
        tasks = [asyncio.to_thread(_run_router, main_router)]
        if eco_router is not None:
            tasks.append(asyncio.to_thread(_run_router, eco_router))

        results = await asyncio.gather(*tasks)

        main_res, main_time = results[0]
        eco_res = None
        eco_time = None
        if len(results) > 1:
            eco_res, eco_time = results[1]

        stop_coords = getattr(walking, "_coords", {})

        return {
            "success": True,
            "main": {
                "route": _sanitize_route(main_res),
                "route_text": format_route_text(main_res, merged),
                "time_seconds": main_time,
            },
            "eco": {
                "route": _sanitize_route(eco_res) if eco_res is not None else None,
                "route_text": format_route_text(eco_res, merged) if eco_res is not None else None,
                "time_seconds": eco_time,
            },
        }
    except Exception as exc:
        return {"success": False, "error": str(exc), "main": None, "eco": None}

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
        merged, router, walking = get_router_for_date(
            date_str, start_time=start_seconds)
        result = router.route(
            n_transfer_limit=max_transfers,
            walking=walking,
            start_time=start_seconds,
            start_point=start_point,
            destination=destination,
            allowed_modes=allowed_modes,
        )
        stop_coords = getattr(walking, "_coords", {})
        return build_journey_plan_response(result, merged, stop_coords)
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

        merged, router, walking = get_router_for_date(
            date_str, start_time=start_seconds)
        result = router.route(
            n_transfer_limit=max_transfers,
            walking=walking,
            start_time=start_seconds,
            start_point=start_point,
            destination=destination,
            allowed_modes=allowed_modes,
        )
        route_text = format_route_text(result, merged)
        return {"success": True, "route": _sanitize_route(result), "route_text": route_text}
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

    for child in tree:
        _, _, tag = child.tag.rpartition("}")
        match tag:
            case "locationName":
                location_name = child.text
            
            case "trainServices":
                services = parse_train_services(child)

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



