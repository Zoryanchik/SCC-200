/**
 * Service for fetching bus-route line data from the backend.
 *
 * Used by `useRouteLine` to populate polylines on the map when the
 * user clicks a line chip inside a bus-stop popup.
 */

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:5050';

/**
 * Fetch all route variants for a given bus line name.
 *
 * @param {string} line  – the line name, e.g. "100" or "1A"
 * @returns {Promise<{ line: string, variants: Array<{ route_id: string, stops: Array }> }>}
 */
export async function fetchRouteLine(line) {
  const res = await fetch(`${API_BASE}/routes/line/${encodeURIComponent(line)}`);
  if (!res.ok) {
    throw new Error(`Failed to fetch route line "${line}": ${res.status}`);
  }
  return res.json();
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
