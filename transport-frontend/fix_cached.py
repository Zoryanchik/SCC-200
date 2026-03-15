with open('src/components/map/MapViewMap.jsx', 'r') as f:
    lines = f.readlines()

new_lines = []
for i, line in enumerate(lines):
    new_lines.append(line)
    if "leg-geometry returned linear (cached)" in line:
        # we know the next line is continue; and the next is }
        # we will insert after `}`
        pass

for i, line in enumerate(lines):
    if "leg-geometry returned linear (cached)" in line:
        idx = i + 2 # the line with `}`
        insert_text = """\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\tif (norm && norm.length >= 2 && data && data.source === 'route_tracks') {
\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\ttry { console.debug('[map] leg-geometry selected route_tracks (cached)', { line: String(line), rid, coordsLen: norm.length }); } catch (e) { /* ignore */ }
\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\tconst color = busIconColor(marker.delayMinutes);
\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\tsetSelectedVehicleTrack((prev) => (prev && prev.id === marker.id) ? { ...prev, coords: norm, color } : prev);
\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\tbreak;
\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t}\n"""
        lines.insert(idx + 1, insert_text)
        break

with open('src/components/map/MapViewMap.jsx', 'w') as f:
    f.write("".join(lines))
print("Done")
