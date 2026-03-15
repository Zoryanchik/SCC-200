#!/usr/bin/env python3
import os, sys
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
TOP_DIR = os.path.dirname(THIS_DIR)
if TOP_DIR not in sys.path:
    sys.path.insert(0, TOP_DIR)

from api import route_geometry

# Provide a canonical route_id (file-prefixed where applicable)
ROUTE_ID = 'MO42_BG-None--SCCU-NWMO-2025-11-02-Lancaster_Feb_Sch_change_2026_-BODS_V1_1::PC0002407:98:RS5'

print('Requesting geometry for route_id:', ROUTE_ID)
res = route_geometry(ROUTE_ID)

import json
print('Result keys:', list(res.keys()))
# Print source and number of coords if present
if isinstance(res, dict):
    src = res.get('source')
    coords = res.get('coords')
    print('source:', src)
    if coords and isinstance(coords, list):
        print('coords count:', len(coords))
        print('first 5 coords:', coords[:5])
    else:
        print('coords: None or empty')
    if res.get('per_leg_coords'):
        plc = res.get('per_leg_coords')
        print('per_leg_coords segments:', len(plc))
else:
    print('Unexpected response:', res)
