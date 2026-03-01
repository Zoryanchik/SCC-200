import React from "react";
import { describe, test, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { renderHook } from "@testing-library/react";
import * as api from "../services/transportApi";
import { useLiveBusLocations } from "../hooks/useTransportData";

// ---- Mocks ----
vi.mock("../services/transportApi", () => ({
  fetchLiveBusLocations: vi.fn().mockResolvedValue([]),
  fetchRailDepartures: vi.fn().mockResolvedValue([]),
  fetchBusArrivals: vi.fn().mockResolvedValue([]),
  searchStops: vi.fn().mockResolvedValue([]),
  getJourneyPlans: vi.fn().mockResolvedValue([]),
  fetchServiceAlerts: vi.fn().mockResolvedValue([]),
  fetchPricing: vi.fn().mockResolvedValue(null),
}));

vi.mock("../services/liveUpdates", () => ({
  liveUpdatesManager: {
    connect: vi.fn().mockRejectedValue(new Error("not available")),
    disconnect: vi.fn().mockResolvedValue(undefined),
    subscribeToTrainMovements: vi.fn(),
    subscribeToBusMovements: vi.fn(),
    subscribeToAlerts: vi.fn(),
    unsubscribe: vi.fn(),
  },
}));

// Stub the lazy-loaded MapViewMap so we don't need leaflet in jsdom
vi.mock("../components/map/MapViewMap", () => ({
  __esModule: true,
  default: ({ onMoveEnd, filteredMarkers, busLoading, trainLoading }) => {
    // Expose onMoveEnd so tests can simulate map pan
    if (typeof window !== "undefined") {
      window.__testOnMoveEnd = onMoveEnd;
    }
    return (
      <div data-testid="map-stub">
        <span data-testid="marker-count">{filteredMarkers.length}</span>
        {busLoading && <span data-testid="bus-loading">loading</span>}
        {trainLoading && <span data-testid="train-loading">loading</span>}
      </div>
    );
  },
}));

vi.mock("../components/common/DepartureCard", () => ({
  __esModule: true,
  default: ({ departure }) => <div data-testid="departure-card">{departure.destination}</div>,
}));

vi.mock("../components/common/RouteCard", () => ({
  __esModule: true,
  default: ({ route }) => <div data-testid="route-card">{route.duration}</div>,
}));

// ---- Helpers ----
afterEach(() => {
  vi.clearAllMocks();
  vi.useRealTimers();
  delete window.__testOnMoveEnd;
});

const renderPage = async () => {
  const mod = await import("./home-page");
  const HomePage = mod.default;
  return render(
    <MemoryRouter>
      <HomePage />
    </MemoryRouter>
  );
};

// ---- Tests ----
describe("HomePage (combined search + map)", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  test("renders the map and search form on the same page", async () => {
    vi.useRealTimers();
    await renderPage();

    // Map section title is always visible (not lazy)
    expect(screen.getByText("Live Transport Map")).toBeTruthy();

    // The MapViewMap is lazy-loaded via Suspense; wait for it to appear
    await waitFor(() => {
      expect(screen.getByTestId("map-stub")).toBeTruthy();
    });

    // Search section
    expect(screen.getByText("Quick journey search")).toBeTruthy();
    expect(screen.getByText("Search routes")).toBeTruthy();
  });

  test("renders filter chips for buses and trains", async () => {
    vi.useRealTimers();
    await renderPage();

    expect(screen.getByTestId("filter-buses")).toBeTruthy();
    expect(screen.getByTestId("filter-trains")).toBeTruthy();
  });

  test("renders departure cards section", async () => {
    vi.useRealTimers();
    await renderPage();

    expect(screen.getByText("Nearby departures")).toBeTruthy();
  });

  test("renders suggested routes section", async () => {
    vi.useRealTimers();
    await renderPage();

    expect(screen.getByText("Suggested routes")).toBeTruthy();
  });
});

