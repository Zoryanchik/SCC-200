/**
 * Service for fetching bus-route line data from the backend.
 *
 * Used by `useRouteLine` to populate polylines on the map when the
 * user clicks a line chip inside a bus-stop popup.
 */

import { resolveOperatorCode } from './operatorMap';

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:5050';

/**
 * Fetch all route variants for a given bus line name.
 *
 * @param {string} line      – the line name, e.g. "100" or "1A"
 * @param {string} [operator] – optional SIRI operator code, e.g. "SCCU"
 * @returns {Promise<{ line: string, variants: Array<{ route_id: string, stops: Array }> }>}
 */
export async function fetchRouteLine(line, operator = null, opts = {}) {
  // Debug: log the operator requested so we can trace incorrect matches
  try {
    console.debug(`[routeLineApi] fetchRouteLine requested: line='${line}' operator='${operator}'`);
  } catch (e) {
    // ignore logging failures in test environments
  }
  // Defensive: resolve human-friendly operator display names to SIRI codes
  let resolvedOp = null;
  try {
    resolvedOp = resolveOperatorCode(operator);
    if (operator && !resolvedOp) {
      console.debug(`[routeLineApi] operator '${operator}' could not be resolved to a SIRI code`);
    } else if (operator && resolvedOp && resolvedOp !== operator) {
      console.debug(`[routeLineApi] resolved operator '${operator}' -> '${resolvedOp}'`);
    }
  } catch (e) {
    // ignore resolution errors
  }
  // If the caller explicitly requested a journey-ordered geometry
  // (opts.journeyId) prefer the by-journey endpoint. Otherwise, if
  // the caller passed a full authoritative route id (contains ':'),
  // request the by-id endpoint. Failing that use the line-based
  // endpoint which returns variants for a short line name.
  let url;
  if (opts && typeof opts.journeyId === 'string' && opts.journeyId.trim()) {
    url = `${API_BASE}/routes/by-journey/${encodeURIComponent(String(opts.journeyId).trim())}`;
  } else if (String(line).includes(':')) {
    url = `${API_BASE}/routes/by-id/${encodeURIComponent(line)}`;
  } else {
    url = `${API_BASE}/routes/line/${encodeURIComponent(line)}`;
    const opToSend = resolvedOp || operator || null;
    if (opToSend) {
      url += `?operator=${encodeURIComponent(opToSend)}`;
    }
  }
  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(`Failed to fetch route line "${line}": ${res.status}`);
  }
  const body = await res.json();
  try {
    // Small debug aid: show which route_ids came back for this fetch
    console.debug(`[routeLineApi] fetched ${Array.isArray(body.variants) ? body.variants.length : 0} variants for line='${line}' operator='${opToSend}'`,
      (Array.isArray(body.variants) && body.variants.map(v => v.route_id)));
  } catch (e) {
    // ignore logging failures
  }
  return body;
}

/**
 * Convert an array of stop objects `{ lat, lon, … }` into
 * `[[lat, lon], …]` pairs that Leaflet Polyline understands.
 *
 * @param {Array<{ lat: number, lon: number }>} stops
 * @returns {Array<[number, number]>}
 */
export function stopsToLatLngs(stops) {
  if (!Array.isArray(stops)) return [];
  return stops
    .filter((s) => s.lat != null && s.lon != null)
    .map((s) => [s.lat, s.lon]);
}
