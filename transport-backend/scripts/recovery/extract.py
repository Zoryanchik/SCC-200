import re

with open('transport-backend/api_40c9ba0.py', 'r', encoding='utf-16') as f:
    text = f.read()

m = re.search(r'def _subsegment_from_coords\(.*?(?=\ndef |\n@|\Z)', text, re.DOTALL | re.MULTILINE)
if m:
    with open('transport-backend/api.py', 'r', encoding='utf-8') as f:
        t = f.read()
    
    t = t.replace('def route_leg_geometry(', m.group(0) + '\n\ndef route_leg_geometry(')
    with open('transport-backend/api.py', 'w', encoding='utf-8') as f:
        f.write(t)
