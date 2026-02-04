import { renderHook, act } from '@testing-library/react'
import { useFavoriteRoutes } from '../useTransportData'

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
