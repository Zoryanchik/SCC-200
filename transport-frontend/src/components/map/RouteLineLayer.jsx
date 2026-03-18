import React, { useEffect, useMemo, useState } from 'react';
import { Polyline, CircleMarker, Tooltip } from 'react-leaflet';
import { stopsToLatLngs } from '../../services/routeLineApi';

// Best-effort OSRM snapping for route overlays.
// Input/Output coords are [lat, lon].
const OSRM_BASE = (typeof import.meta !== 'undefined' && import.meta.env && import.meta.env.VITE_OSRM_BASE)
  ? String(import.meta.env.VITE_OSRM_BASE)
  : 'http://127.0.0.1:5012';

const osrmRouteCoordsLatLon = async (coordsLatLon, { profile = 'driving', timeoutMs = 2500, maxWaypoints = 90 } = {}) => {
  try {
    if (!Array.isArray(coordsLatLon) || coordsLatLon.length < 2) return coordsLatLon;
    const norm = [];
    for (const pt of coordsLatLon) {
      if (!Array.isArray(pt) || pt.length < 2) continue;
      const lat = Number(pt[0]);
      const lon = Number(pt[1]);
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue;
      norm.push([lat, lon]);
    }
    if (norm.length < 2) return coordsLatLon;

    let sampled = norm;
    if (norm.length > maxWaypoints) {
      const step = (norm.length - 1) / (maxWaypoints - 1);
      sampled = [];
      for (let i = 0; i < maxWaypoints; i++) {
        const idx = Math.round(i * step);
        sampled.push(norm[Math.min(norm.length - 1, Math.max(0, idx))]);
      }
      const dedup = [];
      for (const p of sampled) {
        const last = dedup[dedup.length - 1];
        if (!last || last[0] !== p[0] || last[1] !== p[1]) dedup.push(p);
      }
      sampled = dedup;
      if (sampled.length < 2) return coordsLatLon;
    }

    const coordStr = sampled.map(([lat, lon]) => `${lon},${lat}`).join(';');
    const url = `${OSRM_BASE.replace(/\/$/, '')}/route/v1/${encodeURIComponent(profile)}/${coordStr}?overview=full&geometries=geojson`;

    const controller = new AbortController();
    const t = setTimeout(() => controller.abort(), timeoutMs);
    let resp;
    try {
      resp = await fetch(url, { signal: controller.signal });
    } finally {
      clearTimeout(t);
    }
    if (!resp || !resp.ok) return coordsLatLon;
    const data = await resp.json();
    const osrmCoords = data && data.routes && data.routes[0] && data.routes[0].geometry && data.routes[0].geometry.coordinates;
    if (!Array.isArray(osrmCoords) || osrmCoords.length < 2) return coordsLatLon;
    const out = [];
    for (const pt of osrmCoords) {
      if (!Array.isArray(pt) || pt.length < 2) continue;
      const lon = Number(pt[0]);
      const lat = Number(pt[1]);
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue;
      out.push([lat, lon]);
    }
    return out.length >= 2 ? out : coordsLatLon;
  } catch (e) {
    return coordsLatLon;
  }
};

const buildSnapKey = (coords, { maxPts = 12 } = {}) => {
  if (!Array.isArray(coords) || coords.length < 2) return null;
  const take = Math.min(maxPts, coords.length);
  const step = (coords.length - 1) / Math.max(1, (take - 1));
  const parts = [];
  for (let i = 0; i < take; i++) {
    const idx = Math.round(i * step);
    const p = coords[Math.min(coords.length - 1, Math.max(0, idx))];
    if (!Array.isArray(p) || p.length < 2) continue;
    const lat = Number(p[0]);
    const lon = Number(p[1]);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue;
    parts.push(`${lat.toFixed(5)},${lon.toFixed(5)}`);
  }
  return parts.length >= 2 ? parts.join('|') : null;
};

