/**
 * transportApi.js — contract tests against backend schemas
 *
 * These tests validate that each service function in transportApi.js
 * correctly handles the exact JSON response shapes returned by the
 * FastAPI backend (api.py).  Mock data mirrors the schemas documented
 * in INTEGRATION_SCHEMAS.md.
 *
 * Test strategy:
 *  - globalThis.fetch is stubbed per-test to return backend-shaped JSON.
 *  - Each test asserts the caller receives the correctly structured data.
 *  - Error paths (non-200, network failure) are covered.
 */

import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  fetchLiveBusLocations,
  searchStops,
  getJourneyPlans,
  fetchRailDepartures,
  fetchWeatherData,
  fetchServiceAlerts,
  fetchPricing,
  fetchBusTimes,
  fetchBusArrivals,
} from '../transportApi';

// ---- helpers ---------------------------------------------------------------

const jsonResponse = (body, status = 200) =>
  Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
    headers: new Headers({ 'content-type': 'application/json' }),
  });

const networkError = () => Promise.reject(new TypeError('Failed to fetch'));

// ---- setup -----------------------------------------------------------------

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn());
});

afterEach(() => {
  vi.restoreAllMocks();
});

// ---- GET /search/stops contract -------------------------------------------

describe('searchStops — GET /search/stops', () => {
  const MOCK_STOPS = [
    { id: 1, name: 'Lancaster Bus Station', atco_code: 'LAN001', lat: 54.048, lon: -2.801, type: 'stop' },
    { id: 'loc:0', name: 'Lancaster, Lancashire, UK', atco_code: null, lat: 54.047, lon: -2.801, type: 'location' },
  ];

  test('returns array of stop/location objects with correct fields', async () => {
    fetch.mockReturnValueOnce(jsonResponse(MOCK_STOPS));

    const result = await searchStops('Lancaster');
    expect(result).toEqual(MOCK_STOPS);
    expect(result[0]).toHaveProperty('type', 'stop');
    expect(result[1]).toHaveProperty('type', 'location');
    expect(result[1].atco_code).toBeNull();
  });

  test('passes query to URL', async () => {
    fetch.mockReturnValueOnce(jsonResponse([]));
    await searchStops('cent');
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/search/stops?q=cent'));
  });

  test('returns empty array when query is empty', async () => {
    fetch.mockReturnValueOnce(jsonResponse([]));
    const result = await searchStops('');
    expect(result).toEqual([]);
  });

  test('throws on HTTP error', async () => {
    fetch.mockReturnValueOnce(jsonResponse({ error: 'Backend not initialized' }, 503));
    await expect(searchStops('test')).rejects.toThrow('503');
  });

  test('stop results include lat/lon for map placement', async () => {
    fetch.mockReturnValueOnce(jsonResponse(MOCK_STOPS));
    const result = await searchStops('Lancaster');
    for (const item of result) {
      expect(typeof item.lat).toBe('number');
      expect(typeof item.lon).toBe('number');
    }
  });
});

// ---- GET /bus/live/{operator} contract ------------------------------------