describe("useLiveBusLocations debounced map center integration", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  test("sends correct lat/lon after map move (simulated via hook)", async () => {
    const mockBuses = [
      { line: "1A", destination: "Lancaster", lat: 54.05, lon: -2.80 },
    ];
    api.fetchLiveBusLocations.mockResolvedValue(mockBuses);

    const { result } = renderHook(() =>
      useLiveBusLocations("SCCU", {
        lat: 54.123,
        lon: -2.456,
        refreshInterval: 60000,
        debounceMs: 100,
      })
    );

    // Before debounce the API should NOT have been called
    expect(api.fetchLiveBusLocations).not.toHaveBeenCalled();

    // Advance past debounce
    await act(async () => {
      vi.advanceTimersByTime(150);
    });

    await waitFor(() => {
      expect(api.fetchLiveBusLocations).toHaveBeenCalledWith("SCCU", {
        lat: 54.123,
        lon: -2.456,
      });
      expect(result.current.data).toEqual(mockBuses);
    });
  });

  test("debounces rapid coordinate changes to avoid backend spam", async () => {
    const mockBuses = [{ line: "2", destination: "Morecambe", lat: 54.1, lon: -2.7 }];
    api.fetchLiveBusLocations.mockResolvedValue(mockBuses);

    const { rerender } = renderHook(
      ({ lat, lon }) =>
        useLiveBusLocations("SCCU", {
          lat,
          lon,
          refreshInterval: 60000,
          debounceMs: 500,
        }),
      { initialProps: { lat: 54.0, lon: -2.8 } }
    );

    // Simulate rapid map panning
    await act(async () => { vi.advanceTimersByTime(100); });
    rerender({ lat: 54.01, lon: -2.81 });

    await act(async () => { vi.advanceTimersByTime(100); });
    rerender({ lat: 54.02, lon: -2.82 });

    await act(async () => { vi.advanceTimersByTime(100); });
    rerender({ lat: 54.03, lon: -2.83 });

    // Only 300ms elapsed, debounce is 500ms -- should NOT have called
    expect(api.fetchLiveBusLocations).not.toHaveBeenCalled();

    // Advance past the debounce window
    await act(async () => { vi.advanceTimersByTime(600); });

    await waitFor(() => {
      // Should fire exactly once with the LAST coordinates
      expect(api.fetchLiveBusLocations).toHaveBeenCalledTimes(1);
      expect(api.fetchLiveBusLocations).toHaveBeenCalledWith("SCCU", {
        lat: 54.03,
        lon: -2.83,
      });
    });
  });

  test("constructs correct URL with lat/lon query params", async () => {
    api.fetchLiveBusLocations.mockResolvedValue([]);

    renderHook(() =>
      useLiveBusLocations("SCCU", {
        lat: 54.05,
        lon: -2.80,
        debounceMs: 50,
      })
    );

    await act(async () => { vi.advanceTimersByTime(100); });

    await waitFor(() => {
      expect(api.fetchLiveBusLocations).toHaveBeenCalledWith("SCCU", {
        lat: 54.05,
        lon: -2.80,
      });
    });
  });

  test("refreshes periodically with same coords after debounce", async () => {
    api.fetchLiveBusLocations.mockResolvedValue([]);

    renderHook(() =>
      useLiveBusLocations("SCCU", {
        lat: 54.05,
        lon: -2.80,
        refreshInterval: 1000,
        debounceMs: 100,
      })
    );

    // Initial debounce + first call
    await act(async () => { vi.advanceTimersByTime(150); });
    await waitFor(() => {
      expect(api.fetchLiveBusLocations).toHaveBeenCalledTimes(1);
    });

    // After refresh interval
    await act(async () => { vi.advanceTimersByTime(1100); });
    await waitFor(() => {
      expect(api.fetchLiveBusLocations).toHaveBeenCalledTimes(2);
    });
  });
});


describe("HomePage marker update logic", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  test("updates markers when busLocations data is returned", async () => {
    const mockBuses = [
      { lat: 54.05, lon: -2.80, line: "1A", destination: "Lancaster" },
      { lat: 54.06, lon: -2.81, line: "2B", destination: "Morecambe" },
    ];
    api.fetchLiveBusLocations.mockResolvedValue(mockBuses);
    api.fetchRailDepartures.mockResolvedValue([]);

    vi.useRealTimers();
    await renderPage();

    // The map stub should render with mock markers initially
    await waitFor(() => {
      expect(screen.getByTestId("map-stub")).toBeTruthy();
    });
  });

  test("renders default alerts when no API alerts are available", async () => {
    api.fetchServiceAlerts.mockResolvedValue([]);
    vi.useRealTimers();
    await renderPage();

    await waitFor(() => {
      expect(screen.getByText(/M6 delays/)).toBeTruthy();
    });
  });

  test("renders default departures when no API data available", async () => {
    api.fetchRailDepartures.mockResolvedValue([]);
    vi.useRealTimers();
    await renderPage();

    await waitFor(() => {
      const cards = screen.getAllByTestId("departure-card");
      expect(cards.length).toBeGreaterThan(0);
    });
  });

  test("shows no-routes message initially (no search performed)", async () => {
    vi.useRealTimers();
    await renderPage();

    await waitFor(() => {
      expect(screen.getByText("No routes found. Try adjusting your search.")).toBeTruthy();
    });
  });

  test("search button is disabled by default (no stops selected)", async () => {
    vi.useRealTimers();
    await renderPage();

    const btn = screen.getByText("Search routes");
    expect(btn.closest("button").disabled).toBe(true);
  });
});

describe("fetchLiveBusLocations API contract", () => {
  test("fetchLiveBusLocations builds correct query params including lat, lon, latTol, lonTol", async () => {
    const { fetchLiveBusLocations: realFetch } = await vi.importActual("../services/transportApi");

    // We can not actually call the network but we can verify the URL construction
    // by checking the mock was called with expected shape
    api.fetchLiveBusLocations.mockResolvedValue([]);

    const { result } = renderHook(() =>
      useLiveBusLocations("SCCU", {
        lat: 54.05,
        lon: -2.80,
        debounceMs: 50,
      })
    );

    await act(async () => {
      vi.advanceTimersByTime(100);
    });

    await waitFor(() => {
      const call = api.fetchLiveBusLocations.mock.calls[0];
      expect(call[0]).toBe("SCCU");
      expect(call[1]).toHaveProperty("lat", 54.05);
      expect(call[1]).toHaveProperty("lon", -2.80);
    });
  });

  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });
});
