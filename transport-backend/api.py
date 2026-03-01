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

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from bus_live import BusLive, get_bus_live
from main import build_for_date
from time_utils import seconds_since_midnight
from ws_server import broker as ws_broker, websocket_endpoint as ws_live_endpoint
from station_classifier import classify_all, classify_to_lookup
from modes import name_to_int, all_transit_modes

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
    yield  # — server is running
    # Shutdown: stop the live-updates poll loop
    try:
        await ws_broker.stop_polling()
    except Exception:  # pragma: no cover
        pass
    

app = FastAPI(title="Transport API", lifespan=lifespan)

# -- WebSocket/STOMP live updates endpoint --------------------------------
app.add_api_websocket_route("/ws/live", ws_live_endpoint)

# -- CORS ------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# — Health check ———————————————————————————————————————————


@app.get("/health")
async def health():
    """Liveness probe. Returns ``{"status": "ok"}`` when the server is up."""
    return {"status": "ok"}


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

    # Fuzzy-correct the query against known Lancashire places / POIs
    corrected = _fuzzy_correct_query(query)

    # When a county is provided, always append it to the query so
    # Nominatim returns geographically relevant results. This works
    # well for place names ("Lancaster Lancashire") and brands alike
    # ("Sainsbury Lancashire"). We ask Nominatim for extra results and
    # then do a lenient post-filter to trim any outliers.
    effective_query = corrected
    if county:
        # Only append if the user hasn't already included the county
        if county.lower() not in corrected.lower():
            effective_query = f"{corrected} {county}"

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
    # Use requests to simplify TLS handling in developer environments.
    # Disable verification here for developer convenience when system CA
    # bundles are missing. In production consider enabling verification.
    import requests
    headers = {"User-Agent": "transport-backend/1.0"}
    resp = requests.get(url, headers=headers, timeout=5, verify=False)
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

        # Default: return only geocoded locations (Lancashire)
        location_results = []
        try:
            location_results = geocode_locations(q, limit)
        except Exception as exc:
            logger.warning("Geocoding lookup failed: %s", exc)
        return location_results
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

    return [
        {
            "line": line_ref,
            "destination": dest,
            "lat": lat_v,
            "lon": lon_v,
            "operator": _operator,
        }
        for line_ref, dest, lat_v, lon_v, _operator in results
    ]

# Global cache for (merged, router, walking) by (date, AM/PM bucket)
_router_cache = {}
_router_cache_lock = threading.Lock()
_base_cache = None

def get_router_for_date(date_str, start_time=None):
    """Return (merged, router, walking) for a given date and time bucket.

    The cache key includes the AM/PM bucket so morning and afternoon
    queries use the correct two-day merge.
    """
    global _base_cache, _router_cache
    bucket = "AM" if (start_time is not None and start_time < 43200) else "PM"
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
        from main import build_for_date
        merged, router, walking = build_for_date(
            loader, walking_raw, date_str, start_time=start_time)
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

    for i, (stop_int, info) in enumerate(legs):
        stop_label = merged.stop_metadata[stop_int] if stop_int < len(merged.stop_metadata) else f"stop#{stop_int}"
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
                desc_parts = []
                if transport:
                    desc_parts.append(transport)
                if line_name:
                    desc_parts.append(f"line {line_name}")
                if j_origin and j_dest:
                    desc_parts.append(f"{j_origin} -> {j_dest}")
                if board_dep is not None:
                    desc_parts.append(f"departs {seconds_to_time(int(board_dep))}")
                desc = " - ".join(desc_parts) if desc_parts else transport
                out.append(f"    - {desc}")

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
        first_name = _stop_name(first_int)
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
        prev_name = _stop_name(prev_int)
        curr_name = _stop_name(curr_int)
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
            leg["departure_time"] = _time_str(board_dep)
            dur_start = board_dep if board_dep else prev_info["arrival_time"]
            if (curr_info["arrival_time"] < math.inf
                    and dur_start < math.inf):
                leg["duration_seconds"] = int(
                    curr_info["arrival_time"] - dur_start)
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
        last_name = _stop_name(last_int)
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

    return {
        "success": True,
        "legs": legs,
        "meta": {
            "start_walk_seconds": start_walk,
            "end_walk_seconds": end_walk,
            "total_arrival": _time_str(total_arrival),
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

@app.post("/api/route")
async def get_route(request: RouteRequest):
    try:
        # Prepare input
        date_str = request.date
        time_str = request.time
        start_point = (request.start_lat, request.start_lon)
        destination = (request.end_lat, request.end_lon)
        max_transfers = request.max_transfers
        allowed_modes = {request.mode} if request.mode in ("bus", "train") else {"bus", "train"}
        start_seconds = seconds_since_midnight(time_str)
        # Get router
        merged, router, walking = get_router_for_date(
            date_str, start_time=start_seconds)
        # Run routing
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
    except Exception as e:
        return {"success": False, "error": str(e)}


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