describe('fetchLiveBusLocations — GET /bus/live/{operator}', () => {
  const MOCK_BUSES = [
    { line: '1A', destination: 'Lancaster', lat: 54.05, lon: -2.80 },
    { line: '2B', destination: 'Morecambe', lat: 54.07, lon: -2.85 },
  ];

  test('returns array of {line, destination, lat, lon}', async () => {
    fetch.mockReturnValueOnce(jsonResponse(MOCK_BUSES));

    const result = await fetchLiveBusLocations('SCCU', { lat: 54.05, lon: -2.80 });
    expect(result).toEqual(MOCK_BUSES);
    expect(result[0]).toHaveProperty('line');
    expect(result[0]).toHaveProperty('destination');
    expect(result[0]).toHaveProperty('lat');
    expect(result[0]).toHaveProperty('lon');
  });

  test('constructs URL with lat, lon, latTol, lonTol params', async () => {
    fetch.mockReturnValueOnce(jsonResponse([]));
    await fetchLiveBusLocations('SCCU', { lat: 54.05, lon: -2.80, latTol: 0.01, lonTol: 0.02 });

    const url = fetch.mock.calls[0][0];
    expect(url).toContain('/bus/live/SCCU');
    expect(url).toContain('lat=54.05');
    expect(url).toContain('lon=-2.8');
    expect(url).toContain('latTol=0.01');
    expect(url).toContain('lonTol=0.02');
  });

  test('calls without query params when lat/lon not provided', async () => {
    fetch.mockReturnValueOnce(jsonResponse([]));
    await fetchLiveBusLocations('SCCU');

    const url = fetch.mock.calls[0][0];
    expect(url).toContain('/bus/live/SCCU');
    expect(url).not.toContain('?');
  });

  test('returns empty array when no buses found', async () => {
    fetch.mockReturnValueOnce(jsonResponse([]));
    const result = await fetchLiveBusLocations('SCCU', { lat: 0, lon: 0 });
    expect(result).toEqual([]);
  });

  test('throws on server error', async () => {
    fetch.mockReturnValueOnce(jsonResponse({ error: 'Internal' }, 500));
    await expect(fetchLiveBusLocations('SCCU', { lat: 54, lon: -2 })).rejects.toThrow('500');
  });

  test('throws on network failure', async () => {
    fetch.mockReturnValueOnce(networkError());
    await expect(fetchLiveBusLocations('SCCU', { lat: 54, lon: -2 })).rejects.toThrow();
  });
});

// ---- POST /journey/plan contract ------------------------------------------

