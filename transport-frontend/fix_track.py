import re

with open('src/components/map/MapViewMap.jsx', 'r') as f:
    text = f.read()

# 1. Update the cached geometry success check
old_cached_geom_check = """
                                                        if (data && data.source === 'linear') {
                                                                try { console.debug('[map] leg-geometry returned linear (cached)', { line: String(line), rid, coordsLen: norm ? norm.length : 0 }); } catch (e) { /* ignore */ }
                                                                continue;
                                                        }
"""
new_cached_geom_check = """
                                                        if (data && data.source === 'linear') {
                                                                try { console.debug('[map] leg-geometry returned linear (cached)', { line: String(line), rid, coordsLen: norm ? norm.length : 0 }); } catch (e) { /* ignore */ }
                                                                continue;
                                                        }
                                                        if (norm && norm.length >= 2 && data && data.source === 'route_tracks') {
                                                                try { console.debug('[map] leg-geometry selected route_tracks (cached)', { line: String(line), rid, coordsLen: norm.length }); } catch (e) { /* ignore */ }
                                                                const color = busIconColor(marker.delayMinutes);
                                                                setSelectedVehicleTrack((prev) => (prev && prev.id === marker.id) ? { ...prev, coords: norm, color } : prev);
                                                                break;
                                                        }
"""
text = text.replace(old_cached_geom_check, new_cached_geom_check)

# 2. Update the post-route geometry success check so it doesn't drop stops/label
old_post_geom_check = "setSelectedVehicleTrack((prev) => (prev && prev.id === marker.id) ? { ...prev, coords: norm, color } : { id: marker.id, coords: norm, color, stops: [], label: null });"
new_post_geom_check = "setSelectedVehicleTrack((prev) => (prev && prev.id === marker.id) ? { ...prev, coords: norm, color } : prev);"
text = text.replace(old_post_geom_check, new_post_geom_check)

# 3. Delete the early return in the cached branch
early_return_block = """
                                                })(selectionToken);
                                                // Important: in cached mode we kick off the label/geometry fetch above.
                                                // Don't run the synchronous variant-selection logic below, because it can
                                                // overwrite the just-fetched in-memory track/label overlay.
                                                const color = busIconColor(marker.delayMinutes);
                                                // Don't set coords to [] here — wait for route_tracks fetch to succeed.
                                                setSelectedVehicleTrack({ id: marker.id, coords: null, color, stops: [], label: null });
                                                try {
                                                        try { if (onOpenPopupSignature) onOpenPopupSignature(makeMarkerSignature(marker)); } catch (ee) { /* ignore */ }
                                                        onOpenPopup(marker.id);
                                                } catch (e) { /* ignore */ }
                                                return;
"""
new_return_block = """
                                                })(selectionToken);
"""
text = text.replace(early_return_block, new_return_block)

# 4. Restore the `stops: stops` instead of `stops: []` in synchronous setters
# We have a few instances of this.
text = re.sub(
    r"setSelectedVehicleTrack\(\{ id: marker\.id, coords: norm, color, stops: \[\], label: null \}\);",
    r"setSelectedVehicleTrack({ id: marker.id, coords: norm, color, stops, label: null });",
    text
)

# 5. Fix `const stops = Array.isArray... stops: []` that might have been changed?
# Wait, look at line 1720: "const stops = Array.isArray(match.stops) ? stopsToLatLngs(match.stops) : [];"
# Wait, this was already defining stops. I just need to make sure we don't have stray bracket arrays.
# The re.sub above handles it.

with open('src/components/map/MapViewMap.jsx', 'w') as f:
    f.write(text)

print("Done")
