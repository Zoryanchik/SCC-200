import React, { useEffect, useMemo, useState } from "react";
import { Polyline, CircleMarker, Tooltip } from "react-leaflet";
import { stopsToLatLngs } from "../../services/routeLineApi";

// Best-effort OSRM snapping for route overlays.
// Input/Output coords are [lat, lon].
//
// By default we call the backend proxy to avoid DNS/CORS issues when OSRM is
// running on a docker-compose hostname (not resolvable from the browser).
// Advanced: override VITE_OSRM_BASE to hit OSRM directly.
const OSRM_BASE =
  typeof import.meta !== "undefined" &&
  import.meta.env &&
  import.meta.env.VITE_OSRM_BASE
    ? String(import.meta.env.VITE_OSRM_BASE)
    : null;

const API_BASE =
  typeof import.meta !== "undefined" &&
  import.meta.env &&
  import.meta.env.VITE_API_BASE_URL
    ? String(import.meta.env.VITE_API_BASE_URL)
    : "";

const osrmMatchCoordsLatLon = async (
  coordsLatLon,
  { profile = "driving", timeoutMs = 6000 } = {},
) => {
  try {
    if (!Array.isArray(coordsLatLon) || coordsLatLon.length < 2)
      return coordsLatLon;
    const norm = [];
    for (const pt of coordsLatLon) {
      if (!Array.isArray(pt) || pt.length < 2) continue;
      const lat = Number(pt[0]);
      const lon = Number(pt[1]);
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue;
      norm.push([lat, lon]);
    }
    if (norm.length < 2) return coordsLatLon;

    const matchUrl = `${API_BASE.replace(/\/$/, "")}/osrm/match`;

    // Match snapping via backend POST (avoids URL length limits for dense traces).
    const controller2 = new AbortController();
    const t2 = setTimeout(() => controller2.abort(), timeoutMs);
    let resp;
    try {
      resp = await fetch(matchUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ coords: norm, profile }),
        signal: controller2.signal,
      });
    } finally {
      clearTimeout(t2);
    }
    if (!resp || !resp.ok) return coordsLatLon;
    const data = await resp.json();
    const osrmCoords =
      data &&
      data.matchings &&
      data.matchings[0] &&
      data.matchings[0].geometry &&
      data.matchings[0].geometry.coordinates;
    if (!Array.isArray(osrmCoords) || osrmCoords.length < 2)
      return coordsLatLon;
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
  const step = (coords.length - 1) / Math.max(1, take - 1);
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
  return parts.length >= 2 ? parts.join("|") : null;
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
  "#1976d2", // blue
  "#d32f2f", // red
  "#388e3c", // green
  "#7b1fa2", // purple
  "#f57c00", // orange
  "#0097a7", // teal
  "#c2185b", // pink
  "#455a64", // blue-grey
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
      const cos = Math.cos((((lat0 + lat1) / 2) * Math.PI) / 180);
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
    const medianStopGap = stopGaps.length
      ? stopGaps[Math.floor(stopGaps.length / 2)]
      : 400; // ~= urban spacing

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
      if (
        toMeters(geom[0], firstStop) > 8_000 &&
        toMeters(geom[geom.length - 1], lastStop) > 8_000
      ) {
        return false;
      }
    }

    return maxGap > 0;
  };

  return (
    <>
      {routeData.variants.map((variant, idx) => {
        // Prefer stored route geometry when available; fall back to straight
        // stop-to-stop lines.
        // IMPORTANT:
        // - Only apply OSRM snapping to *real track* geometry returned by the backend
        //   (geometry_source === 'route_link_tracks').
        // - Never OSRM-snap stop-derived fallback geometry.
        // - Do not inject/force stops onto the track; render track exactly as returned.
        const stopPositions = stopsToLatLngs(variant.stops);
        // If the backend returns no variant geometry, fall back to stop-to-stop.
        // This is important for "line clicked from stop" flows where we may
        // only have stop coordinates.
        const geomSource =
          variant && variant.geometry_source != null
            ? String(variant.geometry_source).toLowerCase()
            : null;
        const isTrackGeom = geomSource === "route_link_tracks";
        const hasSaneGeom = Boolean(
          isTrackGeom &&
          Array.isArray(variant.geometry) &&
          variant.geometry.length >= 2 &&
          isGeometrySane(variant.geometry, stopPositions),
        );
        const basePositions = hasSaneGeom ? variant.geometry : stopPositions;

        // Only snap when we have a real track polyline.
        // If we only have stop-to-stop coords, keep it as a straight line.
        // Cache by a small key so we don't spam OSRM on re-renders.
        //
        // NOTE: We key the snapping effect by a sampled signature of the polyline.
        // In practice, route data can update without a length change (e.g. refreshed
        // geometry points), so we must not depend only on `length`.
        const [snapped, setSnapped] = useState(null);
        const snapKey = useMemo(
          () => buildSnapKey(basePositions),
          [basePositions],
        );

        const osrmProfile = useMemo(() => {
          const raw =
            variant?.mode ??
            variant?.access_mode ??
            variant?.travel_mode ??
            routeData?.mode ??
            routeData?.access_mode ??
            routeData?.travel_mode ??
            null;
          const m = raw == null ? "" : String(raw).toLowerCase();
          return m === "walking" || m === "walk" || m === "foot"
            ? "foot"
            : "driving";
        }, [variant, routeData]);

        useEffect(() => {
          let cancelled = false;
          if (!hasSaneGeom) {
            setSnapped(null);
            return () => {
              cancelled = true;
            };
          }
          if (
            !snapKey ||
            !Array.isArray(basePositions) ||
            basePositions.length < 2
          ) {
            setSnapped(null);
            return () => {
              cancelled = true;
            };
          }
          (async () => {
            try {
              const out = await osrmMatchCoordsLatLon(basePositions, {
                profile: osrmProfile,
              });
              if (cancelled) return;
              setSnapped(Array.isArray(out) && out.length >= 2 ? out : null);
            } catch (e) {
              if (cancelled) return;
              setSnapped(null);
            }
          })();
          return () => {
            cancelled = true;
          };
          // eslint-disable-next-line react-hooks/exhaustive-deps
        }, [hasSaneGeom, snapKey, basePositions.length, osrmProfile]);

        const positions =
          Array.isArray(snapped) && snapped.length >= 2
            ? snapped
            : basePositions;

        if (positions.length < 2) return null;

        const color = VARIANT_COLORS[idx % VARIANT_COLORS.length];
        const dashArray = dashed || idx > 0 ? "8 6" : undefined;

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
                    if (e && typeof e.stopPropagation === "function") {
                      e.stopPropagation();
                    }
                    if (onRouteClick) onRouteClick(routeData.line);
                  } catch (err) {
                    /* ignore */
                  }
                },
              }}
              pathOptions={{
                color: "#00ffff",
                weight: 6,
                opacity: 0.9,
                dashArray,
                pane: "routePane",
              }}
            >
              <HoverTooltip sticky delayMs={500}>
                <strong style={{ fontSize: "1.2em" }}>
                  Line {routeData.line}
                </strong>
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
                    if (e && typeof e.stopPropagation === "function") {
                      e.stopPropagation();
                    }
                    if (onRouteClick) onRouteClick(routeData.line);
                  } catch (err) {
                    /* ignore */
                  }
                },
              }}
              pathOptions={{
                color,
                weight: 4,
                opacity: 0.8,
                dashArray, // dash secondary variants (and optionally primary)
                pane: "routePane",
              }}
            >
              <HoverTooltip sticky delayMs={500}>
                <strong style={{ fontSize: "1.2em" }}>
                  Line {routeData.line}
                </strong>
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
                    color: "#fff",
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
