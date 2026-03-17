/**
 * Service for fetching bus-route line data from the backend.
 *
 * Used by `useRouteLine` to populate polylines on the map when the
 * user clicks a line chip inside a bus-stop popup.
 */

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:5050';

// ── Mock route data (Lancaster / NW England area) ───────────────────
//
// Used as a fallback whenever the backend is unreachable so the
// route-line feature is testable without a running server.
// Stops follow realistic road corridors for each line.

export const MOCK_ROUTES = {
  '1': {
    line: '1',
    variants: [
      {
        route_id: 'mock-1-LancasterUni-to-Morecambe',
        stops: [
          { name: 'Lancaster University Underpass', lat: 54.0101, lon: -2.7852, atco_code: '2500B0615' },
          { name: 'Hazelrigg Lane', lat: 54.0185, lon: -2.7888, atco_code: '2500B0616' },
          { name: 'Scotforth (St Pauls)', lat: 54.0270, lon: -2.7930, atco_code: '2500B0902' },
          { name: 'Scotforth Road', lat: 54.0353, lon: -2.7971, atco_code: '2500B0901' },
          { name: 'Bowerham Road', lat: 54.0425, lon: -2.7952, atco_code: '2500B0801' },
          { name: 'Lancaster Bus Station', lat: 54.04895, lon: -2.80117, atco_code: '2500LAA12000' },
          { name: 'Lancaster Railway Station', lat: 54.04889, lon: -2.80750, atco_code: '2500LAA13200' },
          { name: 'Morecambe Road (Scale Hall)', lat: 54.0540, lon: -2.8210, atco_code: '2500B0301' },
          { name: 'Scale Hall Lane', lat: 54.0600, lon: -2.8390, atco_code: '2500B0302' },
          { name: 'Torrisholme Road', lat: 54.0660, lon: -2.8560, atco_code: '2500B0303' },
          { name: 'Morecambe Bus Station', lat: 54.0723, lon: -2.8696, atco_code: '2500MOR0001' },
        ],
      },
    ],
  },

  '100': {
    line: '100',
    variants: [
      {
        route_id: 'mock-100-LancasterUni-to-Bus-Station',
        stops: [
          { name: 'Lancaster University Underpass', lat: 54.0101, lon: -2.7852, atco_code: '2500B0615' },
          { name: 'Hazelrigg Lane', lat: 54.0185, lon: -2.7888, atco_code: '2500B0616' },
          { name: 'Scotforth (St Pauls)', lat: 54.0270, lon: -2.7930, atco_code: '2500B0902' },
          { name: 'Scotforth Road', lat: 54.0353, lon: -2.7971, atco_code: '2500B0901' },
          { name: 'Bowerham Road', lat: 54.0425, lon: -2.7952, atco_code: '2500B0801' },
          { name: 'Lancaster Bus Station', lat: 54.04895, lon: -2.80117, atco_code: '2500LAA12000' },
        ],
      },
    ],
  },

  '40': {
    line: '40',
    variants: [
      {
        route_id: 'mock-40-Lancaster-to-Galgate',
        stops: [
          { name: 'Lancaster Bus Station', lat: 54.04895, lon: -2.80117, atco_code: '2500LAA12000' },
          { name: 'Hala Square', lat: 54.04310, lon: -2.78430, atco_code: '2500B0701' },
          { name: 'Aldcliffe Road', lat: 54.0380, lon: -2.7940, atco_code: '2500B0702' },
          { name: 'Galgate Village', lat: 54.01780, lon: -2.78980, atco_code: '2500B1101' },
        ],
      },
    ],
  },
};

/**
 * Fetch all route variants for a given bus line name.
 * Throws on any network or HTTP error.
 *
 * @param {string} line  – the line name, e.g. "100" or "1A"
 * @returns {Promise<{ line: string, variants: Array<{ route_id: string, stops: Array }> }>}
 */
export async function fetchRouteLine(line, opts = {}) {
  let url = `${API_BASE}/routes/line/${encodeURIComponent(line)}`;
  try {
    const lat = opts && opts.lat != null ? Number(opts.lat) : null;
    const lon = opts && opts.lon != null ? Number(opts.lon) : null;
    if (Number.isFinite(lat) && Number.isFinite(lon)) {
      const qs = new URLSearchParams({ lat: String(lat), lon: String(lon) });
      url += `?${qs.toString()}`;
    }
  } catch (e) {
    // ignore
  }
  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(`Failed to fetch route line "${line}": ${res.status}`);
  }
  return res.json();
}

/**
 * Fetch route variants for a line restricted to routes that serve a stop.
 * Throws on any network or HTTP error.
 *
 * @param {string} atcoCode
 * @param {string} line
 * @returns {Promise<{ line: string, variants: Array }>} 
 */
export async function fetchRouteLineAtStop(atcoCode, line) {
  const atcoKey = String(atcoCode || '').trim();
  if (!atcoKey) throw new Error('Missing atcoCode');
  const url = `${API_BASE}/routes/line_at_stop/${encodeURIComponent(atcoKey)}/${encodeURIComponent(line)}`;
  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(`Failed to fetch route line "${line}" at stop "${atcoKey}": ${res.status}`);
  }
  return res.json();
}

