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
    api.searchStops.mockResolvedValueOnce([{ atco_code: 'ATCO1', name: 'Central Bus Stop' }])

    const { result } = renderHook(() => useStopSearch('Cent', 10))

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
      expect(result.current.results.length).toBeGreaterThan(0)
    })
  })
})
