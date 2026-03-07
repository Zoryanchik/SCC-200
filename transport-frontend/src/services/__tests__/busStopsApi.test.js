/**
 * Tests for busStopsApi service
 *
 * Validates:
 *  - fetchBusStops calls the correct API URL and normalises response
 *  - fetchClassifiedStops calls /stops/classify
 *  - getBusStopsWithFallback returns mock data on API failure
 *  - Mock data structure integrity
 *  - Classification filtering on mock fallback
 *  - normalisation handles edge cases (missing fields, bad coords)
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  fetchBusStops,
  fetchClassifiedStops,
  getBusStopsWithFallback,
  fetchBusArrivals,
  MOCK_BUS_STOPS,
  MOCK_ARRIVALS,
} from '../busStopsApi';

// ── Helpers ─────────────────────────────────────────────────────────

/** Build a minimal valid stop object as returned by /search/stops */
const makeApiStop = (overrides = {}) => ({
  id: 'stop-1',
  name: 'Test Stop',
  lat: 54.05,
  lon: -2.80,
  atco_code: '2500TEST001',
  type: 'stop',
  classification: 'local',
  lines: ['1', '2'],
  ...overrides,
});

beforeEach(() => {
  vi.restoreAllMocks();
});

afterEach(() => {
  vi.restoreAllMocks();
});

// ── MOCK_BUS_STOPS integrity ────────────────────────────────────────

describe('MOCK_BUS_STOPS', () => {
  it('contains at least 10 mock stops', () => {
    expect(MOCK_BUS_STOPS.length).toBeGreaterThanOrEqual(10);
  });

  it('every mock stop has required fields', () => {
    for (const stop of MOCK_BUS_STOPS) {
      expect(stop).toHaveProperty('id');
      expect(stop).toHaveProperty('name');
      expect(typeof stop.lat).toBe('number');
      expect(typeof stop.lon).toBe('number');
      expect(stop).toHaveProperty('classification');
      expect(Array.isArray(stop.lines)).toBe(true);
      expect(stop.lines.length).toBeGreaterThan(0);
    }
  });

  it('includes at least one hub and one interchange', () => {
    const classes = MOCK_BUS_STOPS.map((s) => s.classification);
    expect(classes).toContain('hub');
    expect(classes).toContain('interchange');
  });

  it('every mock stop has a unique id', () => {
    const ids = MOCK_BUS_STOPS.map((s) => s.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it('coordinates are within Lancashire bounds', () => {
    for (const stop of MOCK_BUS_STOPS) {
      // Rough Lancashire bounding box
      expect(stop.lat).toBeGreaterThan(53.5);
      expect(stop.lat).toBeLessThan(55.0);
      expect(stop.lon).toBeGreaterThan(-3.5);
      expect(stop.lon).toBeLessThan(-2.0);
    }
  });
});

// ── fetchBusStops ───────────────────────────────────────────────────

describe('fetchBusStops', () => {
  it('calls the correct URL and returns normalised stops', async () => {
    const apiStops = [makeApiStop(), makeApiStop({ id: 'stop-2', name: 'Stop B', lat: 54.06, lon: -2.81 })];
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(apiStops),
    });

    const result = await fetchBusStops();

    expect(global.fetch).toHaveBeenCalledTimes(1);
    const calledUrl = global.fetch.mock.calls[0][0];
    expect(calledUrl).toContain('/stops/geo');
    expect(result).toHaveLength(2);
    expect(result[0].name).toBe('Test Stop');
    expect(result[0].lat).toBe(54.05);
  });

  it('passes classification param when specified', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve([makeApiStop({ classification: 'hub' })]),
    });

    await fetchBusStops({ classification: 'hub' });

    const calledUrl = global.fetch.mock.calls[0][0];
    expect(calledUrl).toContain('classification=hub');
  });

  it('passes bbox param when specified', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve([]),
    });

    await fetchBusStops({ bbox: '53.0,-3.5,55.0,-2.0' });

    const calledUrl = global.fetch.mock.calls[0][0];
    expect(calledUrl).toContain('bbox=53.0');
  });

  it('throws on HTTP error', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 503,
    });

    await expect(fetchBusStops()).rejects.toThrow('HTTP error! status: 503');
  });

  it('throws on non-array response', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ error: 'bad' }),
    });

    await expect(fetchBusStops()).rejects.toThrow('Unexpected response format');
  });

  it('filters out stops with missing lat/lon', async () => {
    const stops = [
      makeApiStop(),
      { id: 'bad-1', name: 'No Coords' }, // no lat/lon
      makeApiStop({ id: 'stop-3', lat: 54.07, lon: -2.82 }),
    ];
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(stops),
    });

    const result = await fetchBusStops();
    expect(result).toHaveLength(2);
  });

  it('normalises string lines into array', async () => {
    const stop = makeApiStop({ lines: '1, 2A, 100' });
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve([stop]),
    });

    const result = await fetchBusStops();
    expect(result[0].lines).toEqual(['1', '2A', '100']);
  });

  it('defaults lines to empty array when missing', async () => {
    const stop = makeApiStop({ lines: undefined });
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve([stop]),
    });

    const result = await fetchBusStops();
    expect(result[0].lines).toEqual([]);
  });
});

// ── fetchClassifiedStops ────────────────────────────────────────────

