#!/usr/bin/env python3
import os, sys
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
TOP_DIR = os.path.dirname(THIS_DIR)
if TOP_DIR not in sys.path:
    sys.path.insert(0, TOP_DIR)

from api import routes_for_line, _fetch_logged_journey_from_db
import asyncio

async def main():
    print('Calling routes_for_line("42")')
    res = await routes_for_line('42')
    print('variants count:', len(res.get('variants', [])))
    for i, v in enumerate(res.get('variants', [])):
        print(i, v.get('route_id'))

    print('\nFetching logged journey dc57121d59b64492a964ad109a3d7835')
    lj = _fetch_logged_journey_from_db('dc57121d59b64492a964ad109a3d7835')
    print('type:', type(lj))
    if lj:
        for li, leg in enumerate(lj.get('legs', [])):
            jm = leg.get('journey_metadata') or {}
            print(f'leg {li} journey_metadata.route_id =', jm.get('route_id'))
            print(f'leg {li} journey_metadata.journey_id =', jm.get('journey_id'))

if __name__ == '__main__':
    asyncio.run(main())
