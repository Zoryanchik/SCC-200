/**
 * Tests for BusStopLayer and BusStopMarker components
 *
 * Validates:
 *  - BusStopMarker renders a Marker with correct position & SVG icon
 *  - BusStopMarker popup shows stop name, classification, lines
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
import { render, screen } from '@testing-library/react';

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
const mockMapInstance = {
  getZoom: () => mockZoom,
  getBounds: () => mockBounds,
  on: vi.fn(),
  off: vi.fn(),
};

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
  Popup: ({ children }) => <div data-testid="popup">{children}</div>,
  useMap: () => mockMapInstance,
}));

// ── Mock useBusStops hook ───────────────────────────────────────────
const mockStops = [
  {
    id: 'stop-1',
    name: 'Lancaster Bus Station',
    lat: 54.04895,
    lon: -2.80117,
    atco_code: '2500LAA12000',
    classification: 'hub',
    lines: [
      { id: 'PC0002407:417:1', name: '1' },
      { id: 'PC0002407:13:2', name: '2' },
      { id: 'PC0002407:97:40', name: '40' },
    ],
  },
  {
    id: 'stop-2',
    name: 'Uni Underpass',
    lat: 54.0101,
    lon: -2.7852,
    atco_code: '2500B0615',
    classification: 'interchange',
    lines: [
      { id: 'PC0002407:417:1', name: '1' },
      { id: 'PC0002407:416:4', name: '4' },
    ],
  },
  {
    id: 'stop-3',
    name: 'Out of Bounds Stop',
    lat: 51.0, // Way south — outside mock bounds
    lon: -2.5,
    atco_code: '9900OOB001',
    classification: 'local',
    lines: [{ id: 'PC9999:1:99', name: '99' }],
  },
];

let hookArgs = {};
vi.mock('../../../hooks/useBusStops', () => ({
  useBusStops: (opts) => {
    hookArgs = opts;
    return { stops: mockStops, loading: false, error: null, refetch: vi.fn() };
  },
}));

import BusStopLayer, { BusStopMarker } from '../BusStopLayer';

beforeEach(() => {
  mockZoom = 14;
  hookArgs = {};
  vi.clearAllMocks();
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
      // lines are {id, name} objects — display name should appear in text
      const displayName = typeof line === 'object' ? line.name : line;
      expect(linesContainer.textContent).toContain(displayName);
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
});
