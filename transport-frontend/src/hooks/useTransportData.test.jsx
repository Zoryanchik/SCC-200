import { describe, test, expect, vi, afterEach, beforeEach } from 'vitest'
import { renderHook, waitFor, act } from '@testing-library/react'
import { useStopSearch, useLiveDepartures, useServiceAlerts, useLiveBusLocations } from './useTransportData'
import * as api from '../services/transportApi'

vi.mock('../services/transportApi', () => ({
  fetchLiveBusLocations: vi.fn(),
  fetchRailDepartures: vi.fn(),
  fetchBusArrivals: vi.fn(),
  searchStops: vi.fn(),
  getJourneyPlans: vi.fn(),
  fetchServiceAlerts: vi.fn(),
  fetchPricing: vi.fn()
}))

vi.mock('../services/liveUpdates', () => ({
  liveUpdatesManager: {
    connect: vi.fn().mockRejectedValue(new Error('not available')),
    disconnect: vi.fn().mockResolvedValue(undefined),
    subscribeToTrainMovements: vi.fn(),
    subscribeToBusMovements: vi.fn(),
    subscribeToAlerts: vi.fn(),
    unsubscribe: vi.fn(),
  }
}))

afterEach(() => {
  vi.clearAllMocks()
  vi.useRealTimers()
})

describe('useTransportData hooks', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
  })

  test('useStopSearch debounces and returns results', async () => {
    vi.useRealTimers()
    api.searchStops.mockResolvedValueOnce([{ atco_code: 'ATCO1', name: 'Lancaster Bus Station', type: 'stop' }])

    const { result } = renderHook(() => useStopSearch('Lan', 10))

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
      // useStopSearch calls the backend with just the query string.
      expect(api.searchStops).toHaveBeenCalledWith('Lan')
      expect(result.current.results.length).toBeGreaterThan(0)
    })
  })

  test('useStopSearch handles mixed stop and location types', async () => {
    vi.useRealTimers()
    api.searchStops.mockResolvedValueOnce([
      { id: 1, name: 'Lancaster Bus Station', atco_code: 'LAN001', lat: 54.048, lon: -2.801, type: 'stop' },
      { id: 'loc:0', name: 'Lancaster, Lancashire, UK', atco_code: null, lat: 54.047, lon: -2.801, type: 'location' }
    ])

    const { result } = renderHook(() => useStopSearch('Lancaster', 10))

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
      expect(result.current.results).toHaveLength(2)
      expect(result.current.results[0].type).toBe('stop')
      expect(result.current.results[1].type).toBe('location')
    })
  })

  test('useLiveDepartures fetches data on mount', async () => {
    vi.useRealTimers()
    api.fetchRailDepartures.mockResolvedValueOnce([{ id: 1, destination: 'Preston' }])

    const { result } = renderHook(() => useLiveDepartures(['LAN'], 10000))

    await waitFor(() => {
      expect(api.fetchRailDepartures).toHaveBeenCalledWith(['LAN'])
      expect(result.current.data.length).toBeGreaterThan(0)
    })
  })

  test('useServiceAlerts fetches alerts', async () => {
    vi.useRealTimers()
    api.fetchServiceAlerts.mockResolvedValueOnce([{ id: 1, message: 'Test alert' }])

    const { result } = renderHook(() => useServiceAlerts(10000))

    await waitFor(() => {
      expect(api.fetchServiceAlerts).toHaveBeenCalled()
      expect(result.current.alerts.length).toBeGreaterThan(0)
    })
  })
})

