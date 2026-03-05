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
        // Prefer OSRM road-following geometry when available;
        // fall back to straight stop-to-stop lines.
        const positions = variant.geometry
          ? variant.geometry
          : stopsToLatLngs(variant.stops);
        if (positions.length < 2) return null;

        const color = VARIANT_COLORS[idx % VARIANT_COLORS.length];

        return (
          <React.Fragment key={`${routeData.line}-v${idx}`}>
            {/* The route polyline */}
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
