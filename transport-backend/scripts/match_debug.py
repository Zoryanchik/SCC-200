"""Instrumented matcher diagnostic.

Fetch live vehicles and replay the matcher deeper phase with fine-grained
subreason labels so we can see what composes the
"spatial_or_temporal_gate_failed" bucket.

Run from the `transport-backend` folder:

    PYTHONPATH=. python3 scripts/match_debug.py

The script writes a JSON report to `cache/match_debug.json` and prints a
short summary to stdout.
"""
import json
import time
from collections import defaultdict
import os

from bus_live import BusLive
from api import get_router_for_date
from time_utils import seconds_since_midnight


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


def main():
    bl = BusLive(timeout=20)
    records = bl.get_bus_live(54.0, -2.8, lat_tol=2.0, lon_tol=2.0)
    print(f"[match_debug] fetched {len(records)} records")
    now = int(time.time())
    today = time.strftime('%Y-%m-%d')
    merged, router, walking = get_router_for_date(today, start_time=None, apply_delay=False)

    # Allow overriding strict tolerance via env var for experiments
    strict_tol = int(os.environ.get('MATCH_STRICT_TOL', '30'))

    totals = defaultdict(int)
    examples = defaultdict(list)

    processed = 0
    # defensive: if records is empty-ish, ensure we handle gracefully
    if not records:
        print("[match_debug] no records returned by BusLive.get_bus_live()")
        report = {
            'total_records': 0,
            'totals': {},
            'examples': {},
        }
        try:
            with open('cache/match_debug.json', 'w') as fh:
                json.dump(report, fh, indent=2)
        except Exception:
            pass
        print(json.dumps(report, indent=2))
        return
    for rec in records:
        # Support both dict-shaped records and tuple-shaped records returned
        # by BusLive. Tuple format typically contains (line, stopname, lat,
        # lon, operator_text, ..., origin_dep_secs, heading, metadata_dict).
        vref = ''
        line = ''
        lat = None
        lon = None
        operator_ref = None
        origin_atco = None
        destination_atco = None
        origin_dep_secs = None
        try:
            if isinstance(rec, dict):
                vref = rec.get('vehicleRef') or rec.get('vehicle_ref') or ''
                line = (rec.get('lineRef') or rec.get('line_ref') or '').strip()
                lat = float(rec.get('lat'))
                lon = float(rec.get('lon'))
                operator_ref = rec.get('operator_ref')
                origin_atco = rec.get('origin_atco')
                destination_atco = rec.get('destination_atco')
                origin_dep_secs = rec.get('origin_dep_secs')
            elif isinstance(rec, (tuple, list)):
                # Best-effort unpacking from known tuple layout
                try:
                    line = str(rec[0])
                except Exception:
                    line = ''
                try:
                    lat = float(rec[2])
                    lon = float(rec[3])
                except Exception:
                    lat = None
                    lon = None
                # metadata dict often in last element
                meta = rec[-1] if rec and isinstance(rec[-1], dict) else {}
                vref = meta.get('vehicle_ref') or meta.get('vehicleRef') or ''
                operator_ref = meta.get('operator_ref') or meta.get('operatorRef')
                origin_atco = meta.get('origin_atco') or meta.get('OriginRef')
                destination_atco = meta.get('destination_atco') or meta.get('DestinationRef')
                # BusLive returns origin_dep_secs as the 7th tuple element
                # when records are tuple-shaped. Prefer that numeric value
                # when present. Otherwise fall back to metadata ISO string.
                origin_dep_secs = None
                try:
                    if len(rec) > 6 and isinstance(rec[6], (int, float)):
                        origin_dep_secs = int(rec[6])
                    else:
                        od = meta.get('origin_dep_secs') or meta.get('OriginAimedDepartureTime')
                        if od:
                            # try to parse ISO datetime like 2026-03-12T15:15:00+00:00
                            try:
                                from datetime import datetime
                                odt = datetime.fromisoformat(str(od).replace('Z', '+00:00'))
                                origin_dep_secs = odt.hour * 3600 + odt.minute * 60 + odt.second
                            except Exception:
                                try:
                                    origin_dep_secs = int(od)
                                except Exception:
                                    origin_dep_secs = None
                except Exception:
                    origin_dep_secs = None
            else:
                # Unknown shape — skip
                continue
        except Exception:
            # malformed record — skip
            continue

        processed += 1
        # follow strict failure checks until spatial/temporal
        # collect line candidates
        simple_line = line.split(':')[-1] if line else ''
        line_candidates = []
        for j_id, jm in enumerate(merged.journey_metadata):
            if not jm:
                continue
            ln = (jm.get('line_name') or '')
            if ln.split(':')[-1].strip() == simple_line:
                line_candidates.append((j_id, jm))
        if not line_candidates:
            # Fatal gate: no matching line — skip silently
            continue

        # destination ATCO
        dest_matches = []
        for j_id, jm in line_candidates:
            try:
                jt = merged.journey_times[j_id]
                dest_stop_int = jt[-1][0]
                journey_dest_atco = merged.get_atco_code(dest_stop_int)
            except Exception:
                journey_dest_atco = None
            if destination_atco:
                if journey_dest_atco and str(journey_dest_atco).strip() == str(destination_atco).strip():
                    dest_matches.append((j_id, jm))
        if destination_atco and not dest_matches:
            # Fatal gate: explicit destination ATCO mismatch — skip silently
            continue

        # operator check
        if operator_ref:
            matched_op = False
            for j_id, jm in line_candidates:
                op_noc = jm.get('operator_national_code') or ''
                if op_noc and str(op_noc).strip() == str(operator_ref).strip():
                    matched_op = True
                    break
                svc = jm.get('service_code') or ''
                if not svc:
                    ln = (jm.get('line_name') or '')
                    if ':' in ln:
                        svc = ln.split(':')[0]
                if svc and str(svc).strip() == str(operator_ref).strip():
                    matched_op = True
                    break
            if not matched_op:
                # Fatal gate: operator mismatch — skip silently
                continue

        # origin ATCO proximity/resolution — prefer feed origin ATCO
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
                    nearby = walking.reachable_stops((lat, lon))
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
            # Fatal gate: origin ATCO mismatch or unreachable — skip silently
            continue

        # origin_dep_secs check
        if origin_dep_secs is None:
            # Fatal gate: missing origin departure — skip silently
            continue
        within_tol = False
        # `strict_tol` is read from MATCH_STRICT_TOL env var (default 30)
        for j_id, jm in line_candidates:
            try:
                jt = merged.journey_times[j_id]
                start_dep = jt[0][2]
                if abs(start_dep - origin_dep_secs) <= int(strict_tol):
                    within_tol = True
                    break
            except Exception:
                continue
        if not within_tol:
            # Fatal gate: origin departure out of tolerance — skip silently
            continue

        # Now the deeper projection/interpolation checks per candidate: if
        # any candidate would succeed here we count 'matched'; otherwise
        # we record the first failing subreason across candidates.
        candidate_failed_reasons = []

        for j_id, jm in line_candidates:
            try:
                jt = merged.journey_times[j_id]
                start_dep = jt[0][2]
                end_arr = jt[-1][1] if jt[-1][1] is not None else jt[-1][2]
            except Exception:
                candidate_failed_reasons.append('no_journey_times')
                continue
            if end_arr is None or start_dep is None:
                candidate_failed_reasons.append('invalid_start_or_end')
                continue
            r_int = merged.journey_to_route[j_id] if j_id < len(merged.journey_to_route) else -1
            if r_int < 0:
                candidate_failed_reasons.append('no_route')
                continue

            # track availability
            track = merged.route_tracks[r_int] if r_int < len(merged.route_tracks) else []
            if not track:
                # try to build track from route stops coords
                route_stops = merged.route_stops[r_int] if r_int < len(merged.route_stops) else []
                ttrack = []
                for sid in route_stops:
                    try:
                        slat, slon = walking.get_loc_coords(sid)
                        ttrack.append((slat, slon))
                    except Exception:
                        continue
                track = ttrack
            if not track:
                candidate_failed_reasons.append('no_track')
                continue
            cum = _cum_distances(track)
            try:
                dist_m, progress = _project_onto_track(lat, lon, track, cum)
            except Exception:
                candidate_failed_reasons.append('projection_error')
                continue

            # Attempt to interpolate expected_time. Prefer stop-projection
            # when stop coordinates exist, but if we cannot build stop_progs
            # fall back to a uniform fractional mapping across scheduled
            # stop times (same approach as used in api.py). If that still
            # fails, fall back to a linear estimate across the journey
            # duration using `progress`.
            expected_time = None
            stop_progs = []
            try:
                # try projection-based stop_progs first
                for sid, atime, dtime in jt:
                    try:
                        slat, slon = walking.get_loc_coords(sid)
                    except Exception:
                        continue
                    sd, along = _project_onto_track(slat, slon, track, cum)
                    stop_progs.append((along, atime if atime is not None else dtime))
                stop_progs = [s for s in stop_progs if s[1] is not None]
                stop_progs.sort(key=lambda x: x[0])
            except Exception:
                stop_progs = []

            # If projection failed to produce stop_progs, synthesize a
            # uniform fractional mapping from scheduled stop times.
            if not stop_progs:
                try:
                    sched_times = []
                    for sid, atime, dtime in jt:
                        sched = atime if atime is not None else dtime
                        if sched is None:
                            continue
                        sched_times.append(sched)
                    n = len(sched_times)
                    if n == 1:
                        stop_progs = [(0.0, sched_times[0])]
                    elif n > 1:
                        for idx, sched in enumerate(sched_times):
                            frac = idx / (n - 1)
                            stop_progs.append((frac, sched))
                except Exception:
                    stop_progs = []

            # interpolate expected_time from stop_progs if possible
            if stop_progs:
                if progress <= stop_progs[0][0]:
                    expected_time = stop_progs[0][1]
                elif progress >= stop_progs[-1][0]:
                    expected_time = stop_progs[-1][1]
                else:
                    for k in range(len(stop_progs) - 1):
                        p0, t0 = stop_progs[k]
                        p1, t1 = stop_progs[k + 1]
                        if p0 <= progress <= p1:
                            seg = p1 - p0
                            frac = (progress - p0) / seg if seg > 0 else 0.0
                            expected_time = t0 + frac * (t1 - t0)
                            break

            # If still no expected_time, fall back to linear estimate
            if expected_time is None:
                try:
                    journey_span = max(end_arr - start_dep, 1)
                    expected_time = int(start_dep + progress * journey_span)
                except Exception:
                    candidate_failed_reasons.append('no_expected_time')
                    continue

            # compute delay relative to current time-of-day (seconds since midnight)
            delay = int((now % 86400) - expected_time)
            journey_dur = max(end_arr - start_dep, 1)
            max_plausible = max(int(journey_dur * 0.5), 1200)
            if abs(delay) > max_plausible:
                candidate_failed_reasons.append('abs_delay_too_big')
                continue

            # If we reach here, candidate looks usable — mark matched and stop
            candidate_failed_reasons = ['matched']
            break

        if 'matched' in candidate_failed_reasons:
            totals['matched'] += 1
            if len(examples['matched']) < 3:
                examples['matched'].append(vref)
            print(f"[{processed}/{len(records)}] {vref}: matched")
            continue

        # aggregate per-vehicle: pick the most common failing reason across
        # candidates or the first seen if tie
        if candidate_failed_reasons:
            from collections import Counter
            c = Counter(candidate_failed_reasons)
            most, _ = c.most_common(1)[0]
            totals[most] += 1
            if len(examples[most]) < 3:
                examples[most].append(vref)
            print(f"[{processed}/{len(records)}] {vref}: {most}")
        else:
            totals['unknown'] += 1
            if len(examples['unknown']) < 3:
                examples['unknown'].append(vref)
            print(f"[{processed}/{len(records)}] {vref}: unknown")

    # end for rec in records
    report = {
        'total_records': len(records),
        'totals': dict(totals),
        'examples': dict(examples),
    }
    try:
        with open('cache/match_debug.json', 'w') as fh:
            json.dump(report, fh, indent=2)
    except Exception:
        pass

    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