describe('getJourneyPlans — POST /journey/plan', () => {
  // Mock reflects exact JSON returned by api.py build_journey_plan_response().
  // Legs use the 'mode' field ("walking" | "bus" | "train").
  // getJourneyPlans() normalises mode → type ("walking"→"walk") so consumers
  // can read leg.type === 'walk' | 'bus' | 'train'.
  const MOCK_JOURNEY_SUCCESS = {
    success: true,
    legs: [
      {
        mode: 'walking',
        from_stop: { name: 'Start', lat: 54.048, lon: -2.801 },
        to_stop: { name: 'Lancaster Bus Station', lat: 54.049, lon: -2.800 },
        duration_seconds: 120,
        departure_time: null,
        arrival_time: '10:02:00',
      },
      {
        mode: 'bus',
        from_stop: { name: 'Lancaster Bus Station', lat: 54.049, lon: -2.800 },
        to_stop: { name: 'Preston Bus Station', lat: 53.759, lon: -2.699 },
        duration_seconds: 1800,
        departure_time: '10:05:00',
        arrival_time: '10:35:00',
        line_name: '40',
        journey_origin: 'Lancaster',
        journey_destination: 'Preston',
      },
    ],
    meta: {
      start_walk_seconds: 120,
      end_walk_seconds: 0,
      total_arrival: '10:35:00',
      start_point: [54.048, -2.801],
      destination: [53.759, -2.699],
    },
    routeGeometries: [
      { id: 'walk-0', name: 'Walk to Lancaster Bus Station', coords: [[54.048, -2.801], [54.049, -2.800]], color: '#888888' },
      { id: 'bus-1', name: 'Bus 40', coords: [[54.049, -2.800], [53.759, -2.699]], color: '#1a73e8' },
    ],
  };

  const MOCK_JOURNEY_ERROR = {
    success: false,
    error: 'No route found',
    legs: null,
    meta: null,
    routeGeometries: null,
  };

  const MOCK_JOURNEY_WALKING_ONLY = {
    success: true,
    legs: [
      {
        mode: 'walking',
        from_stop: { lat: 54.048, lon: -2.801 },
        to_stop: { lat: 54.050, lon: -2.799 },
        duration_seconds: 300,
        departure_time: null,
        arrival_time: '10:05:00',
      },
    ],
    meta: {
      start_walk_seconds: 150,
      end_walk_seconds: 150,
      total_arrival: '10:05:00',
    },
    routeGeometries: [
      { id: 'walk-0', name: 'Walking', coords: [[54.048, -2.801], [54.050, -2.799]], color: '#888888' },
    ],
  };

  test('sends POST with correct body shape (fromStop, toStop, departureTime, date)', async () => {
    fetch.mockReturnValueOnce(jsonResponse(MOCK_JOURNEY_SUCCESS));

    await getJourneyPlans(
      { lat: 54.048, lon: -2.801 },
      { lat: 53.759, lon: -2.699 },
      '2026-02-19T10:00:00Z'
    );

    const [url, options] = fetch.mock.calls[0];
    expect(url).toContain('/journey/plan');
    expect(options.method).toBe('POST');

    const body = JSON.parse(options.body);
    expect(body.fromStop).toEqual({ lat: 54.048, lon: -2.801 });
    expect(body.toStop).toEqual({ lat: 53.759, lon: -2.699 });
    expect(body).toHaveProperty('departureTime');
    expect(body).toHaveProperty('date');
  });

  test('returns journey plan with legs, meta, and routeGeometries', async () => {
    fetch.mockReturnValueOnce(jsonResponse(MOCK_JOURNEY_SUCCESS));

    const result = await getJourneyPlans(
      { lat: 54.048, lon: -2.801 },
      { lat: 53.759, lon: -2.699 },
      '2026-02-19T10:00:00Z'
    );

    expect(result.success).toBe(true);
    expect(result.legs).toHaveLength(2);
    expect(result.routeGeometries).toHaveLength(2);
    expect(result.meta).toHaveProperty('start_walk_seconds');
    expect(result.meta).toHaveProperty('total_arrival');
  });

  test('leg object has required fields: mode, type (normalised), from_stop, to_stop, arrival_time', async () => {
    fetch.mockReturnValueOnce(jsonResponse(MOCK_JOURNEY_SUCCESS));

    const result = await getJourneyPlans(
      { lat: 54.048, lon: -2.801 },
      { lat: 53.759, lon: -2.699 },
      '2026-02-19T10:00:00Z'
    );

    for (const leg of result.legs) {
      // 'mode' is the raw backend field; 'type' is the normalised field
      // added by getJourneyPlans() so UI components can read leg.type.
      expect(leg).toHaveProperty('mode');
      expect(leg).toHaveProperty('type');
      expect(leg).toHaveProperty('from_stop');
      expect(leg).toHaveProperty('to_stop');
      expect(leg).toHaveProperty('arrival_time');
    }
  });

  test('transit legs include line_name, journey_origin, journey_destination', async () => {
    fetch.mockReturnValueOnce(jsonResponse(MOCK_JOURNEY_SUCCESS));

    const result = await getJourneyPlans(
      { lat: 54.048, lon: -2.801 },
      { lat: 53.759, lon: -2.699 },
      '2026-02-19T10:00:00Z'
    );

    // mode: 'bus' normalises to type: 'bus'
    const busLeg = result.legs.find(l => l.type === 'bus');
    expect(busLeg).toBeDefined();
    expect(busLeg.mode).toBe('bus');
    expect(busLeg.line_name).toBe('40');
    expect(busLeg.journey_origin).toBe('Lancaster');
    expect(busLeg.journey_destination).toBe('Preston');
  });

  test('routeGeometries have id, name, coords, color fields', async () => {
    fetch.mockReturnValueOnce(jsonResponse(MOCK_JOURNEY_SUCCESS));

    const result = await getJourneyPlans(
      { lat: 54.048, lon: -2.801 },
      { lat: 53.759, lon: -2.699 },
      '2026-02-19T10:00:00Z'
    );

    for (const geo of result.routeGeometries) {
      expect(geo).toHaveProperty('id');
      expect(geo).toHaveProperty('name');
      expect(geo).toHaveProperty('coords');
      expect(geo).toHaveProperty('color');
      expect(Array.isArray(geo.coords)).toBe(true);
      // coords are [lat, lon] pairs
      for (const coord of geo.coords) {
        expect(coord).toHaveLength(2);
        expect(typeof coord[0]).toBe('number');
        expect(typeof coord[1]).toBe('number');
      }
    }
  });

  test('handles walking-only route response', async () => {
    fetch.mockReturnValueOnce(jsonResponse(MOCK_JOURNEY_WALKING_ONLY));

    const result = await getJourneyPlans(
      { lat: 54.048, lon: -2.801 },
      { lat: 54.050, lon: -2.799 },
      '2026-02-19T10:00:00Z'
    );

    expect(result.success).toBe(true);
    expect(result.legs).toHaveLength(1);
    // backend mode "walking" normalises to type "walk"
    expect(result.legs[0].mode).toBe('walking');
    expect(result.legs[0].type).toBe('walk');
    expect(result.routeGeometries[0].color).toBe('#888888');
  });

  test('handles error response with null legs/meta/routeGeometries', async () => {
    fetch.mockReturnValueOnce(jsonResponse(MOCK_JOURNEY_ERROR));

    const result = await getJourneyPlans(
      { lat: 54.048, lon: -2.801 },
      { lat: 99, lon: 99 },
      '2026-02-19T10:00:00Z'
    );

    expect(result.success).toBe(false);
    expect(result.error).toBe('No route found');
    expect(result.legs).toStrictEqual([]);
    expect(result.meta).toStrictEqual({});
    expect(result.routeGeometries).toStrictEqual([]);
  });

  test('throws when fromStop/toStop lack lat/lon', async () => {
    await expect(
      getJourneyPlans({ name: 'A' }, { name: 'B' }, '2026-02-19T10:00:00Z')
    ).rejects.toThrow('lat/lon');
  });

  test('normalises departureTime ISO string into date + time parts', async () => {
    fetch.mockReturnValueOnce(jsonResponse(MOCK_JOURNEY_SUCCESS));

    await getJourneyPlans(
      { lat: 54.048, lon: -2.801 },
      { lat: 53.759, lon: -2.699 },
      '2026-02-19T10:00:00Z'
    );

    const body = JSON.parse(fetch.mock.calls[0][1].body);
    // date should be YYYY-MM-DD and departureTime should be HH:MM:SS
    expect(body.date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(body.departureTime).toMatch(/^\d{2}:\d{2}:\d{2}$/);
  });

  test('augments legs with offset display fields when backend provides day offsets', async () => {
    const MOCK_WITH_OFFSETS = {
      success: true,
      legs: [
        {
          mode: 'bus',  // backend uses 'mode'; service normalises to type:'bus'
          from_stop: { name: 'A', lat: 54.048, lon: -2.801 },
          to_stop: { name: 'B', lat: 53.759, lon: -2.699 },
          departure_time: '23:50:00',
          arrival_time: '00:20:00',
          departure_day_offset: 0,
          arrival_day_offset: 1,
        }
      ],
      meta: {},
      routeGeometries: [],
    };

    fetch.mockReturnValueOnce(jsonResponse(MOCK_WITH_OFFSETS));

    const result = await getJourneyPlans({ lat: 54.048, lon: -2.801 }, { lat: 53.759, lon: -2.699 }, '2026-02-28T23:00:00Z');

    expect(result.legs).toHaveLength(1);
    const leg = result.legs[0];
    expect(leg).toHaveProperty('arrival_time_with_offset');
    expect(leg.arrival_time_with_offset).toContain('00:20:00');
    expect(leg.arrival_time_with_offset).toContain('+1d');
    expect(leg).toHaveProperty('arrival_datetime_iso');
    // arrival_datetime_iso should be the next day (2026-03-01) because Feb 28 + 1 day -> Mar 1
    expect(leg.arrival_datetime_iso.startsWith('2026-03-01')).toBeTruthy();
  });
});

// ---- GET /rail/departures/{station} (not yet implemented) -----------------

describe('fetchRailDepartures — GET /rail/departures/{station}', () => {
  const MOCK_DEPARTURES = [
    {
      serviceId: 'XC1234',
      destination: 'Preston',
      scheduledTime: '10:15',
      departureTime: '10:17',
      status: 'delayed',
      lat: 54.049,
      lon: -2.800,
    },
  ];

  test('fetches departures for station code', async () => {
    fetch.mockReturnValueOnce(jsonResponse(MOCK_DEPARTURES));
    const result = await fetchRailDepartures('LAN');
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/rail/departures/LAN'));
    expect(result).toEqual(MOCK_DEPARTURES);
  });

  test('throws on HTTP error', async () => {
    fetch.mockReturnValueOnce(jsonResponse({ error: 'not found' }, 404));
    await expect(fetchRailDepartures('XXX')).rejects.toThrow('404');
  });
});

// ---- GET /weather ---------------------------------------------------------
// api.py /weather proxies SCC weather and returns OpenWeatherMap-shaped JSON:
//   { weather: [{main, description, icon}], wind: {speed}, main: {temp, humidity, ...} }
// WeatherWidget.jsx reads result.weather[0].main, result.main.temp, result.wind.speed.

describe('fetchWeatherData — GET /weather', () => {
  // Reflects the actual shape returned by api.py /weather (OpenWeatherMap proxy)
  const MOCK_WEATHER = {
    weather: [{ main: 'Clouds', description: 'overcast clouds', icon: '04d' }],
    wind: { speed: 5.2, deg: 210 },
    main: { temp: 12.3, feels_like: 10.1, humidity: 68, pressure: 1012 },
  };

  test('fetches weather with default coords', async () => {
    fetch.mockReturnValueOnce(jsonResponse(MOCK_WEATHER));
    const result = await fetchWeatherData();
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/weather?lat=54.05&lon=-2.8'));
    expect(result).toEqual(MOCK_WEATHER);
  });

  test('response has OpenWeatherMap shape: weather[], wind, main', async () => {
    fetch.mockReturnValueOnce(jsonResponse(MOCK_WEATHER));
    const result = await fetchWeatherData();
    expect(Array.isArray(result.weather)).toBe(true);
    expect(result.weather[0]).toHaveProperty('main');
    expect(result).toHaveProperty('wind');
    expect(result).toHaveProperty('main');
    expect(typeof result.main.temp).toBe('number');
    expect(typeof result.wind.speed).toBe('number');
  });

  test('passes custom lat/lon', async () => {
    fetch.mockReturnValueOnce(jsonResponse(MOCK_WEATHER));
    await fetchWeatherData(53.0, -1.5);
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining('lat=53'));
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining('lon=-1.5'));
  });
});

