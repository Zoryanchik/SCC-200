"""Investigate vehicles that fail with 'no_stop_progs'.

Runs a day+tz matching pass and collects detailed info for the first
N vehicles that hit the 'no_stop_progs' deeper failure so we can
inspect missing tracks/stop coordinate gaps.

Run from `transport-backend`:

    PYTHONPATH=. python3 scripts/investigate_no_stop_progs.py
"""
import json
import time
import os
from collections import defaultdict

from bus_live import BusLive
from api import get_router_for_date


def _hav(lat1, lon1, lat2, lon2):
    import math
    R = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _cum_distances(track):
    dists = [0.0]
    for i in range(1, len(track)):
        dists.append(dists[-1] + _hav(track[i - 1][0], track[i - 1][1], track[i][0], track[i][1]))
    return dists


def _project_onto_track(plat, plon, track, cum_dists):
    if not track:
        return (1e9, 0.0)
    total_len = cum_dists[-1] if cum_dists[-1] > 0 else 1.0
    best_dist = 1e9
    best_along = 0.0
    import math
    for i in range(len(track) - 1):
        ax, ay = track[i]
        bx, by = track[i + 1]
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
        cx = ax + t * (bx - ax)
        cy = ay + t * (by - ay)
        d = _hav(plat, plon, cx, cy)
        if d < best_dist:
            best_dist = d
            seg_len = cum_dists[i + 1] - cum_dists[i]
            best_along = cum_dists[i] + t * seg_len
    for i in range(len(track)):
        d = _hav(plat, plon, track[i][0], track[i][1])
        if d < best_dist:
            best_dist = d
            best_along = cum_dists[i]
    return (best_dist, best_along / total_len)


def main(limit=20, tol=30):
    bl = BusLive(timeout=10)
    records = bl.get_bus_live(54.0, -2.8, lat_tol=2.0, lon_tol=2.0)
    print(f"[investigate_no_stop_progs] fetched {len(records)} records")
    today = time.strftime('%Y-%m-%d')
    merged, router, walking = get_router_for_date(today, start_time=None, apply_delay=False)

    day_shifts = (0, 86400, -86400)
    tz_offsets = (0, 3600, -3600)

    found = []
    for rec in records:
        if len(found) >= limit:
            break
        # unpack
        meta = {}
        base = rec
        try:
            if isinstance(rec[-1], dict):
                meta = rec[-1]
                base = rec[:-1]
        except Exception:
            base = rec
        if len(base) == 7:
            line_ref, dest, lat_v, lon_v, _op, delay_s, origin_dep = base
        else:
            line_ref, dest, lat_v, lon_v, _op, delay_s, origin_dep, bearing = base

        # Build line candidates (same as other scripts)
        simple_line = (line_ref or '').split(':')[-1].strip()
        line_candidates = []
        for j_id, jm in enumerate(merged.journey_metadata):
            if not jm:
                continue
            ln = (jm.get('line_name') or '')
            if ln.split(':')[-1].strip() == simple_line:
                line_candidates.append((j_id, jm))
        if not line_candidates:
            continue

        # origin_atco/nearby check
        origin_atco = meta.get('origin_atco') if isinstance(meta, dict) else None
        origin_ok = False
        for j_id, jm in line_candidates:
            try:
                jt = merged.journey_times[j_id]
                first_stop_int = jt[0][0]
                journey_first_atco = merged.get_atco_code(first_stop_int)
            except Exception:
                continue
            if origin_atco:
                if journey_first_atco and str(journey_first_atco).strip() == str(origin_atco).strip():
                    origin_ok = True
                    break
            else:
                try:
                    nearby = walking.reachable_stops((lat_v, lon_v))
                    if not nearby:
                        continue
                    nearest_stop_int, walk_secs = nearby[0]
                    nearest_atco = merged.get_atco_code(nearest_stop_int)
                    if nearest_atco and journey_first_atco and nearest_atco == journey_first_atco:
                        origin_ok = True
                        break
                except Exception:
                    continue
        if not origin_ok:
            continue

        # origin_dep availability
        if origin_dep is None:
            continue
        try:
            od = int(origin_dep)
        except Exception:
            continue

        # try candidates and record when deeper failure includes no_stop_progs
        for j_id, jm in line_candidates:
            try:
                jt = merged.journey_times[j_id]
                start_dep = jt[0][2]
            except Exception:
                continue
            matched = False
            for d in day_shifts:
                for tz in tz_offsets:
                    adj = od + d + tz
                    if abs(start_dep - adj) <= int(tol):
                        matched = True
                        break
                if matched:
                    break
            if not matched:
                continue

            # attempt deeper projection/interpolation; if stop_progs empty, record
            r_int = merged.journey_to_route[j_id] if j_id < len(merged.journey_to_route) else -1
            route_stops = merged.route_stops[r_int] if r_int < len(merged.route_stops) else []

            # NOTE: full-route polylines were removed; prefer stitched link fragments.
            route_poly = []
            try:
                from api import _try_stitch_route_link_tracks_for_route_int  # type: ignore

                route_poly = _try_stitch_route_link_tracks_for_route_int(merged, r_int, walking) or []
            except Exception:
                route_poly = []

            if not route_poly:
                # fall back: coarse polyline from route stops coords
                ttrack = []
                for sid in route_stops:
                    try:
                        slat, slon = walking.get_loc_coords(sid)
                        ttrack.append((slat, slon))
                    except Exception:
                        continue
                route_poly = ttrack

            cum = _cum_distances(route_poly) if route_poly else [1.0]
            # compute projection-based stop_progs
            proj_stop_progs = []
            for sid, atime, dtime in jt:
                try:
                    slat, slon = walking.get_loc_coords(sid)
                except Exception:
                    continue
                sd, along = _project_onto_track(slat, slon, route_poly, cum)
                sched = atime if atime is not None else dtime
                if sched is not None:
                    proj_stop_progs.append((along, sched))
            proj_stop_progs = [s for s in proj_stop_progs if s[1] is not None]

            # synthesize uniform stop_progs from scheduled times if projection
            # produced none — this mirrors production matcher fallback.
            synth_stop_progs = []
            if not proj_stop_progs:
                try:
                    sched_times = []
                    for sid, atime, dtime in jt:
                        sched = atime if atime is not None else dtime
                        if sched is None:
                            continue
                        sched_times.append(sched)
                    n = len(sched_times)
                    if n == 1:
                        synth_stop_progs = [(0.0, sched_times[0])]
                    elif n > 1:
                        for idx, sched in enumerate(sched_times):
                            frac = idx / (n - 1)
                            synth_stop_progs.append((frac, sched))
                except Exception:
                    synth_stop_progs = []

            # If projection produced none, record the case and include
            # synthesized counts so we can see whether fallback would apply.
            if not proj_stop_progs:
                found.append({
                    'vref': meta.get('vehicle_ref') or '',
                    'line_ref': line_ref,
                    'j_id': j_id,
                    'r_int': r_int,
                    'route_poly_len': len(route_poly) if route_poly else 0,
                    'route_stops_len': len(route_stops) if route_stops else 0,
                    'projection_stop_progs_count': 0,
                    'synth_stop_progs_count': len(synth_stop_progs),
                    'using_fallback': bool(synth_stop_progs),
                })
                break

    # write report
    out = {'timestamp': int(time.time()), 'found': found}
    try:
        os.makedirs('cache', exist_ok=True)
        with open('cache/no_stop_progs.json', 'w') as fh:
            json.dump(out, fh, indent=2)
    except Exception:
        pass

    print(json.dumps({'found': len(found)}, indent=2))


if __name__ == '__main__':
    main()
