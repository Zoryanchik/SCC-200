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
})
