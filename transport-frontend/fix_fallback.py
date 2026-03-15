with open('src/components/map/MapViewMap.jsx', 'r') as f:
    text = f.read()

old_fallback = """
                                                let best = null;
                                                if (metaRouteId || debugShowAllBuses) {
                                                        for (const variant of routeData.variants) {
                                                                let geom = Array.isArray(variant.geometry) ? variant.geometry : stopsToLatLngs(variant.stops);
                                                                if (!Array.isArray(geom) || geom.length === 0) continue;
                                                                const norm = geom.map((pt) => ([Number(pt[0]), Number(pt[1])]));
                                                                let minD = Infinity;
                                                                for (const p of norm) {
                                                                        const dlat = p[0] - pos[0];
                                                                        const dlon = p[1] - pos[1];
                                                                        const d2 = dlat * dlat + dlon * dlon;
                                                                        if (d2 < minD) minD = d2;
                                                                }
                                                                if (best == null || minD < best.minD) {
                                                                        best = { variant, norm, minD };
                                                                }
                                                        }

                                                        if (!best) {
                                                                setSelectedVehicleTrack(null);
                                                                return;
                                                        }
                                                } else {
                                                        setSelectedVehicleTrack(null);
                                                        return;
                                                }
"""

new_fallback = """
                                                let best = null;
                                                for (const variant of routeData.variants) {
                                                        let geom = Array.isArray(variant.geometry) ? variant.geometry : stopsToLatLngs(variant.stops);
                                                        if (!Array.isArray(geom) || geom.length === 0) continue;
                                                        const norm = geom.map((pt) => ([Number(pt[0]), Number(pt[1])]));
                                                        let minD = Infinity;
                                                        for (const p of norm) {
                                                                const dlat = p[0] - pos[0];
                                                                const dlon = p[1] - pos[1];
                                                                const d2 = dlat * dlat + dlon * dlon;
                                                                if (d2 < minD) minD = d2;
                                                        }
                                                        if (best == null || minD < best.minD) {
                                                                best = { variant, norm, minD };
                                                        }
                                                }

                                                if (!best) {
                                                        setSelectedVehicleTrack(null);
                                                        return;
                                                }
"""

if old_fallback in text:
    text = text.replace(old_fallback, new_fallback)
    with open('src/components/map/MapViewMap.jsx', 'w') as f:
        f.write(text)
    print("Replaced successfully")
else:
    print("Could not find the fallback string.")

