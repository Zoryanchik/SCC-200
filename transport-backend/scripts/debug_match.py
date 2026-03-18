#!/usr/bin/env python3
"""Debug a single live feed tuple against the timetable matcher.

Usage: PYTHONPATH=. python3 scripts/debug_match.py --line 42 --lat 54.05 --lon -2.8 --origin_dep 24600 --operator SCCU

Prints diagnostics from both the strict matcher and the lightweight
diagnosis helper to explain why a vehicle was or was not matched.
"""
import argparse
import json
from pprint import pprint


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--line", required=True)
    p.add_argument("--lat", type=float, required=True)
    p.add_argument("--lon", type=float, required=True)
    p.add_argument("--origin_dep", type=int, default=None)
    p.add_argument("--operator", type=str, default=None)
    p.add_argument("--dest", type=str, default=None)
    args = p.parse_args()

    # Import heavy functions from the API module
    try:
        from api import _compute_delay_from_timetable, _diagnose_match_failure, get_router_for_date
    except Exception as e:
        print("Failed to import API helpers:", e)
        raise

    print("Running matcher (return_jid=True) ...")
    try:
        res = _compute_delay_from_timetable(args.line, args.dest or "", args.lat, args.lon,
                                            return_jid=True,
                                            origin_dep_secs=args.origin_dep,
                                            operator_ref=args.operator,
                                            strict_tol=600,
                                            feed_origin_atco=None,
                                            feed_destination_atco=None)
        print("_compute_delay_from_timetable ->", res)
    except Exception as e:
        print("Error running _compute_delay_from_timetable:", e)

    print("\nRunning _diagnose_match_failure ...")
    try:
        reasons = _diagnose_match_failure(args.line, args.dest or "", args.lat, args.lon,
                                          origin_dep_secs=args.origin_dep,
                                          operator_ref=args.operator,
                                          strict_tol=600,
                                          feed_origin_atco=None,
                                          feed_destination_atco=None)
        print("reject_reasons:")
        pprint(reasons)
    except Exception as e:
        print("Error running _diagnose_match_failure:", e)

    # Extra introspection: list candidate journeys for this line and why each
    print("\nIntrospecting candidate journeys for this line in today's merged data...")
    try:
        from datetime import datetime
        today = datetime.now().date().isoformat()
        now = datetime.now()
        now_secs = now.hour * 3600 + now.minute * 60 + now.second
        merged, router, walking = get_router_for_date(today, start_time=now_secs, apply_delay=False)
    except Exception as e:
        print("Failed to load merged/router/walking:", e)
        return

    simple_line = str(args.line).strip()
    candidates = []
    for j_id, jmeta in enumerate(merged.journey_metadata):
        if not jmeta:
            continue
        ln = (jmeta.get('line_name') or "").split(":")[-1].strip()
        if ln != simple_line:
            continue
        candidates.append(j_id)

    print(f"Found {len(candidates)} journeys with line {simple_line}")
    detailed = []
    for j_id in candidates:
        info = {"j_id": j_id}
        try:
            jt = merged.journey_times[j_id]
            info['start_dep'] = jt[0][2]
            info['end_arr'] = jt[-1][1] if jt[-1][1] is not None else jt[-1][2]
            # operator
            info['operator_noc'] = merged.journey_metadata[j_id].get('operator_national_code')
            # destination atco
            try:
                dest_idx = jt[-1][0]
                info['dest_atco'] = merged.get_atco_code(dest_idx)
            except Exception:
                info['dest_atco'] = None
            # route idx
            route_idx = merged.journey_to_route[j_id] if j_id < len(merged.journey_to_route) else None
            info['route_idx'] = route_idx
            # Full-route polylines were removed; fragment geometry is stored as per-route link tracks.
            # We can only provide a coarse “has geometry fragments” signal here.
            try:
                info['has_link_tracks'] = bool(
                    getattr(merged, 'route_link_tracks', None)
                    and route_idx is not None
                    and merged.route_link_tracks.get(route_idx)
                )
            except Exception:
                info['has_link_tracks'] = False
            # walking proximity
            try:
                nearby = walking.reachable_stops((args.lat, args.lon))
                info['nearby_len'] = len(nearby)
                if nearby:
                    info['nearest_stop_atco'] = merged.get_atco_code(nearby[0][0])
            except Exception:
                info['nearby_len'] = None
        except Exception as e:
            info['error'] = str(e)
        detailed.append(info)

    print(json.dumps(detailed, indent=2))


if __name__ == '__main__':
    main()