// ---- GET /alerts ----------------------------------------------------------

describe('fetchServiceAlerts — GET /alerts', () => {
  const MOCK_ALERTS = [
    {
      id: 'a1',
      title: 'M6 delays',
      severity: 'high',
      description: 'Roadworks on M6 junction 33',
      affectedLines: ['40', '42'],
      start: '2026-02-19T06:00:00Z',
      end: '2026-02-19T18:00:00Z',
    },
  ];

  test('fetches alerts', async () => {
    fetch.mockReturnValueOnce(jsonResponse(MOCK_ALERTS));
    const result = await fetchServiceAlerts();
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/alerts'));
    expect(result).toEqual(MOCK_ALERTS);
  });

  test('returns empty array when no alerts', async () => {
    fetch.mockReturnValueOnce(jsonResponse([]));
    const result = await fetchServiceAlerts();
    expect(result).toEqual([]);
  });
});

// ---- GET /pricing ---------------------------------------------------------

describe('fetchPricing — GET /pricing', () => {
  const MOCK_PRICING = {
    price: 4.50,
    currency: 'GBP',
    fares: [
      { type: 'single', price: 4.50 },
      { type: 'return', price: 7.20 },
    ],
  };

  test('fetches pricing for from/to stops', async () => {
    fetch.mockReturnValueOnce(jsonResponse(MOCK_PRICING));
    const result = await fetchPricing('LAN001', 'PRE001');
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/pricing?from=LAN001&to=PRE001'));
    expect(result).toEqual(MOCK_PRICING);
  });

  test('throws on server error', async () => {
    fetch.mockReturnValueOnce(jsonResponse({ error: 'unknown' }, 500));
    await expect(fetchPricing('A', 'B')).rejects.toThrow('500');
  });
});

