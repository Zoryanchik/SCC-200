/**
 * Bus Stops API Service
 *
 * Fetches geo-enriched classified bus stop data from the backend
 * ``GET /stops/geo`` endpoint.  Falls back to a curated set of mock
 * stops when the API is unavailable so the map always renders stop
 * markers for development / demo purposes.
 *
 * Backend shape (GET /stops/geo):
 *   [{ id, name, lat, lon, atco_code, classification, lines,
 *      degree, frequency }]
 *
 * The unified BusStop shape consumed by components is:
 *   { id, name, lat, lon, atco_code, classification, lines }
 *
 * "lines" is an array of route/line identifiers that serve the stop.
 */

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:5050';

// ── Mock bus stops (Lancaster / NW England area) ────────────────────
// These are real-world stops with plausible coordinates so the map
// always has something to render even without a running backend.
export const MOCK_BUS_STOPS = [
  {
    id: 'mock-1',
    name: 'Lancaster Bus Station',
    lat: 54.04895,
    lon: -2.80117,
    atco_code: '2500LAA12000',
    classification: 'hub',
    lines: ['1', '2', '2A', '4', '40', '42', '100'],
  },
  {
    id: 'mock-2',
    name: 'Lancaster University Underpass',
    lat: 54.0101,
    lon: -2.7852,
    atco_code: '2500B0615',
    classification: 'interchange',
    lines: ['1', '2', '2A', '4', '40', '100'],
  },
  {
    id: 'mock-3',
    name: 'Lancaster Railway Station',
    lat: 54.04889,
    lon: -2.80750,
    atco_code: '2500LAA13200',
    classification: 'interchange',
    lines: ['1', '2A', '42'],
  },
  {
    id: 'mock-4',
    name: 'Morecambe Bus Station',
    lat: 54.07230,
    lon: -2.86960,
    atco_code: '2500MOR0001',
    classification: 'hub',
    lines: ['2', '2A', '5', '6'],
  },
  {
    id: 'mock-5',
    name: 'Williamson Park',
    lat: 54.05560,
    lon: -2.78600,
    atco_code: '2500B0501',
    classification: 'local',
    lines: ['18', '19'],
  },
  {
    id: 'mock-6',
    name: 'Hala Square',
    lat: 54.04310,
    lon: -2.78430,
    atco_code: '2500B0701',
    classification: 'local',
    lines: ['4'],
  },
  {
    id: 'mock-7',
    name: 'Galgate Village',
    lat: 54.01780,
    lon: -2.78980,
    atco_code: '2500B1101',
    classification: 'local',
    lines: ['40', '41', '42'],
  },
  {
    id: 'mock-8',
    name: 'Carnforth Market Street',
    lat: 54.13020,
    lon: -2.77140,
    atco_code: '2500CAR0001',
    classification: 'interchange',
    lines: ['49', '55', '555'],
  },
  {
    id: 'mock-9',
    name: 'Heysham Village',
    lat: 54.04730,
    lon: -2.89200,
    atco_code: '2500HEY0001',
    classification: 'request_stop',
    lines: ['5'],
  },
  {
    id: 'mock-10',
    name: 'Bowerham Road',
    lat: 54.04250,
    lon: -2.79520,
    atco_code: '2500B0801',
    classification: 'local',
    lines: ['4', '18'],
  },
  {
    id: 'mock-11',
    name: 'Scotforth Road',
    lat: 54.03530,
    lon: -2.79710,
    atco_code: '2500B0901',
    classification: 'local',
    lines: ['1', '100'],
  },
  {
    id: 'mock-12',
    name: 'Bulk Road',
    lat: 54.05620,
    lon: -2.79420,
    atco_code: '2500B0601',
    classification: 'request_stop',
    lines: ['19'],
  },
];

/**
 * Normalise a raw stop from /stops/classify into the unified shape.
 * The backend endpoint returns {stop_index, name, degree, frequency,
 * interchange, lines, classification} but does NOT include lat/lon
 * directly — those come from merged-data stop coords. When the API
 * enriches the response in the future, lat/lon will already be present.
 *
 * @param {Object} raw - Raw stop object from API
 * @param {number} idx - Fallback index for id generation
 * @returns {Object|null} Normalised bus stop or null if unusable
 */
const normaliseStop = (raw, idx) => {
  if (!raw || typeof raw !== 'object') return null;

  const lat = raw.lat ?? raw.latitude;
  const lon = raw.lon ?? raw.longitude;
  if (typeof lat !== 'number' || typeof lon !== 'number') return null;

  return {
    id: raw.atco_code || raw.id || `stop-${idx}`,
    name: raw.name || 'Unknown Stop',
    lat,
    lon,
    atco_code: raw.atco_code || null,
    classification: raw.classification || 'local',
    lines: Array.isArray(raw.lines)
      ? raw.lines
      : typeof raw.lines === 'string'
        ? raw.lines.split(',').map((s) => s.trim()).filter(Boolean)
        : [],
  };
};

/**
 * Fetch bus stops with coordinates from the backend.
 *
 * Uses ``GET /stops/geo`` which returns classified stops already
 * enriched with lat/lon from NaPTAN data.
 *
 * @param {Object} [options]
 * @param {string} [options.classification] - Filter by class
 * @param {string} [options.bbox]           - Viewport "south,west,north,east"
 * @returns {Promise<Array>} Array of normalised bus stop objects
 */
export const fetchBusStops = async ({ classification, bbox } = {}) => {
  const params = new URLSearchParams();
  if (classification) {
    params.set('classification', classification);
  }
  if (bbox) {
    params.set('bbox', bbox);
  }

  const qs = params.toString();
  const url = `${API_BASE_URL}/stops/geo${qs ? `?${qs}` : ''}`;

  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`);
  }
  const data = await response.json();
  if (!Array.isArray(data)) {
    throw new Error('Unexpected response format');
  }
  return data.map(normaliseStop).filter(Boolean);
};

/**
 * Fetch all classified stops from /stops/classify.
 * Returns enriched data including degree/frequency/interchange metrics.
 *
 * NOTE: This endpoint does not currently include lat/lon. When it does
 * in a future update, this function will be the primary data source.
 *
 * @param {string} [classification] - Optional classification filter
 * @returns {Promise<Array>} Array of raw classified stop objects
 */
export const fetchClassifiedStops = async (classification) => {
  let url = `${API_BASE_URL}/stops/classify`;
  if (classification) {
    url += `?classification=${encodeURIComponent(classification)}`;
  }
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`);
  }
  return response.json();
};

/**
 * Get bus stops, falling back to mock data when the API is unavailable.
 *
 * @param {Object} [options]
 * @param {string} [options.classification] - Optional filter
 * @param {number} [options.limit=200]
 * @returns {Promise<Array>} Bus stops (real or mock)
 */
export const getBusStopsWithFallback = async (options = {}) => {
  try {
    const stops = await fetchBusStops(options);
    if (stops.length > 0) return stops;
  } catch (err) {
    // API unavailable — fall through to mock data
    console.warn('Bus stops API unavailable, using mock data:', err.message);
  }

  // Return mock data, optionally filtered by classification
  let mocks = MOCK_BUS_STOPS;
  if (options.classification) {
    mocks = mocks.filter((s) => s.classification === options.classification);
  }
  return mocks;
};
