#!/usr/bin/env python3
import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / 'transport-backend'))
from api import get_router_for_date

def main(atco):
    merged, _router, _walking = get_router_for_date('2026-03-08')
    ints = [i for i in range(len(merged.stop_to_routes)) if merged.get_atco_code(i) == atco]
    print('stop_ints=', ints)
    for i in ints:
        routes = merged.stop_to_routes[i]
        print('stop_int', i, 'routes=', routes)
        for r in routes:
            meta = merged.route_metadata[r]
            rid = meta.get('route_id') if meta else None
            ln = meta.get('line_name') if meta else None
            print('  ', r, rid, 'line_name=', ln)

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: check_stop_routes.py ATCO')
        sys.exit(2)
    main(sys.argv[1])