/**
 * Fetch route line data with a mock fallback.
 *
 * When the backend is unreachable (network error or non-OK HTTP status)
 * the function returns the matching entry from {@link MOCK_ROUTES} when
 * available, or an empty-variants object when the requested line has no
 * mock data.  This keeps the feature fully testable without a live server.
 *
 * @param {string} line  – the line name, e.g. "100" or "1A"
 * @returns {Promise<{ line: string, variants: Array }>}
 */
export async function fetchRouteLineWithFallback(line, opts = {}) {
  // Try the real backend but don't block the UI forever — use a 5s
  // timeout. Only after the timeout or an explicit network failure do
  // we fall back to the mock data. This avoids instant fallback that
  // masks transient backend availability during startup.
  const controller = new AbortController();
  const timeoutMs = 5000;
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    let url = `${API_BASE}/routes/line/${encodeURIComponent(line)}`;
    try {
      const lat = opts && opts.lat != null ? Number(opts.lat) : null;
      const lon = opts && opts.lon != null ? Number(opts.lon) : null;
      if (Number.isFinite(lat) && Number.isFinite(lon)) {
        const qs = new URLSearchParams({ lat: String(lat), lon: String(lon) });
        url += `?${qs.toString()}`;
      }
    } catch (e) {
      // ignore
    }
    const res = await fetch(url, { signal: controller.signal });
    clearTimeout(timeout);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (err) {
    // If fetch failed or timed out, fall back to mock data but log the cause.
    const msg = err && err.name === 'AbortError' ? `timed out after ${timeoutMs}ms` : (err && err.message) ? err.message : String(err);
    // eslint-disable-next-line no-console
    console.warn(`Route line API unavailable for "${line}" (${msg}), using mock data`);

    // For ambiguous short lines ("1", "2", ...), mock fallback can be misleading
    // once the backend requires geo context. Only apply this stricter behavior
    // for *network-ish* failures (including timeouts). For HTTP errors we keep
    // the historical behavior of falling back to mock routes so tests/dev mode
    // stay predictable.
    const isHttpError = err && typeof err.message === 'string' && err.message.startsWith('HTTP ');
    const isAmbiguous = typeof line === 'string' && !line.includes(':') && /^\d{1,3}[A-Z]?$/.test(line.trim());
    const hasGeo = !!(opts && Number.isFinite(Number(opts.lat)) && Number.isFinite(Number(opts.lon)));
    if (!isHttpError && isAmbiguous && !hasGeo) {
      return { line, variants: [] };
    }

    return MOCK_ROUTES[line] ?? { line, variants: [] };
  }
}

/**
 * Fetch route line data but reject on network errors or non-OK HTTP statuses.
 * This variant does NOT fall back to mock data — callers can use this when
 * they need to treat failures (including timeouts) as errors.
 *
 * @param {string} line
 * @param {object} [opts] - optional settings: { timeoutMs: number, signal: AbortSignal }
 */
export async function fetchRouteLineNoFallback(line, opts = {}) {
  const timeoutMs = typeof opts.timeoutMs === 'number' ? opts.timeoutMs : 5000;
  const controller = new AbortController();
  const signal = opts.signal || controller.signal;

  // if the caller didn't provide a signal, create an internal timeout
  let timeout = null;
  if (!opts.signal) {
    timeout = setTimeout(() => controller.abort(), timeoutMs);
  }

  try {
    const res = await fetch(`${API_BASE}/routes/line/${encodeURIComponent(line)}`, { signal });
    if (timeout) clearTimeout(timeout);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (err) {
    if (timeout) clearTimeout(timeout);
    throw err;
  }
}

/**
 * Fetch a short label/metadata for a route. This calls the backend's
 * `/routes/label/:line` endpoint and throws on failure or timeout.
 *
 * @param {string} line
 * @param {object} [opts] - optional settings: { timeoutMs: number, signal: AbortSignal }
 */
export async function fetchRouteLabel(line, opts = {}) {
  const timeoutMs = typeof opts.timeoutMs === 'number' ? opts.timeoutMs : 5000;
  const controller = new AbortController();
  const signal = opts.signal || controller.signal;

  let timeout = null;
  if (!opts.signal) {
    timeout = setTimeout(() => controller.abort(), timeoutMs);
  }

  try {
    let url = `${API_BASE}/routes/label/${encodeURIComponent(line)}`;
    try {
      const lat = opts && opts.lat != null ? Number(opts.lat) : null;
      const lon = opts && opts.lon != null ? Number(opts.lon) : null;
      if (Number.isFinite(lat) && Number.isFinite(lon)) {
        const qs = new URLSearchParams({ lat: String(lat), lon: String(lon), strict_geo: '1' });
        url += `?${qs.toString()}`;
      }
    } catch (e) {
      // ignore query param construction errors
    }

    const res = await fetch(url, { signal });
    if (timeout) clearTimeout(timeout);
    // Treat 404 as "no label available" (not a fatal error) so callers
    // that only need geometry can proceed. Other HTTP errors remain fatal.
    if (res.status === 404) return null;
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (err) {
    if (timeout) clearTimeout(timeout);
    throw err;
  }
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
