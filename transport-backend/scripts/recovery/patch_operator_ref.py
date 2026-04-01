import os
import re

for path in ['transport-backend/tests/test_live_match_offtrack_between_far_stops.py', 'transport-backend/tests/test_live_match_unstable_suppression.py', 'transport-backend/tests/test_progress_unbound_regression.py']:
    if not os.path.exists(path): continue
    with open(path, 'r', encoding='utf-8') as f:
        t = f.read()

    # Some tests do operator_ref="XY". We can just remove operator_ref=... from the calls!
    t = re.sub(r',\s*operator_ref=[^,\)]+', '', t)
    
    # Also I'll check if they test the result of _compute_delay_from_timetable. Because it changed from a tuple to returning a single delay_s!
    t = re.sub(r'delay,\s*dist,\s*frag_idx,\s*dist_thresh\s*=\s*(api_module\._compute_delay_from_timetable\()', r'res = \1', t)

    with open(path, 'w', encoding='utf-8') as f:
        f.write(t)