describe('useLiveBusLocations', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
  })

  test('passes lat and lon to fetchLiveBusLocations after debounce', async () => {
    const mockBuses = [
      { line: '1A', destination: 'Lancaster', lat: 54.05, lon: -2.80 }
    ]
    api.fetchLiveBusLocations.mockResolvedValue(mockBuses)

    const { result } = renderHook(() =>
      useLiveBusLocations('SCCU', {
        lat: 54.05,
        lon: -2.80,
        refreshInterval: 60000,
        debounceMs: 100,
      })
    )

    // Before debounce fires, the API should NOT have been called
    expect(api.fetchLiveBusLocations).not.toHaveBeenCalled()

    // Advance past debounce
    await act(async () => {
      vi.advanceTimersByTime(150)
    })

    await waitFor(() => {
      expect(api.fetchLiveBusLocations).toHaveBeenCalledWith('SCCU', {
        lat: 54.05,
        lon: -2.80,
      })
      expect(result.current.data).toEqual(mockBuses)
      expect(result.current.loading).toBe(false)
      expect(result.current.error).toBeNull()
    })
  })

  test('debounces rapid lat/lon changes and only fires once', async () => {
    const mockBuses = [{ line: '2', destination: 'Morecambe', lat: 54.1, lon: -2.7 }]
    api.fetchLiveBusLocations.mockResolvedValue(mockBuses)

    const { rerender } = renderHook(
      ({ lat, lon }) =>
        useLiveBusLocations('SCCU', {
          lat,
          lon,
          refreshInterval: 60000,
          debounceMs: 200,
        }),
      { initialProps: { lat: 54.0, lon: -2.8 } }
    )

    // Simulate rapid map panning (multiple re-renders with different centers)
    await act(async () => { vi.advanceTimersByTime(50) })
    rerender({ lat: 54.01, lon: -2.81 })

    await act(async () => { vi.advanceTimersByTime(50) })
    rerender({ lat: 54.02, lon: -2.82 })

    await act(async () => { vi.advanceTimersByTime(50) })
    rerender({ lat: 54.03, lon: -2.83 })

    // At this point only 150ms have passed; debounce is 200ms
    // API should NOT have been called yet
    expect(api.fetchLiveBusLocations).not.toHaveBeenCalled()

    // Advance past final debounce
    await act(async () => { vi.advanceTimersByTime(250) })

    await waitFor(() => {
      // Should only have been called ONCE with the LAST coordinates
      expect(api.fetchLiveBusLocations).toHaveBeenCalledTimes(1)
      expect(api.fetchLiveBusLocations).toHaveBeenCalledWith('SCCU', {
        lat: 54.03,
        lon: -2.83,
      })
    })
  })

  test('calls API without lat/lon when they are undefined', async () => {
    api.fetchLiveBusLocations.mockResolvedValue([])

    renderHook(() =>
      useLiveBusLocations('SCCU', { debounceMs: 50 })
    )

    await act(async () => { vi.advanceTimersByTime(100) })

    await waitFor(() => {
      expect(api.fetchLiveBusLocations).toHaveBeenCalledWith('SCCU', {})
    })
  })

  test('does not call API when operatorCode is falsy', async () => {
    renderHook(() =>
      useLiveBusLocations('', { lat: 54.05, lon: -2.80, debounceMs: 50 })
    )

    await act(async () => { vi.advanceTimersByTime(100) })

    expect(api.fetchLiveBusLocations).not.toHaveBeenCalled()
  })

  test('sets error state when API call fails', async () => {
    // Use real timers for error test to avoid interference with retry delays
    vi.useRealTimers()
    api.fetchLiveBusLocations.mockRejectedValue(new Error('Network error'))

    const { result } = renderHook(() =>
      useLiveBusLocations('SCCU', {
        lat: 54.05,
        lon: -2.80,
        debounceMs: 10,
      })
    )

    await waitFor(() => {
      expect(result.current.error).toBeTruthy()
      expect(result.current.loading).toBe(false)
    }, { timeout: 10000 })
  })

  test('refetch returns a callable function', async () => {
    api.fetchLiveBusLocations.mockResolvedValue([])

    const { result } = renderHook(() =>
      useLiveBusLocations('SCCU', {
        lat: 54.05,
        lon: -2.80,
        debounceMs: 50,
      })
    )

    await act(async () => { vi.advanceTimersByTime(100) })

    await waitFor(() => {
      expect(typeof result.current.refetch).toBe('function')
    })
  })
})
