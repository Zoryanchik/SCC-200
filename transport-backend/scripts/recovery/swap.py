import re

with open('transport-backend/api.py', 'r', encoding='utf-8') as f:
    t = f.read()

# Replace the misaligned chunk!
# We just need to move @app.get("/route/leg-geometry") to before def route_leg_geometry(.
idx1 = t.find('@app.get("/route/leg-geometry")')
idx2 = t.find('def _subsegment_from_coords')
idx3 = t.find('def route_leg_geometry(')

subseg = t[idx2:idx3]
t_clean = t[:idx1] + subseg + t[idx1:idx2] + t[idx3:]

with open('transport-backend/api.py', 'w', encoding='utf-8') as f:
    f.write(t_clean)