/**
 * Leaflet tooltips open immediately on hover by default; this wrapper delays
 * mounting the Tooltip so users must dwell on the feature for a short time
 * before it appears.
 */
function HoverTooltip({ delayMs = 500, children, ...tooltipProps }) {
  const [enabled, setEnabled] = useState(false);

  useEffect(() => {
    const id = setTimeout(() => setEnabled(true), delayMs);
    return () => clearTimeout(id);
  }, [delayMs]);

  if (!enabled) return null;
  return <Tooltip {...tooltipProps}>{children}</Tooltip>;
}

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
 * @param {{ routeData: object, lineColor?: string, onRouteClick?: function }} props
 *   routeData — the { line, variants } object from the API / cache
 */
function SingleRouteLine({ routeData, onRouteClick, dashed = false }) {
  if (!routeData || !routeData.variants || routeData.variants.length === 0) {
    return null;
  }

  // If a variant geometry contains a huge discontinuity (often caused by
  // mismatched or jumbled upstream polylines), rendering it produces the
  // obvious "teleport" / Z-shaped line that covers the map. Detect and
  // suppress those geometries so we at least draw a sane stop-to-stop line.
  const isGeometrySane = (geom, stopPositions) => {
    if (!Array.isArray(geom) || geom.length < 2) return false;

    // Use a permissive threshold: derive a typical spacing from the stops
    // (median of consecutive stop gaps in rough metres) then allow large
    // multipliers. Also guard with an absolute cap.
    const toMeters = (a, b) => {
      if (!a || !b) return Infinity;
      const lat0 = a[0];
      const lon0 = a[1];
      const lat1 = b[0];
      const lon1 = b[1];
      const cos = Math.cos(((lat0 + lat1) / 2) * Math.PI / 180);
      const dlat = (lat1 - lat0) * 111_320;
      const dlon = (lon1 - lon0) * 111_320 * cos;
      return Math.sqrt(dlat * dlat + dlon * dlon);
    };

    const stopGaps = [];
    if (Array.isArray(stopPositions) && stopPositions.length >= 2) {
      for (let i = 0; i < stopPositions.length - 1; i++) {
        const d = toMeters(stopPositions[i], stopPositions[i + 1]);
        if (Number.isFinite(d) && d > 0) stopGaps.push(d);
      }
    }
    stopGaps.sort((a, b) => a - b);
    const medianStopGap = stopGaps.length ? stopGaps[Math.floor(stopGaps.length / 2)] : 400; // ~= urban spacing

    const ABS_MAX_GAP_M = 15_000; // if we jump >15km, it's definitely wrong
    const REL_MAX_GAP_M = Math.max(3_000, medianStopGap * 10);
    const threshold = Math.min(ABS_MAX_GAP_M, REL_MAX_GAP_M);

    let maxGap = 0;
    for (let i = 0; i < geom.length - 1; i++) {
      const d = toMeters(geom[i], geom[i + 1]);
      if (d > maxGap) maxGap = d;
      if (d > threshold) return false;
    }

    // Additional sanity: avoid bizarre geometries that never come near the stops.
    // (This is soft: only reject if it's way off.)
    if (Array.isArray(stopPositions) && stopPositions.length) {
      const firstStop = stopPositions[0];
      const lastStop = stopPositions[stopPositions.length - 1];
      if (toMeters(geom[0], firstStop) > 8_000 && toMeters(geom[geom.length - 1], lastStop) > 8_000) {
        return false;
      }
    }

    return maxGap > 0;
  };

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

        const hasSaneGeom = Boolean(variant.geometry && isGeometrySane(variant.geometry, stopPositions));
        const basePositions = hasSaneGeom
          ? chaikinSmooth(insertStopsIntoGeometry(variant.geometry, stopPositions), 1)
          : stopPositions;

        // Always try OSRM snapping when available; fall back silently.
        // Cache by a small key so we don't spam OSRM on re-renders.
        const [snapped, setSnapped] = useState(null);
        const snapKey = useMemo(
          () => buildSnapKey(basePositions),
          // eslint-disable-next-line react-hooks/exhaustive-deps
          [variant && variant.route_id, basePositions && basePositions.length, hasSaneGeom],
        );

        useEffect(() => {
          let cancelled = false;
          if (!snapKey || !Array.isArray(basePositions) || basePositions.length < 2) {
            setSnapped(null);
            return () => { cancelled = true; };
          }
          (async () => {
            try {
              const out = await osrmRouteCoordsLatLon(basePositions);
              if (cancelled) return;
              setSnapped((Array.isArray(out) && out.length >= 2) ? out : null);
            } catch (e) {
              if (cancelled) return;
              setSnapped(null);
            }
          })();
          return () => { cancelled = true; };
          // eslint-disable-next-line react-hooks/exhaustive-deps
        }, [snapKey]);

        const positions = (Array.isArray(snapped) && snapped.length >= 2) ? snapped : basePositions;
        
        
        if (positions.length < 2) return null;

  const color = VARIANT_COLORS[idx % VARIANT_COLORS.length];
  const dashArray = dashed || idx > 0 ? '8 6' : undefined;

        return (
          <React.Fragment key={`${routeData.line}-v${idx}`}>
            {/* Cyan underlay/frame so route variants have a cyan outline */}
            <Polyline
              positions={positions}
              eventHandlers={{
                click: (e) => {
                  try {
                    if (e && e.originalEvent) {
                      e.originalEvent.preventDefault();
                      e.originalEvent.stopPropagation();
                    }
                    if (e && typeof e.stopPropagation === 'function') {
                      e.stopPropagation();
                    }
                    if (onRouteClick) onRouteClick(routeData.line);
                  } catch (err) { /* ignore */ }
                }
              }}
              pathOptions={{
                color: '#00ffff',
                weight: 6,
                opacity: 0.9,
                dashArray,
              }}
            >
              <HoverTooltip sticky delayMs={500}>
                <strong style={{ fontSize: '1.2em' }}>Line {routeData.line}</strong>
              </HoverTooltip>
            </Polyline>

            {/* The route polyline (on top) */}
            <Polyline
              positions={positions}
              eventHandlers={{
                click: (e) => {
                  try {
                    if (e && e.originalEvent) {
                      e.originalEvent.preventDefault();
                      e.originalEvent.stopPropagation();
                    }
                    if (e && typeof e.stopPropagation === 'function') {
                      e.stopPropagation();
                    }
                    if (onRouteClick) onRouteClick(routeData.line);
                  } catch (err) { /* ignore */ }
                }
              }}
              pathOptions={{
                color,
                weight: 4,
                opacity: 0.8,
                dashArray, // dash secondary variants (and optionally primary)
              }}
            >
              <HoverTooltip sticky delayMs={500}>
                <strong style={{ fontSize: '1.2em' }}>Line {routeData.line}</strong>
              </HoverTooltip>
            </Polyline>
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
                  <HoverTooltip direction="top" offset={[0, -6]} delayMs={500}>
                    <span style={{ fontWeight: 600 }}>{stop.name}</span>
                  </HoverTooltip>
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
 * @param {{ activeRoutes: Map<string, object>, onRouteClick?: function }} props
 *   activeRoutes — from useRouteLine().activeRoutes
 */
export default function RouteLineLayer({ activeRoutes, onRouteClick }) {
  const entries = useMemo(
    () => Array.from(activeRoutes.entries()),
    [activeRoutes],
  );

  if (entries.length === 0) return null;

  return (
    <>
      {entries.map(([line, value]) => {
        const routeData = value && value.data ? value.data : value;
        const dashed = Boolean(value && value.style && value.style.dashed);
        return (
        <SingleRouteLine 
          key={line} 
          routeData={routeData} 
          dashed={dashed}
          onRouteClick={onRouteClick} 
        />
        );
      })}
    </>
  );
}

export { SingleRouteLine };
