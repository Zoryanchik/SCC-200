"""Transport Backend -€” FastAPI server.

Exposes transport functionality (health check, live buses, journey
planning) as a JSON API consumed by the frontend.
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

logger = logging.getLogger(__name__)

# Add the current directory to the path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))


#Remove later if useless
"""class BusLiveRequest(BaseModel):
    lat: float
    lon: float
    lat_tol: float = 0.0003
    lon_tol: float = 0.0003

class BusLiveResponse(BaseModel):
    success: bool
    buses: Optional[List[Dict[str, Any]]] = None
    error: Optional[str] = None"""


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

# -”€-”€ Lifespan (startup / shutdown) -”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€


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
            "Backend initialisation failed -€” endpoints requiring "
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

# -”€-”€ Health check -”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€


@app.get("/health")
async def health():
    """Liveness probe. Returns ``{"status": "ok"}`` when the server is up."""
    return {"status": "ok"}


# -- Stop search ---------------------------------------------------------------



def geocode_locations(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    if not query or limit <= 0:
        return []
    params = urlencode({"format": "json", "q": query, "limit": str(limit), "addressdetails": "0"})
    url = f"https://nominatim.openstreetmap.org/search?{params}"
    request = UrllibRequest(url, headers={"User-Agent": "transport-backend/1.0"})
    with urlopen(request, timeout=5) as response:
        payload = json.loads(response.read().decode("utf-8"))
    results = []
    for idx, item in enumerate(payload):
        try:
            lat = float(item.get("lat"))
            lon = float(item.get("lon"))
        except (TypeError, ValueError):
            continue
        name = item.get("display_name") or item.get("name") or query
        results.append({
            "id": f"loc:{idx}",
            "name": name,
            "lat": lat,
            "lon": lon,
            "atco_code": None,
            "type": "location",
        })
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
        loader = _base_cache["loader"]
        stop_results = loader.search_stops(q, limit if not classification else limit * 3)
        for stop in stop_results:
            stop["type"] = "stop"
        
        # Apply classification filter when requested
        if classification:
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

        remaining = max(0, limit - len(stop_results))
        location_results = []
        if remaining > 0:
            try:
                location_results = geocode_locations(q, remaining)
            except Exception as exc:
                logger.warning("Geocoding lookup failed: %s", exc)
        return stop_results + location_results
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
        timetable, _router, _walking = get_router_for_date(date_str)
    except Exception:
        return {}

    merged = timetable.today
    idx_lookup = classify_to_lookup(merged)

    # Convert stop-int → ATCO code so we can match search results
    atco_lookup: Dict[str, str] = {}
    bus_mapper = getattr(merged.bus_data, "map_stops", None)
    train_mapper = getattr(merged.train_data, "map_stops", None)
    bus_stop_count = len(merged.bus_data.stop_to_routes)

    for stop_int, cls in idx_lookup.items():
        code = None
        if stop_int < bus_stop_count and bus_mapper:
            try:
                code = bus_mapper.get_code(stop_int)
            except Exception:
                pass
        elif train_mapper:
            try:
                code = train_mapper.get_code(stop_int - bus_stop_count)
            except Exception:
                pass
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
        timetable, _router, _walking = get_router_for_date(date_str)
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"error": "Backend not initialized"},
        )

    merged = timetable.today
    results = classify_all(merged)

    if classification:
        results = [r for r in results if r["classification"] == classification]

    return results


# -”€-”€ Static files & frontend -”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€-”€

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


#Remove later if useless
"""
@app.post("/api/bus_live", response_model=BusLiveResponse)
async def get_live_buses(request: BusLiveRequest):
    #Get live bus data near a location.
    try:
        from bus_live import BusLive as BusLiveClass
        bl = BusLiveClass(timeout=10)  # 10 second timeout for live data
        results = bl.get_bus_live(request.lat, request.lon, lat_tol=request.lat_tol, lon_tol=request.lon_tol)

        # Convert tuples to dictionaries for JSON response
        buses = []
        for line_ref, dest, lat, lon, operator in results:
            buses.append({
                "line_ref": line_ref,
                "destination": dest,
                "latitude": lat,
                "longitude": lon,
                "operator": operator
            })

        return BusLiveResponse(success=True, buses=buses)

    except Exception as e:
        return BusLiveResponse(success=False, error=str(e))
"""


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
        }
        for line_ref, dest, lat_v, lon_v, _operator in results
    ]

# Global cache for router/timetable/walking by date
_router_cache = {}
_router_cache_lock = threading.Lock()
_base_cache = None

def get_router_for_date(date_str):
    global _base_cache
    with _router_cache_lock:
        if date_str in _router_cache:
            return _router_cache[date_str]
        if _base_cache is None:
            from main import initialize_base
            _base_cache = initialize_base()
        loader = _base_cache["loader"]
        walking_raw = _base_cache["walking_raw"]
        from main import build_for_date
        timetable, router, walking = build_for_date(loader, walking_raw, date_str)
        _router_cache[date_str] = (timetable, router, walking)
        return timetable, router, walking

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

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
    meta = route_result.pop("_meta", {})
    start_walk = meta.get("start_walk_seconds", 0)
    end_walk = meta.get("end_walk_seconds", 0)
    total_arrival = meta.get("total_arrival")
    start_point = meta.get("start_point", ())
    destination = meta.get("destination", ())

    all_prevs = {info["prev_stop"] for info in route_result.values()
                 if info["prev_stop"] is not None}
    destinations = [s for s in route_result if s not in all_prevs]
    if not destinations:
        destinations = list(route_result.keys())

    stop = destinations[0]
    visited = set()
    legs = []
    while stop is not None and stop not in visited:
        visited.add(stop)
        legs.append((stop, route_result[stop]))
        stop = route_result[stop]["prev_stop"]
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
        transport = info.get("type") or "origin"

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
        merged: MergedData instance (timetable.today)
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
            "type": "walking",
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
            "type": "walking",
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
        transport = curr_info.get("type", "") or "unknown"
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
            "type": transport,
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
            "type": "walking",
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

        timetable, router, walking = get_router_for_date(date_str)
        result = router.route(
            n_transfer_limit=max_transfers,
            walking=walking,
            start_date=date_str,
            start_time=start_seconds,
            start_point=start_point,
            destination=destination,
            allowed_modes=allowed_modes,
        )
        stop_coords = getattr(walking, "_coords", {})
        return build_journey_plan_response(
            result, timetable.today, stop_coords)
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
        timetable, router, walking = get_router_for_date(date_str)
        # Run routing
        result = router.route(
            n_transfer_limit=max_transfers,
            walking=walking,
            start_date=date_str,
            start_time=start_seconds,
            start_point=start_point,
            destination=destination,
            allowed_modes=allowed_modes,
        )
        route_text = format_route_text(result, timetable.today)
        return {"success": True, "route": result, "route_text": route_text}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/weather")
async def route_weather(lat: float | None = None, lon: float | None = None):
    if lat is None or lon is None:
        return JSONResponse(
            status_code=400,
            content={"error": "lat and lon are required"},
        )
    
    fetch_url = f"https://transport.scc.lancs.ac.uk/weather?lat={lat}&lon={lon}"

    import urllib

    try:
        with urllib.request.urlopen(fetch_url) as resp:
            data = json.load(resp)
    except Exception as exc:
        return JSONResponse(
            status_code=500,
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
    uvicorn.run(app, host="localhost", port=5005)







