/**
 * useJourneyPlans hook — integration schema tests
 *
 * Validates that the useJourneyPlans hook correctly passes
 * the journey plan response from the backend (api.py /journey/plan)
 * through to the consumer, including legs, meta, and routeGeometries.
 */

import React from 'react';
import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import { useJourneyPlans } from '../useTransportData';
import * as api from '../../services/transportApi';

vi.mock('../../services/transportApi', () => ({
  fetchLiveBusLocations: vi.fn(),
  fetchRailDepartures: vi.fn(),
  fetchBusArrivals: vi.fn(),
  searchStops: vi.fn(),
  getJourneyPlans: vi.fn(),
  fetchServiceAlerts: vi.fn(),
  fetchPricing: vi.fn(),
}));

vi.mock('../../services/liveUpdates', () => ({
  liveUpdatesManager: {
    connect: vi.fn().mockRejectedValue(new Error('not available')),
    disconnect: vi.fn().mockResolvedValue(undefined),
    subscribeToTrainMovements: vi.fn(),
    subscribeToBusMovements: vi.fn(),
    subscribeToAlerts: vi.fn(),
    unsubscribe: vi.fn(),
  },
}));

afterEach(() => {
  vi.clearAllMocks();
});

// Exact backend response shapes from api.py build_journey_plan_response
const BACKEND_MULTI_LEG = {
  success: true,
  legs: [
    {
      type: 'walking',
      from_stop: { name: 'Start', lat: 54.048, lon: -2.801 },
      to_stop: { name: 'Lancaster Bus Station', lat: 54.049, lon: -2.800 },
      duration_seconds: 120,
      departure_time: null,
      arrival_time: '10:02:00',
    },
    {
      type: 'bus',
      from_stop: { name: 'Lancaster Bus Station', lat: 54.049, lon: -2.800 },
      to_stop: { name: 'Preston Bus Station', lat: 53.759, lon: -2.699 },
      duration_seconds: 1800,
      departure_time: '10:05:00',
      arrival_time: '10:35:00',
      line_name: '40',
      journey_origin: 'Lancaster',
      journey_destination: 'Preston',
    },
    {
      type: 'walking',
      from_stop: { name: 'Preston Bus Station', lat: 53.759, lon: -2.699 },
      to_stop: { name: 'Destination', lat: 53.760, lon: -2.700 },
      duration_seconds: 60,
      departure_time: '10:35:00',
      arrival_time: '10:36:00',
    },
  ],
  meta: {
    start_walk_seconds: 120,
    end_walk_seconds: 60,
    total_arrival: '10:36:00',
    start_point: [54.048, -2.801],
    destination: [53.760, -2.700],
  },
  routeGeometries: [
    { id: 'walk-0', name: 'Walk to Lancaster Bus Station', coords: [[54.048, -2.801], [54.049, -2.800]], color: '#888888' },
    { id: 'bus-1', name: 'Bus 40', coords: [[54.049, -2.800], [53.759, -2.699]], color: '#1a73e8' },
    { id: 'walk-2', name: 'Walk to destination', coords: [[53.759, -2.699], [53.760, -2.700]], color: '#888888' },
  ],
};

