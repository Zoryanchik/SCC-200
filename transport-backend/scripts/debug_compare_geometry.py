"""Backend-only journey geometry debugger.

This script lets you reproduce /journey/compare geometry behavior without the
frontend, and prints link-fragment diagnostics.

It starts by using FastAPI's TestClient against the in-process `api.app`.
That means:
- no Uvicorn needed
- it uses your current code + local DB DSNs/env vars

Typical use:
  ROUTE_GEOM_TRACE=1 python3 transport-backend/scripts/debug_compare_geometry.py \
    --date 2026-03-23 --time 17:48 \
    --from-lat 54.00549 --from-lon -2.78745 \
    --to-lat 54.04755 --to-lon -2.80102

If you want to narrow output, use --only-bus.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


def _ensure_backend_on_path() -> None:
        """Allow running this script from the repo root.

        When executed as:
            python3 transport-backend/scripts/debug_compare_geometry.py ...
        Python's sys.path[0] becomes transport-backend/scripts, so sibling modules
        like `api` won't be importable unless we add transport-backend.
        """

        here = Path(__file__).resolve()
        backend_dir = here.parents[1]  # transport-backend/
        if (backend_dir / "api.py").exists() and str(backend_dir) not in sys.path:
                sys.path.insert(0, str(backend_dir))


def _pp(obj: Any) -> str:
    return json.dumps(obj, indent=2, sort_keys=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="YYYY-MM-DD")
    ap.add_argument("--time", required=True, help="HH:MM (24h)")
    ap.add_argument("--from-lat", type=float, required=True)
    ap.add_argument("--from-lon", type=float, required=True)
    ap.add_argument("--to-lat", type=float, required=True)
    ap.add_argument("--to-lon", type=float, required=True)
    ap.add_argument("--mode", default="both", choices=["both", "bus", "train"], help="router mode")
    ap.add_argument("--max-transfers", type=int, default=2)
    ap.add_argument("--only-bus", action="store_true", help="print only bus legs + their geometry")
    ap.add_argument("--router", default="main", help="which compare router result to inspect (main/eco/lazy/greedy)")
    args = ap.parse_args()

    if os.environ.get("ROUTE_GEOM_TRACE") != "1":
        print("NOTE: set ROUTE_GEOM_TRACE=1 for detailed fragment-load logs")

    _ensure_backend_on_path()

    import api  # local module import
    from fastapi.testclient import TestClient

    client = TestClient(api.app, raise_server_exceptions=True)

    payload = {
        "fromStop": {"lat": args.from_lat, "lon": args.from_lon},
        "toStop": {"lat": args.to_lat, "lon": args.to_lon},
        "departureTime": args.time,
        "date": args.date,
        "maxTransfers": args.max_transfers,
        "mode": ("bus" if args.mode == "bus" else "train" if args.mode == "train" else "both"),
        "includeGeometry": True,
    }

    resp = client.post("/journey/compare", json=payload)
    print(f"HTTP {resp.status_code}")

    # Print what the backend believes the active router context is.
    # get_router_for_date() stamps these into merged.meta.
    try:
        merged_meta = getattr(getattr(api, "_base_cache", None), "meta", None)
    except Exception:
        merged_meta = None
    try:
        # (best-effort) print recent cache keys; useful when comparing date/bucket.
        cache_keys = sorted(list(getattr(api, "_router_cache", {}).keys()))
    except Exception:
        cache_keys = None
    if os.environ.get("ROUTE_GEOM_TRACE") == "1":
        if isinstance(merged_meta, dict):
            print("[script] base_cache.meta:", _pp({k: merged_meta.get(k) for k in ("date", "bucket") if k in merged_meta}))
        if cache_keys is not None:
            # avoid huge output; show the last few keys only
            print("[script] router_cache.keys.tail:", _pp(cache_keys[-8:]))

    data = resp.json()
    if not data.get("success"):
        print(_pp(data))
        return 1

    router_key = args.router
    route = (data.get(router_key) or {}).get("route")
    if not route:
        print(f"No route under key '{router_key}'. Available: {list(data.keys())}")
        print(_pp(data.get("available_routers")))
        return 1

    legs = route.get("legs") or []
    geoms = route.get("routeGeometries") or []

    # The backend has historically returned either:
    #  1) a list aligned by leg index (geoms[i] corresponds to legs[i])
    #  2) a list of dicts with an explicit `leg_index`
    # Handle both so this script stays useful across versions.
    geom_by_leg: dict[int, Any] = {}
    if isinstance(geoms, list):
        aligned = True
        for obj in geoms:
            if not (obj is None or isinstance(obj, dict)):
                aligned = False
                break
            if isinstance(obj, dict) and "leg_index" in obj:
                aligned = False
                break
        if aligned:
            for i, g in enumerate(geoms):
                geom_by_leg[i] = g
        else:
            for g in geoms:
                if isinstance(g, dict) and "leg_index" in g:
                    geom_by_leg[int(g["leg_index"])] = g

    print(f"legs={len(legs)} geoms={len(geoms)}")

    for i, leg in enumerate(legs):
        if not isinstance(leg, dict):
            continue
        mode = leg.get("mode") or leg.get("transport")
        if args.only_bus and mode != "bus":
            continue

        g = geom_by_leg.get(i)
        coords_len = len((g or {}).get("coords") or []) if isinstance(g, dict) else 0
        print("-" * 60)
        print(f"leg[{i}] mode={mode} line={leg.get('line_name')} route_id={leg.get('route_id')} route_int={leg.get('route_int')} coords_len={coords_len}")
        if g:
            print(f"  geom.source={g.get('source')} geom.note={g.get('note')}")
            if coords_len:
                coords = g.get("coords")
                print(f"  first={coords[0]} last={coords[-1]}")
        else:
            print("  geom: <missing>")

    # Optional: print full JSON at end (kept off by default)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