// ---- fetchBusTimes --------------------------------------------------------

describe('fetchBusTimes — GET /bus/times/{stopCode}', () => {
  test('fetches bus times for stop code', async () => {
    const mockTimes = [{ line: '1A', due: '5 min' }];
    fetch.mockReturnValueOnce(jsonResponse(mockTimes));
    const result = await fetchBusTimes('2800S12345');
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/bus/times/2800S12345'));
    expect(result).toEqual(mockTimes);
  });
});

// ---- fetchBusArrivals -----------------------------------------------------

describe('fetchBusArrivals — GET /bus/arrivals/{stopCode}', () => {
  test('fetches bus arrivals for stop code', async () => {
    const mockArrivals = [{ line: '2B', eta: '3 min' }];
    fetch.mockReturnValueOnce(jsonResponse(mockArrivals));
    const result = await fetchBusArrivals('2800S12345');
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/bus/arrivals/2800S12345'));
    expect(result).toEqual(mockArrivals);
  });

  test('throws on HTTP error', async () => {
    fetch.mockReturnValueOnce(jsonResponse({}, 404));
    await expect(fetchBusArrivals('INVALID')).rejects.toThrow('404');
  });
});

// ---- API_BASE_URL environment variable configuration ----------------------

/**
 * These tests verify that API_BASE_URL is driven by the VITE_API_BASE_URL
 * environment variable (P1 fix).  Because the constant is evaluated at
 * module-load time, each test resets the module registry and dynamically
 * re-imports transportApi to pick up the stubbed env value.
 */
