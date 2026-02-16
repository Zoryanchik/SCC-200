"""Transport Backend — FastAPI server.

Exposes transport functionality (health check, live buses, journey
planning) as a JSON API consumed by the frontend.
"""

from contextlib import asynccontextmanager
import logging
import os
import sys
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from bus_live import BusLive
from main import build_for_date
from time_utils import seconds_since_midnight

logger = logging.getLogger(__name__)

# Add the current directory to the path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

class BusLiveRequest(BaseModel):
    lat: float
    lon: float
    lat_tol: float = 0.0003
    lon_tol: float = 0.0003

class BusLiveResponse(BaseModel):
    success: bool
    buses: Optional[List[Dict[str, Any]]] = None
    error: Optional[str] = None


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

# ── Lifespan (startup / shutdown) ─────────────────────────────────────────


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
            "Backend initialisation failed — endpoints requiring "
            "transport data will be unavailable: %s", exc,
        )
    yield  # ← server is running
    # Shutdown logic (if needed) goes here


app = FastAPI(title="Transport API", lifespan=lifespan)

# ── Health check ──────────────────────────────────────────────────────────


@app.get("/health")
async def health():
    """Liveness probe. Returns ``{"status": "ok"}`` when the server is up."""
    return {"status": "ok"}


# ── Static files & frontend ──────────────────────────────────────────────

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

@app.post("/api/bus_live", response_model=BusLiveResponse)
async def get_live_buses(request: BusLiveRequest):
    """Get live bus data near a location."""
    try:
        bl = BusLive(timeout=10)  # 10 second timeout for live data
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
    from time_utils import seconds_to_time
    if not route_result:
        return "\n  No route found."
    # If route_result only contains '_meta', treat as only walking
    if set(route_result.keys()) == {'_meta'}:
        meta = route_result['_meta']
        start_point = meta.get('start_point', ())
        destination = meta.get('destination', ())
        start_walk = meta.get('start_walk_seconds', 0)
        end_walk = meta.get('end_walk_seconds', 0)
        total_arrival = meta.get('total_arrival')
        out = []
        out.append(f"\n{'='*60}")
        out.append(f"  Route found — only walking")
        out.append(f"{'='*60}")
        if start_point:
            out.append(f"\n  ◎ Start  ({start_point[0]:.5f}, {start_point[1]:.5f})")
        walk_min = (start_walk + end_walk) / 60
        out.append(f"    │  🚶 Walk {walk_min:.0f} min ({start_walk + end_walk}s)")
        if destination:
            out.append(f"  ◎ Destination  ({destination[0]:.5f}, {destination[1]:.5f})")
        if total_arrival is not None:
            from time_utils import seconds_to_time
            out.append(f"    Arrive at {seconds_to_time(int(total_arrival))}")
        out.append(f"\n{'='*60}")
        return "\n".join(out)
    meta = route_result.pop('_meta', {})
    start_walk = meta.get('start_walk_seconds', 0)
    end_walk   = meta.get('end_walk_seconds', 0)
    total_arrival = meta.get('total_arrival')
    start_point = meta.get('start_point', ())
    destination = meta.get('destination', ())
    all_prevs = {info["prev_stop"] for info in route_result.values() if info["prev_stop"] is not None}
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
    out.append(f"\n{'='*60}")
    out.append(f"  Route found — {len(legs)} stop(s)")
    out.append(f"{'='*60}")
    if start_point and legs:
        first_arrival = legs[0][1]["arrival_time"]
        depart_time = first_arrival - start_walk
        out.append(f"\n  ◎ Start  ({start_point[0]:.5f}, {start_point[1]:.5f})")
        out.append(f"    Depart at {seconds_to_time(int(depart_time))}")
        walk_min = start_walk / 60
        out.append(f"    │  🚶 Walk {walk_min:.0f} min ({start_walk}s)")
    for i, (stop_int, info) in enumerate(legs):
        stop_label = merged.stop_metadata[stop_int] if stop_int < len(merged.stop_metadata) else f"stop#{stop_int}"
        arrival = seconds_to_time(int(info["arrival_time"])) if info["arrival_time"] != float("inf") else "--:--:--"
        transport = info["type"] if info["type"] else "origin"
        if i == 0:
            out.append(f"  ● {stop_label}")
            out.append(f"    Arrive at {arrival}")
        else:
            if transport == "walking":
                prev_stop_int, prev_info = legs[i - 1]
                walk_secs = info["arrival_time"] - prev_info["arrival_time"]
                walk_min = walk_secs / 60
                out.append(f"    │  🚶 Walk {walk_min:.0f} min ({int(walk_secs)}s)")
            else:
                jinfo = info.get("journey_info")
                line_name = jinfo.get("line_name", "") if jinfo else ""
                if line_name and ":" in line_name:
                    line_name = line_name.split(":")[-1]
                j_origin = info.get("journey_origin", "")
                j_dest   = info.get("journey_destination", "")
                board_dep = info.get("board_departure")
                desc_parts = []
                if transport:
                    desc_parts.append(transport)
                if line_name:
                    desc_parts.append(f"line {line_name}")
                if j_origin and j_dest:
                    desc_parts.append(f"{j_origin} → {j_dest}")
                if board_dep is not None:
                    desc_parts.append(f"departs {seconds_to_time(int(board_dep))}")
                desc = " · ".join(desc_parts) if desc_parts else transport
                out.append(f"    │  {desc}")
            out.append(f"  ● {stop_label}")
            out.append(f"    Arrive at {arrival}")
    if destination and legs:
        walk_min = end_walk / 60
        out.append(f"    │  🚶 Walk {walk_min:.0f} min ({end_walk}s)")
        out.append(f"  ◎ Destination  ({destination[0]:.5f}, {destination[1]:.5f})")
        if total_arrival is not None:
            out.append(f"    Arrive at {seconds_to_time(int(total_arrival))}")
    out.append(f"\n{'='*60}")
    return "\n".join(out)

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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="localhost", port=8000)
