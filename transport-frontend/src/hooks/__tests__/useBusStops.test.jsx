/**
 * Tests for useBusStops hook
 *
 * Validates:
 *  - Fetches stops on mount and exposes them
 *  - Sets loading state correctly
 *  - Handles errors gracefully
 *  - enabled=false prevents fetch
 *  - refetch triggers a re-fetch
 *  - Classification parameter is forwarded
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { useBusStops } from '../useBusStops';

// Mock the busStopsApi module
vi.mock('../../services/busStopsApi', () => ({
  getBusStopsWithFallback: vi.fn(),
}));

import { getBusStopsWithFallback } from '../../services/busStopsApi';

const FAKE_STOPS = [
  { id: 'stop-1', name: 'Stop A', lat: 54.05, lon: -2.80, classification: 'hub', lines: ['1'] },
  { id: 'stop-2', name: 'Stop B', lat: 54.06, lon: -2.81, classification: 'local', lines: ['2'] },
];

beforeEach(() => {
  vi.clearAllMocks();
  getBusStopsWithFallback.mockResolvedValue(FAKE_STOPS);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('useBusStops', () => {
  it('fetches stops on mount and exposes them', async () => {
    const { result } = renderHook(() => useBusStops());

    // Initially loading
    expect(result.current.loading).toBe(true);

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.stops).toEqual(FAKE_STOPS);
    expect(result.current.error).toBeNull();
    expect(getBusStopsWithFallback).toHaveBeenCalledTimes(1);
  });

  it('sets error state when fetch fails', async () => {
    getBusStopsWithFallback.mockRejectedValue(new Error('API down'));

    const { result } = renderHook(() => useBusStops());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.stops).toEqual([]);
    expect(result.current.error).toBe('API down');
  });

  it('does not fetch when enabled=false', async () => {
    const { result } = renderHook(() => useBusStops({ enabled: false }));

    // Give it a tick to ensure no async call happens
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50));
    });

    expect(getBusStopsWithFallback).not.toHaveBeenCalled();
    expect(result.current.stops).toEqual([]);
    expect(result.current.loading).toBe(false);
  });

  it('forwards classification to the API', async () => {
    const { result } = renderHook(() =>
      useBusStops({ classification: 'hub' }),
    );

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(getBusStopsWithFallback).toHaveBeenCalledWith({
      classification: 'hub',
    });
  });

  it('refetch triggers a new API call', async () => {
    const { result } = renderHook(() => useBusStops());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(getBusStopsWithFallback).toHaveBeenCalledTimes(1);

    const updatedStops = [
      { id: 'stop-3', name: 'Stop C', lat: 54.07, lon: -2.82, classification: 'local', lines: ['3'] },
    ];
    getBusStopsWithFallback.mockResolvedValue(updatedStops);

    await act(async () => {
      result.current.refetch();
    });

    await waitFor(() => {
      expect(result.current.stops).toEqual(updatedStops);
    });

    expect(getBusStopsWithFallback).toHaveBeenCalledTimes(2);
  });

  it('re-fetches when classification changes', async () => {
    const { result, rerender } = renderHook(
      ({ classification }) => useBusStops({ classification }),
      { initialProps: { classification: undefined } },
    );

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(getBusStopsWithFallback).toHaveBeenCalledTimes(1);

    rerender({ classification: 'interchange' });

    await waitFor(() => {
      expect(getBusStopsWithFallback).toHaveBeenCalledTimes(2);
    });

    expect(getBusStopsWithFallback).toHaveBeenLastCalledWith({
      classification: 'interchange',
    });
  });
});
