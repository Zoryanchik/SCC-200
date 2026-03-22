import React from 'react';
import { describe, test, expect, vi, afterEach, beforeEach } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { render, screen, waitFor, act } from '@testing-library/react';
import * as api from '../../services/transportApi';

// ---- Mocks ----
vi.mock('../../services/transportApi', () => ({
  fetchLiveBusLocations: vi.fn().mockResolvedValue([]),
  fetchRailDepartures: vi.fn().mockResolvedValue([]),
  fetchBusArrivals: vi.fn().mockResolvedValue([]),
  searchStops: vi.fn().mockResolvedValue([]),
  getJourneyPlans: vi.fn().mockResolvedValue([]),
  fetchServiceAlerts: vi.fn().mockResolvedValue([]),
  fetchPricing: vi.fn().mockResolvedValue(null),
  fetchWeatherData: vi.fn().mockResolvedValue({
    weather: [{ main: 'Clear' }],
    main: { temp: 12, humidity: 50 },
    wind: { speed: 2.3 },
  }),
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

// Stub the map component used by the page.
vi.mock('../../components/map/MapViewMap', () => ({
  __esModule: true,
  default: ({ filteredMarkers }) => (
    <div data-testid="map-stub">
      <span data-testid="marker-count">{filteredMarkers.length}</span>
      {filteredMarkers.map((m) => (
        <div key={m.id} data-testid={`marker-${m.type}`}>
          <span data-testid="marker-name">{m.name}</span>
        </div>
      ))}
    </div>
  ),
}));

afterEach(() => {
  vi.clearAllMocks();
  vi.useRealTimers();
});

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });

  // MapViewPage reads a debug flag from localStorage at module scope.
  // JSDOM's localStorage can be missing/stubbed in our test environment,
  // so provide a minimal mock.
  if (typeof window !== 'undefined') {
    // In jsdom, `window.localStorage` may be a non-writable proxy.
    // Define it explicitly to ensure MapViewPage can read `getItem()`.
    const storage = {
      getItem: vi.fn(() => null),
      setItem: vi.fn(() => undefined),
      removeItem: vi.fn(() => undefined),
      clear: vi.fn(() => undefined),
      key: vi.fn(() => null),
      length: 0,
    };

    Object.defineProperty(window, 'localStorage', {
      value: storage,
      configurable: true,
      writable: true,
    });
  }
});

const renderPage = async () => {
  const mod = await import('../map-view-page');
  const MapViewPage = mod.default;
  return render(
    <MemoryRouter>
      <MapViewPage />
    </MemoryRouter>
  );
};

describe('MapViewPage Off-lines toggle', () => {
  test('only hides grey/unmapped buses (never hides mapped buses)', async () => {
    const mockBuses = [
      // mapped bus: match_reason === matched => should always be visible
      { lat: 54.05, lon: -2.8, line: '100', destination: 'Blackpool', match_reason: 'matched' },
      // grey/unmapped bus: explicitly unmatched
      { lat: 54.051, lon: -2.801, line: 'X1', destination: 'Somewhere', meta: { match_reason: 'unmatched' } },
    ];

    api.fetchLiveBusLocations.mockResolvedValue(mockBuses);
    api.fetchRailDepartures.mockResolvedValue([]);

    await renderPage();

    // Default: Off-lines OFF => grey bus hidden
    await waitFor(() => {
      expect(screen.getAllByTestId('marker-bus').length).toBe(1);
      const names = screen.getAllByTestId('marker-name').map((el) => el.textContent || '');
      expect(names.some((t) => t.includes('Blackpool'))).toBe(true);
      expect(names.some((t) => t.includes('Somewhere'))).toBe(false);
    });

    // Turn Off-lines ON => show both
    await act(async () => {
      screen.getByText('Off-lines').click();
    });

    await waitFor(() => {
      expect(screen.getAllByTestId('marker-bus').length).toBe(2);
      const names = screen.getAllByTestId('marker-name').map((el) => el.textContent || '');
      expect(names.some((t) => t.includes('Blackpool'))).toBe(true);
      expect(names.some((t) => t.includes('Somewhere'))).toBe(true);
    });
  });
});