const BACKEND_WALKING_ONLY = {
  success: true,
  legs: [
    {
      type: 'walking',
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

const BACKEND_NO_ROUTE = {
  success: true,
  legs: [],
  meta: {},
  routeGeometries: [],
};

const BACKEND_ERROR = {
  success: false,
  error: 'Router failed: no data for date',
  legs: null,
  meta: null,
  routeGeometries: null,
};

describe('useJourneyPlans — backend integration', () => {
  test('stores multi-leg journey plan from backend', async () => {
    api.getJourneyPlans.mockResolvedValueOnce(BACKEND_MULTI_LEG);

    const fromStop = { lat: 54.048, lon: -2.801 };
    const toStop = { lat: 53.760, lon: -2.700 };

    const { result } = renderHook(() =>
      useJourneyPlans(fromStop, toStop, '2026-02-19T10:00:00Z')
    );

    await act(async () => {
      result.current.fetchPlans();
    });

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
      expect(result.current.routes).toBeDefined();
      expect(result.current.routes.success).toBe(true);
      expect(result.current.routes.legs).toHaveLength(3);
    });

    // Verify leg types
    const legTypes = result.current.routes.legs.map(l => l.type);
    expect(legTypes).toEqual(['walking', 'bus', 'walking']);

    // Verify routeGeometries are available for polyline rendering
    expect(result.current.routes.routeGeometries).toHaveLength(3);
    expect(result.current.routes.routeGeometries[1].color).toBe('#1a73e8');
  });

  test('stores walking-only journey plan', async () => {
    api.getJourneyPlans.mockResolvedValueOnce(BACKEND_WALKING_ONLY);

    const fromStop = { lat: 54.048, lon: -2.801 };
    const toStop = { lat: 54.050, lon: -2.799 };

    const { result } = renderHook(() =>
      useJourneyPlans(fromStop, toStop, '2026-02-19T10:00:00Z')
    );

    await act(async () => {
      result.current.fetchPlans();
    });

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
      expect(result.current.routes.legs).toHaveLength(1);
      expect(result.current.routes.legs[0].type).toBe('walking');
    });
  });

  test('handles no-route-found (empty legs)', async () => {
    api.getJourneyPlans.mockResolvedValueOnce(BACKEND_NO_ROUTE);

    const { result } = renderHook(() =>
      useJourneyPlans({ lat: 54, lon: -2 }, { lat: 55, lon: -3 }, '2026-02-19T10:00:00Z')
    );

    await act(async () => {
      result.current.fetchPlans();
    });

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
      expect(result.current.routes.success).toBe(true);
      expect(result.current.routes.legs).toEqual([]);
    });
  });

  test('handles backend error (success:false, null fields)', async () => {
    api.getJourneyPlans.mockResolvedValueOnce(BACKEND_ERROR);

    const { result } = renderHook(() =>
      useJourneyPlans({ lat: 54, lon: -2 }, { lat: 55, lon: -3 }, '2026-02-19T10:00:00Z')
    );

    await act(async () => {
      result.current.fetchPlans();
    });

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
      expect(result.current.routes.success).toBe(false);
      expect(result.current.routes.legs).toBeNull();
      expect(result.current.routes.meta).toBeNull();
      expect(result.current.routes.routeGeometries).toBeNull();
    });
  });

  test('does not fetch when fromStop is null', async () => {
    const { result } = renderHook(() =>
      useJourneyPlans(null, { lat: 54, lon: -2 }, '2026-02-19T10:00:00Z')
    );

    await act(async () => {
      result.current.fetchPlans();
    });

    expect(api.getJourneyPlans).not.toHaveBeenCalled();
  });

  test('does not fetch when toStop is null', async () => {
    const { result } = renderHook(() =>
      useJourneyPlans({ lat: 54, lon: -2 }, null, '2026-02-19T10:00:00Z')
    );

    await act(async () => {
      result.current.fetchPlans();
    });

    expect(api.getJourneyPlans).not.toHaveBeenCalled();
  });

  test('sets error state on network failure with retry exhaustion', async () => {
    api.getJourneyPlans.mockRejectedValue(new Error('Network error'));

    const { result } = renderHook(() =>
      useJourneyPlans({ lat: 54, lon: -2 }, { lat: 55, lon: -3 }, '2026-02-19T10:00:00Z')
    );

    await act(async () => {
      result.current.fetchPlans();
    });

    await waitFor(() => {
      expect(result.current.error).toBeTruthy();
      expect(result.current.loading).toBe(false);
    }, { timeout: 15000 });
  });

  test('routeGeometries coords use [lat, lon] order (not GeoJSON)', async () => {
    api.getJourneyPlans.mockResolvedValueOnce(BACKEND_MULTI_LEG);

    const { result } = renderHook(() =>
      useJourneyPlans({ lat: 54.048, lon: -2.801 }, { lat: 53.760, lon: -2.700 }, '2026-02-19T10:00:00Z')
    );

    await act(async () => {
      result.current.fetchPlans();
    });

    await waitFor(() => {
      const firstCoord = result.current.routes.routeGeometries[0].coords[0];
      // First value is latitude (positive ~54), second is longitude (negative ~-2.8)
      expect(firstCoord[0]).toBeGreaterThan(50);
      expect(firstCoord[1]).toBeLessThan(0);
    });
  });
});
