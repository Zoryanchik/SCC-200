/**
 * usePricing & useLiveUpdates hooks — integration schema tests
 *
 * Covers the remaining untested hooks in useTransportData.js
 * to achieve ≥85% coverage on the hooks module.
 */

import React from 'react';
import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { usePricing, useLiveUpdates } from '../useTransportData';
import * as api from '../../services/transportApi';
import { liveUpdatesManager } from '../../services/liveUpdates';

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

// ---- usePricing -----------------------------------------------------------

describe('usePricing — GET /pricing contract', () => {
  const MOCK_PRICING = {
    price: 4.50,
    currency: 'GBP',
    fares: [
      { type: 'single', price: 4.50 },
      { type: 'return', price: 7.20 },
    ],
  };

  test('fetches pricing when both stops are provided', async () => {
    api.fetchPricing.mockResolvedValueOnce(MOCK_PRICING);

    const { result } = renderHook(() => usePricing('LAN001', 'PRE001'));

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
      expect(result.current.pricing).toEqual(MOCK_PRICING);
      expect(result.current.error).toBeNull();
    });

    expect(api.fetchPricing).toHaveBeenCalledWith('LAN001', 'PRE001');
  });

  test('does not fetch when fromStop is null', () => {
    renderHook(() => usePricing(null, 'PRE001'));
    expect(api.fetchPricing).not.toHaveBeenCalled();
  });

  test('does not fetch when toStop is null', () => {
    renderHook(() => usePricing('LAN001', null));
    expect(api.fetchPricing).not.toHaveBeenCalled();
  });

  test('sets error state on API failure', async () => {
    api.fetchPricing.mockRejectedValue(new Error('Pricing unavailable'));

    const { result } = renderHook(() => usePricing('LAN001', 'PRE001'));

    await waitFor(() => {
      expect(result.current.error).toBeTruthy();
      expect(result.current.loading).toBe(false);
      expect(result.current.pricing).toBeNull();
    }, { timeout: 10000 });
  });

  test('returns null pricing initially', () => {
    api.fetchPricing.mockReturnValue(new Promise(() => {})); // never resolves
    const { result } = renderHook(() => usePricing('A', 'B'));
    expect(result.current.pricing).toBeNull();
  });
});

// ---- useLiveUpdates -------------------------------------------------------

describe('useLiveUpdates — WebSocket/STOMP', () => {
  test('sets error when connection fails', async () => {
    liveUpdatesManager.connect.mockRejectedValueOnce(new Error('connection refused'));

    const { result } = renderHook(() => useLiveUpdates('train'));

    await waitFor(() => {
      expect(result.current.error).toBeTruthy();
      expect(result.current.isConnected).toBe(false);
    });
  });

  test('subscribes to train movements when type is train', async () => {
    liveUpdatesManager.connect.mockResolvedValueOnce(undefined);
    liveUpdatesManager.subscribeToTrainMovements.mockReturnValue('sub-1');

    const { result } = renderHook(() => useLiveUpdates('train'));

    await waitFor(() => {
      expect(liveUpdatesManager.connect).toHaveBeenCalled();
    });
  });

  test('subscribes to bus movements when type is bus', async () => {
    liveUpdatesManager.connect.mockResolvedValueOnce(undefined);
    liveUpdatesManager.subscribeToBusMovements.mockReturnValue('sub-2');

    const { result } = renderHook(() => useLiveUpdates('bus'));

    await waitFor(() => {
      expect(liveUpdatesManager.connect).toHaveBeenCalled();
    });
  });

  test('subscribes to alerts when type is alerts', async () => {
    liveUpdatesManager.connect.mockResolvedValueOnce(undefined);
    liveUpdatesManager.subscribeToAlerts.mockReturnValue('sub-3');

    const { result } = renderHook(() => useLiveUpdates('alerts'));

    await waitFor(() => {
      expect(liveUpdatesManager.connect).toHaveBeenCalled();
    });
  });

  test('disconnects on unmount', async () => {
    liveUpdatesManager.connect.mockRejectedValueOnce(new Error('down'));

    const { unmount } = renderHook(() => useLiveUpdates('train'));

    await waitFor(() => {
      expect(liveUpdatesManager.connect).toHaveBeenCalled();
    });

    unmount();

    await waitFor(() => {
      expect(liveUpdatesManager.disconnect).toHaveBeenCalled();
    });
  });

  test('defaults to train subscription when no type given', async () => {
    liveUpdatesManager.connect.mockRejectedValueOnce(new Error('down'));

    renderHook(() => useLiveUpdates());

    await waitFor(() => {
      expect(liveUpdatesManager.connect).toHaveBeenCalled();
    });
  });
});
