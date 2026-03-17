import { renderHook, waitFor } from '@testing-library/react'
import { vi } from 'vitest'
import { useStopSearch } from '../useTransportData'
import * as api from '../../services/transportApi'

vi.mock('../../services/transportApi', () => ({
  fetchLiveBusLocations: vi.fn(),
  fetchRailDepartures: vi.fn(),
  fetchBusArrivals: vi.fn(),
  searchStops: vi.fn(),
  getJourneyPlans: vi.fn(),
  fetchServiceAlerts: vi.fn(),
  fetchPricing: vi.fn()
}))

vi.mock('../../services/liveUpdates', () => ({
  liveUpdatesManager: {
    connect: vi.fn().mockRejectedValue(new Error('not available')),
    disconnect: vi.fn().mockResolvedValue(undefined),
    subscribeToTrainMovements: vi.fn(),
    subscribeToBusMovements: vi.fn(),
    subscribeToAlerts: vi.fn(),
    unsubscribe: vi.fn(),
  }
}))

describe('useStopSearch', () => {
  test('returns results after debounce', async () => {
    api.searchStops.mockResolvedValueOnce([{ atco_code: 'ATCO1', name: 'Central Bus Stop', type: 'stop' }])

    const { result } = renderHook(() => useStopSearch('Cent', 10))

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
      expect(result.current.results.length).toBeGreaterThan(0)
    })
  })

  test('returns mixed stop and location results', async () => {
    api.searchStops.mockResolvedValueOnce([
      { id: 1, name: 'Lancaster Bus Station', atco_code: 'LAN001', lat: 54.048, lon: -2.801, type: 'stop' },
      { id: 'loc:0', name: 'Lancaster, UK', atco_code: null, lat: 54.047, lon: -2.801, type: 'location' }
    ])

    const { result } = renderHook(() => useStopSearch('Lancaster', 10))

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
      expect(result.current.results).toHaveLength(2)
      const types = result.current.results.map(r => r.type)
      expect(types).toContain('stop')
      expect(types).toContain('location')
    })
  })

  test('location results include lat/lon for map usage', async () => {
    api.searchStops.mockResolvedValueOnce([
      { id: 'loc:0', name: 'Lancaster, UK', atco_code: null, lat: 54.047, lon: -2.801, type: 'location' }
    ])

    const { result } = renderHook(() => useStopSearch('Lancaster', 10))

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
      const loc = result.current.results[0]
      expect(loc.type).toBe('location')
      expect(typeof loc.lat).toBe('number')
      expect(typeof loc.lon).toBe('number')
      expect(loc.atco_code).toBeNull()
    })
  })


  test('boosts in-viewport results when bbox provided', async () => {
    api.searchStops.mockResolvedValueOnce([
      { id: 1, name: 'Common Garden Street', atco_code: 'C1', lat: 54.050, lon: -2.800, type: 'stop' },
      { id: 2, name: 'Common Road (Out of view)', atco_code: 'C2', lat: 54.150, lon: -2.900, type: 'stop' },
    ])

    const mapCenter = { lat: 54.055, lon: -2.805 }
    const mapBbox = { south: 54.00, west: -2.85, north: 54.10, east: -2.75 }
    const { result } = renderHook(() => useStopSearch('Common', 10, mapCenter, mapBbox))

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
      expect(result.current.results).toHaveLength(2)
    })

    expect(result.current.results[0].name).toBe('Common Garden Street')
  })

  test('ranks upstream stop before merged when both in viewport', async () => {
    api.searchStops.mockResolvedValueOnce([
      { id: 1, name: 'Common Garden Street', atco_code: 'UP1', lat: 54.050, lon: -2.800, type: 'stop', source: 'naptan' },
      { id: 2, name: 'Common Garden Street', atco_code: 'M1', lat: 54.051, lon: -2.801, type: 'stop', source: 'merged' },
    ])

    const mapCenter = { lat: 54.055, lon: -2.805 }
    const mapBbox = { south: 54.00, west: -2.85, north: 54.10, east: -2.75 }
    const { result } = renderHook(() => useStopSearch('Common', 10, mapCenter, mapBbox))

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
      expect(result.current.results).toHaveLength(2)
    })

    expect(result.current.results[0].atco_code).toBe('UP1')
  })
})
