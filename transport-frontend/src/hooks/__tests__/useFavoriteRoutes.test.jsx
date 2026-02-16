import { renderHook, act } from '@testing-library/react'
import { vi } from 'vitest'
import { useFavoriteRoutes } from '../useTransportData'

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

describe('useFavoriteRoutes', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  test('loads empty favorites and can save/remove', () => {
    const { result } = renderHook(() => useFavoriteRoutes())
    expect(result.current.favorites).toEqual([])

    act(() => {
      result.current.saveFavorite({ from: 'A', to: 'B' })
    })

    expect(result.current.favorites.length).toBe(1)
    const fav = result.current.favorites[0]
    expect(fav.from).toBe('A')
    expect(JSON.parse(localStorage.getItem('favoriteRoutes')).length).toBe(1)

    act(() => {
      result.current.removeFavorite('A', 'B')
    })
    expect(result.current.favorites.length).toBe(0)
  })
})
