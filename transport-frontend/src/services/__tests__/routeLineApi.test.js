/**
 * Tests for routeLineApi service
 *
 * Validates:
 *  - MOCK_ROUTES data integrity
 *  - fetchRouteLine calls the correct API URL, returns data, throws on error
 *  - fetchRouteLineWithFallback returns API data when available
 *  - fetchRouteLineWithFallback returns matching MOCK_ROUTES entry on failure
 *  - fetchRouteLineWithFallback returns empty-variants object for unknown lines
 *  - stopsToLatLngs converts stop arrays to [lat, lon] pairs correctly
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  fetchRouteLine,
  fetchRouteLineWithFallback,
  stopsToLatLngs,
  MOCK_ROUTES,
} from '../routeLineApi';

beforeEach(() => {
  vi.restoreAllMocks();
});

afterEach(() => {
  vi.restoreAllMocks();
});

// ── MOCK_ROUTES integrity ───────────────────────────────────────────

describe('MOCK_ROUTES', () => {
  it('contains at least one line entry', () => {
    expect(Object.keys(MOCK_ROUTES).length).toBeGreaterThan(0);
  });

  it('every mock route has "line" and "variants" fields', () => {
    for (const [key, route] of Object.entries(MOCK_ROUTES)) {
      expect(route).toHaveProperty('line');
      expect(route.line).toBe(key);
      expect(Array.isArray(route.variants)).toBe(true);
      expect(route.variants.length).toBeGreaterThan(0);
    }
  });

  it('every variant has route_id and non-empty stops array', () => {
    for (const route of Object.values(MOCK_ROUTES)) {
      for (const variant of route.variants) {
        expect(variant).toHaveProperty('route_id');
        expect(Array.isArray(variant.stops)).toBe(true);
        expect(variant.stops.length).toBeGreaterThanOrEqual(2);
      }
    }
  });

  it('every stop has name, lat, lon and atco_code', () => {
    for (const route of Object.values(MOCK_ROUTES)) {
      for (const variant of route.variants) {
        for (const stop of variant.stops) {
          expect(stop).toHaveProperty('name');
          expect(typeof stop.lat).toBe('number');
          expect(typeof stop.lon).toBe('number');
          expect(stop).toHaveProperty('atco_code');
        }
      }
    }
  });

  it('stop coordinates are within Lancashire bounding box', () => {
    for (const route of Object.values(MOCK_ROUTES)) {
      for (const variant of route.variants) {
        for (const stop of variant.stops) {
          expect(stop.lat).toBeGreaterThan(53.5);
          expect(stop.lat).toBeLessThan(55.0);
          expect(stop.lon).toBeGreaterThan(-3.5);
          expect(stop.lon).toBeLessThan(-2.0);
        }
      }
    }
  });

  it('contains mock data for line "1"', () => {
    expect(MOCK_ROUTES['1']).toBeDefined();
    expect(MOCK_ROUTES['1'].variants.length).toBeGreaterThan(0);
  });

  it('line 1 route has at least 5 stops', () => {
    expect(MOCK_ROUTES['1'].variants[0].stops.length).toBeGreaterThanOrEqual(5);
  });

  it('stop atco_codes are unique within each variant', () => {
    for (const route of Object.values(MOCK_ROUTES)) {
      for (const variant of route.variants) {
        const codes = variant.stops.map((s) => s.atco_code);
        expect(new Set(codes).size).toBe(codes.length);
      }
    }
  });
});

// ── fetchRouteLine (raw API call) ───────────────────────────────────

describe('fetchRouteLine', () => {
  const FAKE_RESPONSE = {
    line: '1',
    variants: [{ route_id: 'r1', stops: [{ name: 'A', lat: 54.0, lon: -2.8, atco_code: 'XXX1' }] }],
  };

  it('calls GET /routes/line/{line} with the correct URL', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(FAKE_RESPONSE),
    });

    await fetchRouteLine('1');

    expect(global.fetch).toHaveBeenCalledTimes(1);
    const url = global.fetch.mock.calls[0][0];
    expect(url).toContain('/routes/line/1');
  });

  it('returns the parsed JSON response', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(FAKE_RESPONSE),
    });

    const result = await fetchRouteLine('1');
    expect(result).toEqual(FAKE_RESPONSE);
  });

  it('throws on non-OK HTTP status', async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 503 });
    await expect(fetchRouteLine('1')).rejects.toThrow('503');
  });

  it('throws on network failure', async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error('Network error'));
    await expect(fetchRouteLine('1')).rejects.toThrow('Network error');
  });

  it('URL-encodes the line name', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(FAKE_RESPONSE),
    });

    await fetchRouteLine('1A/X');

    const url = global.fetch.mock.calls[0][0];
    expect(url).toContain('1A%2FX');
  });
});

// ── fetchRouteLineWithFallback ──────────────────────────────────────

describe('fetchRouteLineWithFallback', () => {
  const API_RESPONSE = {
    line: '1',
    variants: [{ route_id: 'api-r1', stops: [{ name: 'API Stop', lat: 54.0, lon: -2.8, atco_code: 'API1' }] }],
  };

  it('returns API data when the backend responds successfully', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(API_RESPONSE),
    });

    const result = await fetchRouteLineWithFallback('1');
    expect(result).toEqual(API_RESPONSE);
  });

  it('returns MOCK_ROUTES entry when the backend fails (network error)', async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error('Network error'));

    const result = await fetchRouteLineWithFallback('1');
    expect(result).toEqual(MOCK_ROUTES['1']);
  });

  it('returns MOCK_ROUTES entry when the backend returns HTTP error', async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 503 });

    const result = await fetchRouteLineWithFallback('100');
    expect(result).toEqual(MOCK_ROUTES['100']);
  });

  it('returns empty-variants object for unknown line when backend fails', async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error('Network error'));

    const result = await fetchRouteLineWithFallback('999');
    expect(result).toEqual({ line: '999', variants: [] });
  });

  it('preserves the correct line name in the empty-variants fallback', async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error('Network error'));

    const result = await fetchRouteLineWithFallback('42X');
    expect(result.line).toBe('42X');
    expect(result.variants).toEqual([]);
  });

  it('does not fall back when API returns successful empty variants', async () => {
    // The backend might legitimately return no variants; we must respect that.
    const emptyApiResponse = { line: '1', variants: [] };
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(emptyApiResponse),
    });

    const result = await fetchRouteLineWithFallback('1');
    expect(result).toEqual(emptyApiResponse);
  });

  it('returns MOCK_ROUTES for all lines that have mock data', async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error('down'));

    for (const line of Object.keys(MOCK_ROUTES)) {
      const result = await fetchRouteLineWithFallback(line);
      expect(result).toEqual(MOCK_ROUTES[line]);
    }
  });
});

// ── stopsToLatLngs ──────────────────────────────────────────────────

describe('stopsToLatLngs', () => {
  it('converts an array of stops to [lat, lon] pairs', () => {
    const stops = [
      { name: 'A', lat: 54.0, lon: -2.8 },
      { name: 'B', lat: 54.1, lon: -2.9 },
    ];
    expect(stopsToLatLngs(stops)).toEqual([[54.0, -2.8], [54.1, -2.9]]);
  });

  it('returns empty array for empty input', () => {
    expect(stopsToLatLngs([])).toEqual([]);
  });

  it('returns empty array for non-array input', () => {
    expect(stopsToLatLngs(null)).toEqual([]);
    expect(stopsToLatLngs(undefined)).toEqual([]);
    expect(stopsToLatLngs('string')).toEqual([]);
  });

  it('filters out stops with null lat or lon', () => {
    const stops = [
      { name: 'Good', lat: 54.0, lon: -2.8 },
      { name: 'No lat', lat: null, lon: -2.9 },
      { name: 'No lon', lat: 54.1, lon: null },
      { name: 'Good 2', lat: 54.2, lon: -2.7 },
    ];
    expect(stopsToLatLngs(stops)).toEqual([[54.0, -2.8], [54.2, -2.7]]);
  });

  it('filters out stops with undefined lat or lon', () => {
    const stops = [
      { name: 'Good', lat: 54.0, lon: -2.8 },
      { name: 'No lon', lat: 54.1 },
    ];
    expect(stopsToLatLngs(stops)).toEqual([[54.0, -2.8]]);
  });

  it('works correctly with MOCK_ROUTES stop data', () => {
    const stops = MOCK_ROUTES['1'].variants[0].stops;
    const result = stopsToLatLngs(stops);
    expect(result).toHaveLength(stops.length);
    for (const [lat, lon] of result) {
      expect(typeof lat).toBe('number');
      expect(typeof lon).toBe('number');
    }
  });

  it('preserves order of stops', () => {
    const stops = [
      { lat: 1.0, lon: 2.0 },
      { lat: 3.0, lon: 4.0 },
      { lat: 5.0, lon: 6.0 },
    ];
    expect(stopsToLatLngs(stops)).toEqual([[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]]);
  });
});
