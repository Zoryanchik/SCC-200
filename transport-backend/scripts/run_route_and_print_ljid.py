#!/usr/bin/env python3
import os, sys
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
TOP_DIR = os.path.dirname(THIS_DIR)
if TOP_DIR not in sys.path:
    sys.path.insert(0, TOP_DIR)

from api import get_router_for_date, build_journey_plan_response
from time_utils import seconds_since_midnight

DATE = '2026-03-03'
START_TIME_STR = '08:00:00'
start_seconds = seconds_since_midnight(START_TIME_STR)

print('Building router for date', DATE, 'at', START_TIME_STR)
merged, router, walking = get_router_for_date(DATE, start_time=start_seconds)

# find stops by substring
def find_stop(q):
    q = q.lower()
    for i, name in enumerate(merged.stop_metadata):
        if name and q in name.lower():
            return i, name
    return None, None

s_idx, s_name = find_stop('underpass')
d_idx, d_name = find_stop('common garden')
print('found start:', s_idx, s_name)
print('found dest :', d_idx, d_name)

if s_idx is None or d_idx is None:
    print('One or both stops not found; aborting')
    sys.exit(1)

start_point = getattr(walking, '_coords', {}).get(s_idx)
dest_point = getattr(walking, '_coords', {}).get(d_idx)
print('coords start:', start_point)
print('coords dest :', dest_point)

if not start_point or not dest_point:
    print('Missing coords for one or both stops; aborting')
    sys.exit(1)

result = router.route(
    n_transfer_limit=2,
    walking=walking,
    start_time=start_seconds,
    start_point=start_point,
    destination=dest_point,
    allowed_modes={'bus','train'},
)
resp = build_journey_plan_response(result, merged, getattr(walking, '_coords', {}), request_start_seconds=start_seconds)
print('meta.route_id =', resp.get('meta', {}).get('route_id'))
print('legs count =', len(resp.get('legs', [])))
if resp.get('legs'):
    print('first leg:', resp['legs'][0])
