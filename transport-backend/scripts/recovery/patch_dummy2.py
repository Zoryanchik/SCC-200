import re
import os

for path in ['transport-backend/tests/test_live_match_offtrack_between_far_stops.py', 'transport-backend/tests/test_live_match_unstable_suppression.py', 'transport-backend/tests/test_progress_unbound_regression.py']:
    if not os.path.exists(path): continue
    with open(path, 'r', encoding='utf-8') as f:
        t = f.read()

    t = re.sub(r'(class _DummyMerged:.*?)(def get_atco_code\()', r'\1    route_tracks = [[]]\n    \2', t, flags=re.DOTALL)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(t)