describe('fetchClassifiedStops', () => {
  it('calls /stops/classify without params by default', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve([]),
    });

    await fetchClassifiedStops();

    expect(global.fetch).toHaveBeenCalledTimes(1);
    const calledUrl = global.fetch.mock.calls[0][0];
    expect(calledUrl).toContain('/stops/classify');
    expect(calledUrl).not.toContain('classification');
  });

  it('appends classification query param when provided', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve([]),
    });

    await fetchClassifiedStops('hub');

    const calledUrl = global.fetch.mock.calls[0][0];
    expect(calledUrl).toContain('classification=hub');
  });

  it('throws on HTTP error', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
    });

    await expect(fetchClassifiedStops()).rejects.toThrow('HTTP error');
  });
});

// ── getBusStopsWithFallback ─────────────────────────────────────────

describe('getBusStopsWithFallback', () => {
  it('returns API data when available', async () => {
    const apiStops = [makeApiStop()];
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(apiStops),
    });

    const result = await getBusStopsWithFallback();

    expect(result).toHaveLength(1);
    expect(result[0].name).toBe('Test Stop');
  });

  it('falls back to mock data when API fails', async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error('Network error'));

    const result = await getBusStopsWithFallback();

    expect(result).toEqual(MOCK_BUS_STOPS);
  });

  it('falls back to mock data when API returns empty array', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve([]),
    });

    const result = await getBusStopsWithFallback();

    expect(result).toEqual(MOCK_BUS_STOPS);
  });

  it('falls back to mock data on HTTP error', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 503,
    });

    const result = await getBusStopsWithFallback();

    expect(result).toEqual(MOCK_BUS_STOPS);
  });

  it('filters mock data by classification when API unavailable', async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error('down'));

    const result = await getBusStopsWithFallback({ classification: 'hub' });

    expect(result.length).toBeGreaterThan(0);
    for (const stop of result) {
      expect(stop.classification).toBe('hub');
    }
  });

  it('passes classification to API call', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve([makeApiStop({ classification: 'interchange' })]),
    });

    await getBusStopsWithFallback({ classification: 'interchange' });

    const calledUrl = global.fetch.mock.calls[0][0];
    expect(calledUrl).toContain('classification=interchange');
  });

  it('passes bbox to API call', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve([makeApiStop()]),
    });

    await getBusStopsWithFallback({ bbox: '53.0,-3.5,55.0,-2.0' });

    const calledUrl = global.fetch.mock.calls[0][0];
    expect(calledUrl).toContain('bbox=53.0');
  });

  it('filters mock fallback stops by bbox', async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error('down'));

    // Tight bbox around Lancaster city centre only
    const result = await getBusStopsWithFallback({ bbox: '54.04,-2.815,54.06,-2.79' });

    expect(result.length).toBeGreaterThan(0);
    for (const stop of result) {
      expect(stop.lat).toBeGreaterThanOrEqual(54.04);
      expect(stop.lat).toBeLessThanOrEqual(54.06);
      expect(stop.lon).toBeGreaterThanOrEqual(-2.815);
      expect(stop.lon).toBeLessThanOrEqual(-2.79);
    }
  });

  it('returns empty array from mock when bbox excludes all stops', async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error('down'));

    // bbox in the middle of the North Sea — no stops
    const result = await getBusStopsWithFallback({ bbox: '55.0,5.0,56.0,6.0' });

    expect(result).toEqual([]);
  });
});

// ── MOCK_ARRIVALS integrity ─────────────────────────────────────────

describe('MOCK_ARRIVALS', () => {
  it('contains at least one arrival', () => {
    expect(MOCK_ARRIVALS.length).toBeGreaterThan(0);
  });

  it('every mock arrival has required fields', () => {
    for (const a of MOCK_ARRIVALS) {
      expect(a).toHaveProperty('line');
      expect(a).toHaveProperty('destination');
      expect(a).toHaveProperty('scheduledTime');
      expect(a).toHaveProperty('status');
    }
  });

  it('scheduledTime is in HH:MM:SS format', () => {
    for (const a of MOCK_ARRIVALS) {
      expect(a.scheduledTime).toMatch(/^\d{2}:\d{2}:\d{2}$/);
    }
  });
});

// ── fetchBusArrivals ────────────────────────────────────────────────

describe('fetchBusArrivals', () => {
  const ATCO = '2500LAA12000';
  const API_ARRIVALS = [
    { line: '1', destination: 'Lancaster', scheduledTime: '12:00:00', status: 'On time' },
    { line: '100', destination: 'Uni', scheduledTime: '12:15:00', status: 'On time' },
  ];

  it('calls GET /bus/arrivals/{stopCode}', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(API_ARRIVALS),
    });

    await fetchBusArrivals(ATCO);

    expect(global.fetch).toHaveBeenCalledTimes(1);
    const url = global.fetch.mock.calls[0][0];
    expect(url).toContain(`/bus/arrivals/${ATCO}`);
  });

  it('returns the arrivals array from the API', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(API_ARRIVALS),
    });

    const result = await fetchBusArrivals(ATCO);
    expect(result).toEqual(API_ARRIVALS);
  });

  it('falls back to MOCK_ARRIVALS on network failure', async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error('Network error'));

    const result = await fetchBusArrivals(ATCO);
    expect(result).toEqual(MOCK_ARRIVALS);
  });

  it('falls back to MOCK_ARRIVALS on HTTP error', async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 503 });

    const result = await fetchBusArrivals(ATCO);
    expect(result).toEqual(MOCK_ARRIVALS);
  });

  it('falls back to MOCK_ARRIVALS when API returns non-array', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ error: 'bad' }),
    });

    const result = await fetchBusArrivals(ATCO);
    expect(result).toEqual(MOCK_ARRIVALS);
  });

  it('URL-encodes the stop code', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve([]),
    });

    await fetchBusArrivals('2500 AB/12');

    const url = global.fetch.mock.calls[0][0];
    expect(url).toContain('2500%20AB%2F12');
  });
});
