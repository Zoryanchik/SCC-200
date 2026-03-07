/**
 * Tests for BusStopLayer, BusStopMarker, and ArrivalsPanel components
 *
 * Validates:
 *  - BusStopMarker renders a Marker with correct position & SVG icon
 *  - BusStopMarker popup shows stop name, classification, lines
 *  - BusStopMarker renders ArrivalsPanel when popup is opened
 *  - ArrivalsPanel fetches and displays arrivals, loading, error, empty states
 *  - BusStopLayer renders nothing when zoom < minZoom
 *  - BusStopLayer renders markers when zoom ≥ minZoom
 *  - BusStopLayer only renders stops within map bounds
 *  - BusStopLayer passes classification to hook
 *
 * Note: react-leaflet components require a MapContainer parent.
 * We mock react-leaflet and leaflet to isolate component logic.
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, act, waitFor } from '@testing-library/react';

// ── Mock leaflet ────────────────────────────────────────────────────
// Capture the icon config passed to L.divIcon so we can assert on it.
vi.mock('leaflet', () => ({
  default: {
    divIcon: (opts) => opts,
  },
  divIcon: (opts) => opts,
}));

// ── Mock react-leaflet ──────────────────────────────────────────────
let mockZoom = 14;
const mockBounds = {
  getSouth: () => 53.0,
  getNorth: () => 55.0,
  getWest: () => -3.5,
  getEast: () => -2.0,
};

// Captures registered Leaflet event handlers so tests can fire them.
const mapHandlers = {};
const mockMapInstance = {
  getZoom: () => mockZoom,
  getBounds: () => mockBounds,
  on: vi.fn((event, handler) => { mapHandlers[event] = handler; }),
  off: vi.fn((event) => { delete mapHandlers[event]; }),
};

// Captures Popup eventHandlers so tests can simulate popup open/close.
let capturedPopupHandlers = {};

vi.mock('react-leaflet', () => ({
  Marker: ({ children, position, icon, ...rest }) => (
    <div
      data-testid={rest['data-testid'] || 'marker'}
      data-position={JSON.stringify(position)}
      data-icon-html={typeof icon === 'object' ? icon.html : undefined}
      data-icon-size={icon?.iconSize ? JSON.stringify(icon.iconSize) : undefined}
    >
      {children}
    </div>
  ),
  Popup: ({ children, eventHandlers }) => {
    if (eventHandlers) capturedPopupHandlers = eventHandlers;
    return <div data-testid="popup">{children}</div>;
  },
  useMap: () => mockMapInstance,
}));

// ── Mock fetchBusArrivals ───────────────────────────────────────────────
vi.mock('../../../services/busStopsApi', () => ({
  fetchBusArrivals: vi.fn(),
}));

import { fetchBusArrivals } from '../../../services/busStopsApi';

const FAKE_ARRIVALS = [
  { line: '1', destination: 'Lancaster Bus Station', scheduledTime: '12:00:00', status: 'On time' },
  { line: '100', destination: 'Lancaster University', scheduledTime: '12:15:00', status: 'On time' },
];

// ── Mock useBusStops hook ───────────────────────────────────────────
const mockStops = [
  {
    id: 'stop-1',
    name: 'Lancaster Bus Station',
    lat: 54.04895,
    lon: -2.80117,
    atco_code: '2500LAA12000',
    classification: 'hub',
    lines: ['1', '2', '40'],
  },
  {
    id: 'stop-2',
    name: 'Uni Underpass',
    lat: 54.0101,
    lon: -2.7852,
    atco_code: '2500B0615',
    classification: 'interchange',
    lines: ['1', '4'],
  },
  {
    id: 'stop-3',
    name: 'Out of Bounds Stop',
    lat: 51.0, // Way south — outside mock bounds
    lon: -2.5,
    atco_code: '9900OOB001',
    classification: 'local',
    lines: ['99'],
  },
];

let hookArgs = {};
vi.mock('../../../hooks/useBusStops', () => ({
  useBusStops: (opts) => {
    hookArgs = opts;
    return { stops: mockStops, loading: false, error: null, refetch: vi.fn() };
  },
}));

import BusStopLayer, { BusStopMarker, ArrivalsPanel } from '../BusStopLayer';

beforeEach(() => {
  mockZoom = 14;
  hookArgs = {};
  capturedPopupHandlers = {};
  // Clear captured handlers so each test starts clean.
  Object.keys(mapHandlers).forEach((k) => delete mapHandlers[k]);
  vi.clearAllMocks();
  // Default: arrivals API returns fake data
  fetchBusArrivals.mockResolvedValue(FAKE_ARRIVALS);
});

afterEach(() => {
  vi.restoreAllMocks();
});

// ── BusStopMarker unit tests ────────────────────────────────────────

describe('BusStopMarker', () => {
  const stop = mockStops[0]; // Lancaster Bus Station (hub)

  it('renders a Marker with correct position', () => {
    render(<BusStopMarker stop={stop} zoom={16} />);

    const marker = screen.getByTestId(`bus-stop-marker-${stop.id}`);
    expect(marker).toBeTruthy();
    const position = JSON.parse(marker.getAttribute('data-position'));
    expect(position).toEqual([stop.lat, stop.lon]);
  });

  it('uses the hub colour in the SVG icon', () => {
    render(<BusStopMarker stop={stop} zoom={16} />);

    const marker = screen.getByTestId(`bus-stop-marker-${stop.id}`);
    const html = marker.getAttribute('data-icon-html');
    // Hub colour is #E91E63
    expect(html).toContain('#E91E63');
    // Should contain SVG elements (circle + line for bus-stop sign)
    expect(html).toContain('<circle');
    expect(html).toContain('<line');
  });

  it('uses the interchange colour in the SVG icon', () => {
    render(<BusStopMarker stop={mockStops[1]} zoom={16} />);

    const marker = screen.getByTestId(`bus-stop-marker-${mockStops[1].id}`);
    const html = marker.getAttribute('data-icon-html');
    expect(html).toContain('#FF9800');
  });

  it('renders the stop name in the popup', () => {
    render(<BusStopMarker stop={stop} zoom={16} />);

    const name = screen.getByTestId('bus-stop-name');
    expect(name.textContent).toBe('Lancaster Bus Station');
  });

  it('renders classification badge', () => {
    render(<BusStopMarker stop={stop} zoom={16} />);

    const badge = screen.getByTestId('bus-stop-classification');
    expect(badge.textContent).toBe('Hub');
  });

  it('renders ATCO code when present', () => {
    render(<BusStopMarker stop={stop} zoom={16} />);

    const atco = screen.getByTestId('bus-stop-atco');
    expect(atco.textContent).toBe('2500LAA12000');
  });

  it('renders bus route lines', () => {
    render(<BusStopMarker stop={stop} zoom={16} />);

    const linesContainer = screen.getByTestId('bus-stop-lines');
    expect(linesContainer).toBeTruthy();
    for (const line of stop.lines) {
      expect(linesContainer.textContent).toContain(line);
    }
  });

  it('does not render lines section when lines is empty', () => {
    const noLinesStop = { ...stop, lines: [] };
    render(<BusStopMarker stop={noLinesStop} zoom={16} />);

    expect(screen.queryByTestId('bus-stop-lines')).toBeNull();
  });

  it('does not render ATCO code when null', () => {
    const noAtcoStop = { ...stop, atco_code: null };
    render(<BusStopMarker stop={noAtcoStop} zoom={16} />);

    expect(screen.queryByTestId('bus-stop-atco')).toBeNull();
  });

  it('uses a smaller icon at zoom 14 and larger at zoom 17', () => {
    const { unmount } = render(<BusStopMarker stop={stop} zoom={14} />);
    let marker = screen.getByTestId(`bus-stop-marker-${stop.id}`);
    let size = JSON.parse(marker.getAttribute('data-icon-size'));
    const smallWidth = size[0];
    unmount();

    render(<BusStopMarker stop={stop} zoom={17} />);
    marker = screen.getByTestId(`bus-stop-marker-${stop.id}`);
    size = JSON.parse(marker.getAttribute('data-icon-size'));
    const largeWidth = size[0];

    expect(largeWidth).toBeGreaterThan(smallWidth);
  });

  it('SVG icon contains both a circle and a post line', () => {
    render(<BusStopMarker stop={stop} zoom={16} />);

    const marker = screen.getByTestId(`bus-stop-marker-${stop.id}`);
    const html = marker.getAttribute('data-icon-html');
    expect(html).toContain('<svg');
    expect(html).toContain('<circle');
    expect(html).toContain('<line');
  });
});

// ── BusStopLayer unit tests ─────────────────────────────────────────

describe('BusStopLayer', () => {
  it('renders markers for stops within bounds when zoom ≥ minZoom', () => {
    mockZoom = 14;
    render(<BusStopLayer />);

    // stop-1 and stop-2 are within bounds; stop-3 (lat 51.0) is outside
    expect(screen.getByTestId('bus-stop-marker-stop-1')).toBeTruthy();
    expect(screen.getByTestId('bus-stop-marker-stop-2')).toBeTruthy();
    expect(screen.queryByTestId('bus-stop-marker-stop-3')).toBeNull();
  });

  it('renders nothing when zoom < minZoom', () => {
    mockZoom = 8;
    render(<BusStopLayer />);

    expect(screen.queryByTestId('bus-stop-marker-stop-1')).toBeNull();
    expect(screen.queryByTestId('bus-stop-marker-stop-2')).toBeNull();
  });

  it('respects custom minZoom prop', () => {
    mockZoom = 11;
    render(<BusStopLayer minZoom={11} />);

    expect(screen.getByTestId('bus-stop-marker-stop-1')).toBeTruthy();
  });

  it('forwards classification to the hook', () => {
    render(<BusStopLayer classification="hub" />);

    expect(hookArgs.classification).toBe('hub');
  });

  it('forwards enabled to the hook', () => {
    render(<BusStopLayer enabled={false} />);

    expect(hookArgs.enabled).toBe(false);
  });

  it('passes bbox string to hook when zoom \u2265 minZoom', () => {
    mockZoom = 14;
    render(<BusStopLayer />);

    // south,west,north,east from mockBounds
    expect(hookArgs.bbox).toBe('53,-3.5,55,-2');
  });

  it('passes null bbox to hook when zoom < minZoom', () => {
    mockZoom = 8;
    render(<BusStopLayer />);

    expect(hookArgs.bbox).toBeNull();
  });

  it('updates bbox when moveend fires', async () => {
    mockZoom = 14;
    render(<BusStopLayer />);

    expect(hookArgs.bbox).toBe('53,-3.5,55,-2');

    await act(async () => {
      mapHandlers['moveend']?.();
    });

    // Still computes from current (unchanged) mockBounds
    expect(hookArgs.bbox).toBe('53,-3.5,55,-2');
  });

  it('sets bbox to null on zoomend when below minZoom', async () => {
    mockZoom = 14;
    render(<BusStopLayer />);
    expect(hookArgs.bbox).toBeTruthy();

    // Simulate zoom out below threshold
    mockZoom = 8;
    await act(async () => {
      mapHandlers['zoomend']?.();
    });

    expect(hookArgs.bbox).toBeNull();
  });

  it('registers both zoomend and moveend listeners', () => {
    render(<BusStopLayer />);

    const events = mockMapInstance.on.mock.calls.map(([e]) => e);
    expect(events).toContain('zoomend');
    expect(events).toContain('moveend');
  });
});

// ── BusStopMarker — popup + arrivals ────────────────────────────────

describe('BusStopMarker — arrivals on popup open', () => {
  const stop = mockStops[0]; // Lancaster Bus Station (hub)

  it('does not render ArrivalsPanel before popup is opened', () => {
    render(<BusStopMarker stop={stop} zoom={16} />);
    // arrivals-loading should NOT be present because popup is closed
    expect(screen.queryByTestId('arrivals-loading')).toBeNull();
  });

  it('renders ArrivalsPanel (loading state) when popup opens', async () => {
    // Make fetchBusArrivals never resolve so we stay in loading state
    fetchBusArrivals.mockImplementation(() => new Promise(() => {}));
    render(<BusStopMarker stop={stop} zoom={16} />);

    await act(async () => {
      capturedPopupHandlers.add?.();
    });

    expect(screen.getByTestId('arrivals-loading')).toBeTruthy();
  });

  it('renders arrivals list after fetch resolves', async () => {
    render(<BusStopMarker stop={stop} zoom={16} />);

    await act(async () => {
      capturedPopupHandlers.add?.();
    });

    await waitFor(() => expect(screen.getByTestId('arrivals-panel')).toBeTruthy());
    const rows = screen.getAllByTestId(/^arrival-row-/);
    expect(rows).toHaveLength(2);
  });

  it('calls fetchBusArrivals with the stop atco_code', async () => {
    render(<BusStopMarker stop={stop} zoom={16} />);

    await act(async () => {
      capturedPopupHandlers.add?.();
    });

    await waitFor(() => expect(fetchBusArrivals).toHaveBeenCalledWith(stop.atco_code));
  });

  it('hides arrivals panel after popup closes', async () => {
    render(<BusStopMarker stop={stop} zoom={16} />);

    await act(async () => { capturedPopupHandlers.add?.(); });
    await waitFor(() => screen.getByTestId('arrivals-panel'));

    await act(async () => { capturedPopupHandlers.remove?.(); });

    expect(screen.queryByTestId('arrivals-panel')).toBeNull();
    expect(screen.queryByTestId('arrivals-loading')).toBeNull();
  });
});

// ── ArrivalsPanel unit tests ────────────────────────────────────────

describe('ArrivalsPanel', () => {
  it('shows loading state initially', () => {
    fetchBusArrivals.mockImplementation(() => new Promise(() => {}));
    render(<ArrivalsPanel atcoCode="2500LAA12000" />);

    expect(screen.getByTestId('arrivals-loading')).toBeTruthy();
  });

  it('renders arrivals list after successful fetch', async () => {
    render(<ArrivalsPanel atcoCode="2500LAA12000" />);

    await waitFor(() => expect(screen.getByTestId('arrivals-panel')).toBeTruthy());
    const rows = screen.getAllByTestId(/^arrival-row-/);
    expect(rows).toHaveLength(FAKE_ARRIVALS.length);
  });

  it('renders line, destination and time for each arrival', async () => {
    render(<ArrivalsPanel atcoCode="2500LAA12000" />);

    await waitFor(() => screen.getByTestId('arrivals-panel'));
    const lines = screen.getAllByTestId('arrival-line');
    expect(lines[0].textContent).toBe('1');
    const destinations = screen.getAllByTestId('arrival-destination');
    expect(destinations[0].textContent).toBe('Lancaster Bus Station');
    const times = screen.getAllByTestId('arrival-time');
    expect(times[0].textContent).toBe('12:00:00');
  });

  it('shows empty state when API returns zero arrivals', async () => {
    fetchBusArrivals.mockResolvedValue([]);
    render(<ArrivalsPanel atcoCode="2500LAA12000" />);

    await waitFor(() => expect(screen.getByTestId('arrivals-empty')).toBeTruthy());
  });

  it('shows error state when fetch throws', async () => {
    fetchBusArrivals.mockRejectedValue(new Error('API down'));
    render(<ArrivalsPanel atcoCode="2500LAA12000" />);

    await waitFor(() => expect(screen.getByTestId('arrivals-error')).toBeTruthy());
  });

  it('calls fetchBusArrivals with the provided ATCO code', async () => {
    render(<ArrivalsPanel atcoCode="2500TEST001" />);

    await waitFor(() => expect(fetchBusArrivals).toHaveBeenCalledWith('2500TEST001'));
  });

  it('re-fetches when atcoCode changes', async () => {
    const { rerender } = render(<ArrivalsPanel atcoCode="2500LAA12000" />);
    await waitFor(() => expect(fetchBusArrivals).toHaveBeenCalledTimes(1));

    rerender(<ArrivalsPanel atcoCode="2500B0615" />);
    await waitFor(() => expect(fetchBusArrivals).toHaveBeenCalledTimes(2));
    expect(fetchBusArrivals).toHaveBeenLastCalledWith('2500B0615');
  });
});
