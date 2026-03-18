#!/usr/bin/env python3
"""Count *matched* journeys in Lancaster and whether we can resolve usable tracks.

Definitions
- "matched" record: a /bus/live/all entry that has `logged_journey_id`.
  (Backend contract work: matched => must have logged_journey_id.)

- "usable track": a geometry response with `coords` length >= 2 AND `source != "linear"`.

How it works
1) Fetch /bus/live/all for Lancaster bbox.
2) For each matched record, determine a leading route_id candidate:
   - prefer `logged_journey_id`
   - else try `journey_id` (if present; some payloads use this field)
3) For each candidate, request /route/leg-geometry?route_int=... (preferred) and classify response.
    Fall back to route_id only if route_int is unavailable.

This mirrors what the frontend uses (it queries /route/leg-geometry for each route_id).
This mirrors what the frontend should use (it prefers /route/leg-geometry with route_int).

Usage
- Start backend locally (port 5050 as in existing scripts)
- Run:
    python3 scripts/count_lancaster_matched_tracks.py

Options
- --url lets you override the /bus/live/all URL.
- --backend lets you override backend base URL.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from collections import Counter

DEFAULT_BACKEND = "http://localhost:5050"
LANCASTER_LIVE_ALL = (
    "http://localhost:5050/bus/live/all?lat=54.0466&lon=-2.8007&latTol=0.25&lonTol=0.25"
)


def _parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--backend", default=DEFAULT_BACKEND, help="Backend base URL (default: localhost:5050)")
    p.add_argument(
        "--url",
        default=LANCASTER_LIVE_ALL,
        help="Full URL for /bus/live/all (default: Lancaster bbox)",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="HTTP timeout seconds (default: 30)",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=0,
        help="If set, only inspect first N matched records.",
    )
    return p.parse_args()


def _fetch_json(url: str, timeout: float):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.load(resp)


def _debug_route_link_tracks_url(backend: str, route_id: str) -> str:
    return f"{backend.rstrip('/')}/debug/route-link-tracks/{urllib.parse.quote(route_id, safe='')}"


def _leg_geometry_url(backend: str, *, route_int: str | None = None, route_id: str | None = None) -> str:
    params: dict[str, str] = {}
    if route_int is not None and str(route_int) != "":
        params["route_int"] = str(route_int)
    elif route_id is not None and str(route_id) != "":
        params["route_id"] = str(route_id)
    q = urllib.parse.urlencode(params)
    return f"{backend.rstrip('/')}/route/leg-geometry?{q}"


def _leg_geometry_url_with_coords(
    backend: str,
    *,
    route_int: str | None = None,
    route_id: str | None = None,
    from_lat: float,
    from_lon: float,
    to_lat: float,
    to_lon: float,
) -> str:
    # /route/leg-geometry requires from/to coordinates even when route_id is present.
    q = urllib.parse.urlencode(
        {
            "route_id": route_id,
            "from_lat": from_lat,
            "from_lon": from_lon,
            "to_lat": to_lat,
            "to_lon": to_lon,
        }
    )
    return f"{backend.rstrip('/')}/route/leg-geometry?{q}"


def _classify_leg_geometry(payload) -> str:
    """Return one of: usable_route_link_tracks, usable_osrm, linear, empty, error, unknown."""
    if not isinstance(payload, dict):
        return "unknown"
    if payload.get("error"):
        return "error"

    coords = payload.get("coords")
    src = payload.get("source")

    if not isinstance(coords, list):
        return "unknown"

    if len(coords) < 2:
        return "empty"

    if src == "linear":
        return "linear"
    if src == "route_link_tracks":
        return "usable_route_link_tracks"
    if src == "osrm":
        return "usable_osrm"
    if src:
        return f"usable_{src}"
    return "usable_unknown_source"


def main() -> int:
    args = _parse_args()

    try:
        live = _fetch_json(args.url, timeout=args.timeout)
    except Exception as e:
        print(f"ERROR fetching {args.url}: {e}", file=sys.stderr)
        return 2

    total = len(live) if isinstance(live, list) else 0
    if not isinstance(live, list):
        print("ERROR: /bus/live/all did not return a JSON array", file=sys.stderr)
        return 2

    matched = []
    for rec in live:
        if isinstance(rec, dict) and rec.get("logged_journey_id"):
            matched.append(rec)

    if args.limit and args.limit > 0:
        matched = matched[: args.limit]

    track_counts = Counter()
    linear_diag = Counter()
    failure_examples = []
    linear_examples = []

    for rec in matched:
        candidates = []
        ljid = rec.get("logged_journey_id")
        jid = rec.get("journey_id")
        if ljid:
            candidates.append(ljid)
        if jid and jid not in candidates:
            candidates.append(jid)

        if not candidates:
            track_counts["no_route_id_candidate"] += 1
            continue

        # We need some coordinates for /route/leg-geometry.
        # Live payloads vary; try common field names.
        lat = rec.get("lat")
        lon = rec.get("lon")
        if lat is None or lon is None:
            lat = rec.get("latitude")
            lon = rec.get("longitude")

        if lat is None or lon is None:
            track_counts["missing_vehicle_coords"] += 1
            continue

        try:
            lat = float(lat)
            lon = float(lon)
        except Exception:
            track_counts["bad_vehicle_coords"] += 1
            continue

        # Emulate frontend: it tries multiple route_ids; we just try the first usable candidate.
        classified = None
        for rid in candidates:
            # We don't know the leg endpoints here; we just pass the vehicle position
            # for both endpoints so the server can still return any best-effort geometry.
            # This script is primarily measuring whether we can resolve *non-linear*
            # geometry (fragment-based `route_link_tracks`) vs OSRM/linear fallback.
            url = _leg_geometry_url_with_coords(
                args.backend,
                rid,
                from_lat=lat,
                from_lon=lon,
                to_lat=lat,
                to_lon=lon,
            )
            try:
                payload = _fetch_json(url, timeout=args.timeout)
            except Exception as e:
                classified = "http_error"
                failure_examples.append({"route_id": rid, "error": str(e)})
                continue

            classified = _classify_leg_geometry(payload)
            # stop at first usable geometry
            if classified.startswith("usable_"):
                break
            # if it was empty/linear/error, try next candidate (if any)

        if classified is None:
            classified = "unknown"
        track_counts[classified] += 1

        if classified == 'linear':
            # Try to explain WHY: are fragment tracks missing for this route_id?
            # We keep this best-effort and small (no huge payloads).
            rid0 = candidates[0]
            try:
                dbg = _fetch_json(_debug_route_link_tracks_url(args.backend, rid0), timeout=args.timeout)
                # Endpoint returns a summary; keep this loose to avoid coupling.
                link_tracks_count = dbg.get('link_tracks_count')
                has_any = (isinstance(link_tracks_count, int) and link_tracks_count > 0)
                if has_any:
                    linear_diag['debug_found_link_tracks_but_leg_geometry_linear'] += 1
                else:
                    linear_diag['no_route_link_tracks'] += 1

                if len(linear_examples) < 10:
                    linear_examples.append({
                        'route_id': rid0,
                        'link_tracks_count': link_tracks_count,
                    })
            except Exception:
                linear_diag['debug_endpoint_error'] += 1

    print("lancaster_live_total_records=", total)
    print("lancaster_matched_records=", len(matched))
    print("matched_track_resolution_counts=", dict(track_counts))

    usable = sum(v for k, v in track_counts.items() if k.startswith("usable_"))
    print("matched_with_usable_track=", usable)
    print("matched_without_usable_track=", len(matched) - usable)

    if track_counts.get('linear'):
        print("\nlinear_diagnostics_counts=", dict(linear_diag))
        if linear_examples:
            print("\nlinear_examples (up to 10):")
            for ex in linear_examples:
                print(" ", ex)

    if len(failure_examples) > 0:
        print("\nhttp_failure_examples (up to 5):")
        for ex in failure_examples[:5]:
            print(" ", ex)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
