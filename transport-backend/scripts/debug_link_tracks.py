"""Backend-only link-track inspector.

Given a route_id (string), this prints:
- whether BusLoader returns fragment tracks from DB
- sample (from_atco,to_atco) keys
- counts / coordinate lengths

This helps answer: "are route_link_tracks present in DB for this route_id?"

Usage:
  python3 transport-backend/scripts/debug_link_tracks.py --route-id "..."

Tip: set BUS_DB_DSN/TRAIN_DB_DSN/WALK_DB_DSN if not using defaults.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _ensure_backend_on_path() -> None:
    """Allow running this script from the repo root.

    When executed as:
      python3 transport-backend/scripts/debug_link_tracks.py ...
    Python's sys.path[0] becomes transport-backend/scripts, so sibling modules
    like `bus_loader` won't be importable unless we add transport-backend.
    """

    here = Path(__file__).resolve()
    backend_dir = here.parents[1]  # transport-backend/
    if (backend_dir / "bus_loader.py").exists() and str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--route-id", required=True)
    ap.add_argument("--limit", type=int, default=10)
    args = ap.parse_args()

    _ensure_backend_on_path()

    from bus_loader import BusLoader
    from main import BUS_DB_PATH, WALK_DB_PATH

    loader = BusLoader(BUS_DB_PATH, walking_db_path=WALK_DB_PATH)

    rid = args.route_id
    raw = loader.get_route_link_tracks_for_route(rid)

    if not raw:
        print(json.dumps({"route_id": rid, "raw": None, "raw_len": 0}, indent=2))
        return 2

    items = list(raw.items())
    out = {
        "route_id": rid,
        "raw_len": len(items),
        "sample": [],
    }

    for (fa, ta), pts in items[: max(0, args.limit)]:
        out["sample"].append({
            "from_atco": fa,
            "to_atco": ta,
            "pts_len": len(pts) if pts else 0,
            "first": (list(pts[0]) if pts else None),
            "last": (list(pts[-1]) if pts else None),
        })

    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
