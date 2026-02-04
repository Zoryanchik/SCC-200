import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useStopSearch, useLiveDepartures, useServiceAlerts } from './useTransportData';

vi.mock('../services/transportApi', () => ({
  fetchLiveBusLocations: vi.fn(),
  fetchRailDepartures: vi.fn(),
  fetchBusArrivals: vi.fn(),
  searchStops: vi.fn(),
  getJourneyPlans: vi.fn(),
  fetchServiceAlerts: vi.fn(),
  fetchPricing: vi.fn()
}));

const flushPromises = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('useTransportData hooks', () => {
  afterEach(() => {
    vi.clearAllMocks();
    vi.useRealTimers();
  });

  it('useStopSearch debounces and returns results', async () => {
    vi.useFakeTimers();
    const { searchStops } = await import('../services/transportApi');
    searchStops.mockResolvedValueOnce([{ id: 1, name: 'Lancaster Bus Station' }]);

    const { result } = renderHook(() => useStopSearch('Lan', 200));

    await act(async () => {
      vi.advanceTimersByTime(200);
      await flushPromises();
    });

    expect(searchStops).toHaveBeenCalledWith('Lan');
    expect(result.current.results).toHaveLength(1);
  });

  it('useLiveDepartures fetches data on mount', async () => {
    const { fetchRailDepartures } = await import('../services/transportApi');
    fetchRailDepartures.mockResolvedValueOnce([{ id: 1, destination: 'Preston' }]);

    const { result } = renderHook(() => useLiveDepartures('LAN', 5000));

    await act(async () => {
      await flushPromises();
    });

    expect(fetchRailDepartures).toHaveBeenCalledWith('LAN');
    expect(result.current.data).toHaveLength(1);
  });

  it('useServiceAlerts fetches alerts', async () => {
    const { fetchServiceAlerts } = await import('../services/transportApi');
    fetchServiceAlerts.mockResolvedValueOnce([{ id: 1, message: 'Test alert' }]);

    const { result } = renderHook(() => useServiceAlerts(60000));

    await act(async () => {
      await flushPromises();
    });

    expect(fetchServiceAlerts).toHaveBeenCalledTimes(1);
    expect(result.current.alerts).toHaveLength(1);
  });
});