describe('API_BASE_URL — VITE_API_BASE_URL env variable (P1)', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  test('defaults to http://localhost:5050 when VITE_API_BASE_URL is empty', async () => {
    vi.stubEnv('VITE_API_BASE_URL', '');
    vi.resetModules();
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(jsonResponse([])));

    const { searchStops: search } = await import('../transportApi');
    await search('test');

    const url = fetch.mock.calls[0][0];
    expect(url).toBe('http://localhost:5050/search/stops?q=test&limit=5');
  });

  test('defaults to http://localhost:5050 when VITE_API_BASE_URL is undefined', async () => {
    // In vitest, deleting an env key is done by setting it to undefined
    vi.stubEnv('VITE_API_BASE_URL', undefined);
    vi.resetModules();
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(jsonResponse([])));

    const { searchStops: search } = await import('../transportApi');
    await search('test');

    const url = fetch.mock.calls[0][0];
    expect(url).toContain('http://localhost:5050/');
  });

  test('uses VITE_API_BASE_URL when set to a custom URL', async () => {
    vi.stubEnv('VITE_API_BASE_URL', 'https://custom-api.example.com');
    vi.resetModules();
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(jsonResponse([])));

    const { searchStops: search } = await import('../transportApi');
    await search('test');

    const url = fetch.mock.calls[0][0];
    expect(url).toBe('https://custom-api.example.com/search/stops?q=test&limit=5');
  });

  test('uses production URL when VITE_API_BASE_URL points to external host', async () => {
    vi.stubEnv('VITE_API_BASE_URL', 'https://transport.scc.lancs.ac.uk');
    vi.resetModules();
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(jsonResponse([])));

    const { searchStops: search } = await import('../transportApi');
    await search('test');

    const url = fetch.mock.calls[0][0];
    expect(url).toBe('https://transport.scc.lancs.ac.uk/search/stops?q=test&limit=5');
  });

  test('all API functions use the configured base URL', async () => {
    vi.stubEnv('VITE_API_BASE_URL', 'http://my-backend:9000');
    vi.resetModules();
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(jsonResponse([])));

    const mod = await import('../transportApi');

    // Call several functions and verify each hits the configured host
    await mod.searchStops('x');
    await mod.fetchBusTimes('STOP1');
    await mod.fetchRailDepartures('LAN');
    await mod.fetchServiceAlerts();

    for (const call of fetch.mock.calls) {
      const url = typeof call[0] === 'string' ? call[0] : call[0].toString();
      expect(url).toMatch(/^http:\/\/my-backend:9000\//);
    }
  });
});
