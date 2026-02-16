import { describe, test, expect, vi, afterEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { useStopSearch, useLiveDepartures, useServiceAlerts } from './useTransportData'
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

afterEach(() => {
  vi.clearAllMocks()
})

describe('useTransportData hooks', () => {
  test('useStopSearch debounces and returns results', async () => {
    api.searchStops.mockResolvedValueOnce([{ atco_code: 'ATCO1', name: 'Lancaster Bus Station', type: 'stop' }])

    const { result } = renderHook(() => useStopSearch('Lan', 10))

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
      expect(api.searchStops).toHaveBeenCalledWith('Lan')
      expect(result.current.results.length).toBeGreaterThan(0)
    })
  })

  test('useStopSearch handles mixed stop and location types', async () => {
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
    api.fetchRailDepartures.mockResolvedValueOnce([{ id: 1, destination: 'Preston' }])

    const { result } = renderHook(() => useLiveDepartures('LAN', 10000))

    await waitFor(() => {
      expect(api.fetchRailDepartures).toHaveBeenCalledWith('LAN')
      expect(result.current.data.length).toBeGreaterThan(0)
    })
  })

  test('useServiceAlerts fetches alerts', async () => {
    api.fetchServiceAlerts.mockResolvedValueOnce([{ id: 1, message: 'Test alert' }])

    const { result } = renderHook(() => useServiceAlerts(10000))

    await waitFor(() => {
      expect(api.fetchServiceAlerts).toHaveBeenCalled()
      expect(result.current.alerts.length).toBeGreaterThan(0)
    })
  })
})
