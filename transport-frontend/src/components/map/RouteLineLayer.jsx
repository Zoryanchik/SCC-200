import React, { useMemo } from 'react';
import { Polyline, CircleMarker, Tooltip } from 'react-leaflet';
import { stopsToLatLngs } from '../../services/routeLineApi';

/**
 * A vibrant palette that cycles for different route variants so
 * inbound / outbound / variant lines are distinguishable.
 */
const VARIANT_COLORS = [
  '#1976d2', // blue
  '#d32f2f', // red
  '#388e3c', // green
  '#7b1fa2', // purple
  '#f57c00', // orange
  '#0097a7', // teal
  '#c2185b', // pink
  '#455a64', // blue-grey
];

/**
 * Draws all route variants for a single active bus line on the map.
 *
 * Each variant is rendered as a coloured polyline with small circle
 * markers at each stop.  Hovering a stop shows its name.
 *
 * @param {{ routeData: object, lineColor?: string }} props
 *   routeData — the { line, variants } object from the API / cache
 */
function SingleRouteLine({ routeData }) {
  if (!routeData || !routeData.variants || routeData.variants.length === 0) {
    return null;
  }

  return (
    <>
      {routeData.variants.map((variant, idx) => {
        // Prefer OSRM / stored route geometry when available;
        // fall back to straight stop-to-stop lines. When geometry is
        // available we also merge explicit stop coordinates into the
        // geometry so stops are guaranteed to lie on the rendered line
        // and then do a light Chaikin smoothing pass for a nicer visual.
        const stopPositions = stopsToLatLngs(variant.stops);
        // Project a point onto a segment (a->b) returning the nearest point
        const projectPointToSegment = (p, a, b) => {
          // treat coordinates as (lat, lon) pairs in degrees — good enough for short distances
          const vx = b[0] - a[0];
          const vy = b[1] - a[1];
          const wx = p[0] - a[0];
          const wy = p[1] - a[1];
          const denom = vx * vx + vy * vy || 1e-12;
          let t = (wx * vx + wy * vy) / denom;
          if (t < 0) t = 0;
          if (t > 1) t = 1;
          return [a[0] + t * vx, a[1] + t * vy];
        };

        // Insert each stop projected onto the nearest geometry segment so the
        // route follows the main road rather than kink towards the raw stop coords.
        const insertStopsIntoGeometry = (geom, stops) => {
          if (!Array.isArray(geom) || geom.length < 2) return stops || [];
          const out = geom.slice();

          for (const stop of (stops || [])) {
            let bestSegIdx = 0;
            let bestD = Infinity;
            let bestProj = null;
            // Find nearest segment and projected point
            for (let i = 0; i < out.length - 1; i++) {
              const a = out[i];
              const b = out[i + 1];
              const proj = projectPointToSegment(stop, a, b);
              const dlat = proj[0] - stop[0];
              const dlon = proj[1] - stop[1];
              const d2 = dlat * dlat + dlon * dlon;
              if (d2 < bestD) {
                bestD = d2;
                bestSegIdx = i;
                bestProj = proj;
              }
            }

            if (bestProj) {
              // Insert the projected point after the segment's start vertex so
              // the stop lies on the polyline but the geometry shape remains road-following.
              // Avoid inserting duplicates next to identical points.
              const nextIdx = bestSegIdx + 1;
              const existing = out[nextIdx];
              if (!existing || existing[0] !== bestProj[0] || existing[1] !== bestProj[1]) {
                out.splice(nextIdx, 0, bestProj);
              }
            }
          }

          return out;
        };

        const chaikinSmooth = (coords, iterations = 1) => {
          if (!Array.isArray(coords) || coords.length < 3) return coords;
          let pts = coords.slice();
          for (let it = 0; it < iterations; it++) {
            const next = [];
            next.push(pts[0]);
            for (let i = 0; i < pts.length - 1; i++) {
              const [x0, y0] = pts[i];
              const [x1, y1] = pts[i + 1];
              const Q = [0.75 * x0 + 0.25 * x1, 0.75 * y0 + 0.25 * y1];
              const R = [0.25 * x0 + 0.75 * x1, 0.25 * y0 + 0.75 * y1];
              next.push(Q);
              next.push(R);
            }
            next.push(pts[pts.length - 1]);
            pts = next;
          }
          return pts;
        };

        const positions = variant.geometry
          ? chaikinSmooth(insertStopsIntoGeometry(variant.geometry, stopPositions), 1)
          : stopPositions;
        
        
        if (positions.length < 2) return null;

        const color = VARIANT_COLORS[idx % VARIANT_COLORS.length];

        return (
          <React.Fragment key={`${routeData.line}-v${idx}`}>
            {/* Cyan underlay/frame so route variants have a cyan outline */}
            <Polyline
              positions={positions}
              pathOptions={{
                color: '#00ffff',
                weight: 6,
                opacity: 0.9,
                dashArray: idx > 0 ? '8 6' : undefined,
              }}
            />

            {/* The route polyline (on top) */}
            <Polyline
              positions={positions}
              pathOptions={{
                color,
                weight: 4,
                opacity: 0.8,
                dashArray: idx > 0 ? '8 6' : undefined, // dash secondary variants
              }}
            />

            {/* Small circles at each stop along the route */}
            {variant.stops
              .filter((s) => s.lat != null && s.lon != null)
              .map((stop, sIdx) => (
                <CircleMarker
                  key={`${routeData.line}-v${idx}-s${sIdx}`}
                  center={[stop.lat, stop.lon]}
                  radius={5}
                  pathOptions={{
                    color: '#fff',
                    weight: 2,
                    fillColor: color,
                    fillOpacity: 1,
                  }}
                >
                  <Tooltip direction="top" offset={[0, -6]}>
                    <span style={{ fontWeight: 600 }}>{stop.name}</span>
                  </Tooltip>
                </CircleMarker>
              ))}
          </React.Fragment>
        );
      })}
    </>
  );
}

/**
 * Map layer that renders polylines for every currently-active bus route.
 *
 * @param {{ activeRoutes: Map<string, object> }} props
 *   activeRoutes — from useRouteLine().activeRoutes
 */
export default function RouteLineLayer({ activeRoutes }) {
  const entries = useMemo(
    () => Array.from(activeRoutes.entries()),
    [activeRoutes],
  );

  if (entries.length === 0) return null;

  return (
    <>
      {entries.map(([line, routeData]) => (
        <SingleRouteLine key={line} routeData={routeData} />
      ))}
    </>
  );
}

export { SingleRouteLine };
