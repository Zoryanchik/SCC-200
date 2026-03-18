"""Produce a detailed report for journeys that failed with `no_stop_progs`.

Reads `cache/no_stop_progs.json` (produced by `investigate_no_stop_progs.py`)
and writes `cache/no_stop_progs_detailed.json` with per-stop coordinate
availability, a short route-track snippet, and basic diagnostics.

Run from the `transport-backend` folder:

    PYTHONPATH=. python3 scripts/no_stop_progs_detail.py

"""
import json
import os
import time

from api import get_router_for_date


def main():
    src = 'cache/no_stop_progs.json'
    if not os.path.exists(src):
        print('no cache/no_stop_progs.json — run investigate_no_stop_progs.py first')
        return
    with open(src, 'r') as fh:
        data = json.load(fh)
    entries = data.get('found', [])
    today = time.strftime('%Y-%m-%d')
    merged, router, walking = get_router_for_date(today, start_time=None, apply_delay=False)

    out = {'timestamp': int(time.time()), 'details': []}
    for e in entries:
        j_id = e.get('j_id')
        r_int = e.get('r_int')
        line_ref = e.get('line_ref')
        vref = e.get('vref')
        detail = {'vref': vref, 'j_id': j_id, 'r_int': r_int, 'line_ref': line_ref}
        try:
            jt = merged.journey_times[j_id]
        except Exception:
            detail['error'] = 'no_journey_times'
            out['details'].append(detail)
            continue
        stops = []
        missing_coords = 0
        for sid, atime, dtime in jt:
            try:
                coords = walking.get_loc_coords(sid)
            except Exception:
                coords = None
            ok = bool(coords and len(coords) == 2)
            if not ok:
                missing_coords += 1
            stops.append({'stop_int': sid, 'has_coords': ok, 'coords': coords})
        detail['stop_count'] = len(stops)
        detail['missing_coords'] = missing_coords
        # projection / fallback metadata (if present from investigator)
        if 'projection_stop_progs_count' in e:
            detail['projection_stop_progs_count'] = e.get('projection_stop_progs_count')
        if 'synth_stop_progs_count' in e:
            detail['synth_stop_progs_count'] = e.get('synth_stop_progs_count')
        if 'using_fallback' in e:
            detail['using_fallback'] = e.get('using_fallback')
        detail['stops'] = stops

        # Geometry diagnostics
    # NOTE: full-route polylines were removed; geometry is provided via stop-to-stop
        # link fragments (route_link_tracks).
        link_tracks = None
        try:
            link_tracks = merged.get_route_link_tracks()
        except Exception:
            link_tracks = None

        if isinstance(link_tracks, dict):
            # We don't have route_int -> fragments directly anymore;
            # report global availability and a small sample for debugging.
            detail['route_link_tracks_links'] = len(link_tracks)
            sample_keys = list(link_tracks.keys())[:5]
            detail['route_link_tracks_sample_keys'] = sample_keys
            # best-effort snippet: first fragment of the first sampled key
            frag_snip = []
            try:
                if sample_keys:
                    frags = link_tracks.get(sample_keys[0]) or []
                    if frags and isinstance(frags[0], (list, tuple)):
                        frag_snip = list(frags[0])[:6]
            except Exception:
                frag_snip = []
            detail['route_link_tracks_sample_fragment_snippet'] = frag_snip
        else:
            detail['route_link_tracks_links'] = 0

        out['details'].append(detail)

    os.makedirs('cache', exist_ok=True)
    with open('cache/no_stop_progs_detailed.json', 'w') as fh:
        json.dump(out, fh, indent=2)
    print(f"wrote cache/no_stop_progs_detailed.json ({len(out['details'])} entries)")


if __name__ == '__main__':
    main()
