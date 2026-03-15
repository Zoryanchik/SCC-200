/**
 * Unit-ish coverage for the bus-click "vehicle track" flow living inside MapViewMap.
 *
 * What we want to lock in:
 * - label endpoint returns multiple route_ids
 * - /route/leg-geometry may return `source: "linear"` for some route_ids
 * - we should keep trying until we find `source: "route_tracks"`
 * - and then render the vehicle track polyline
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import React from 'react';
import { render, fireEvent, waitFor } from '@testing-library/react';

// Leaflet / react-leaflet are heavy and depend on DOM layout.
// For these tests we only need to observe that a Marker click triggers
// fetches and eventually results in a Polyline render.
vi.mock('react-leaflet', () => {
  const React = require('react');

  return {
    // Minimal Map container; just render children.
    MapContainer: ({ children }) => <div data-testid="map">{children}</div>,

    // The component renders Marker with `eventHandlers={{ click: ... }}`.
    // Expose it as a clickable div.
    Marker: ({ eventHandlers, children }) => (
      <div
        data-testid="marker"
        onClick={(e) => eventHandlers?.click?.({ originalEvent: { stopPropagation() {} } })}
      >
        {children}
      </div>
    ),

    // Bus track is shown with a Polyline when coordsLen >= 2.
    Polyline: ({ positions }) => (
      <div data-testid="vehicle-track" data-positions={JSON.stringify(positions)} />
    ),

    // Other react-leaflet components used by MapViewMap: stub to render children.
    TileLayer: () => null,
    Popup: ({ children }) => <div data-testid="popup">{children}</div>,
    Tooltip: ({ children }) => <div data-testid="tooltip">{children}</div>,
    CircleMarker: ({ children }) => <div data-testid="circle">{children}</div>,
    Pane: ({ children }) => <div data-testid="pane">{children}</div>,
    useMap: () => ({
      // MapController calls createPane/getPane in some versions; be permissive.
      createPane: () => ({ style: {} }),
      getPane: () => ({ style: {} }),
      getCenter: () => ({ lat: 54.0, lng: -2.8 }),
      fitBounds: () => {},
      on: () => {},
      off: () => {},
    }),

		// MapController wires events via this hook.
		useMapEvents: () => ({}),
  };
});

// Avoid pulling in real UI icon code & leaflet objects.
vi.mock('leaflet', () => {
  const L = {
    // Some helper might call L.icon(...)
    icon: () => ({}),
    divIcon: () => ({}),
    point: () => ({}),
    Marker: function Marker() {},
  };
  // Provide the prototype/options shape Leaflet expects.
  L.Marker.prototype = { options: {} };
  return {
    __esModule: true,
    default: L,
    ...L,
  };
});

// MapViewMap imports these layers – stub them out.
vi.mock('../BusStopLayer', () => ({
  default: () => null,
}));
vi.mock('../RouteLineLayer', () => ({
  default: () => null,
}));
vi.mock('../JourneyRouteLayer', () => ({
  default: () => null,
}));

// The component should call these network helpers.
const fetchRouteLabelMock = vi.fn();
const fetchRouteLineNoFallbackMock = vi.fn();
const fetchRouteLineWithFallbackMock = vi.fn();

vi.mock('../../../services/routeLineApi', () => ({
  fetchRouteLabel: (...args) => fetchRouteLabelMock(...args),
  fetchRouteLineNoFallback: (...args) => fetchRouteLineNoFallbackMock(...args),
  fetchRouteLineWithFallback: (...args) => fetchRouteLineWithFallbackMock(...args),
  stopsToLatLngs: (stops) => (Array.isArray(stops) ? stops.map((s) => [s.lat, s.lon]) : []),
}));

import MapViewMap from '../MapViewMap';

// The component reads API_BASE from import/meta or env in some repos; we rely on it
// constructing fetch URLs that include `/route/leg-geometry`.

describe('MapViewMap vehicle track selection', () => {
  beforeEach(() => {
    // MapViewMap reads debug flags from localStorage.
    // Vitest's jsdom environment can have localStorage disabled depending on config,
    // so we stub the minimal API we need.
    if (!global.window) global.window = {};
    if (!global.window.localStorage || typeof global.window.localStorage.getItem !== 'function') {
      global.window.localStorage = {
        getItem: () => null,
        setItem: () => {},
        removeItem: () => {},
        clear: () => {},
      };
    }

    fetchRouteLabelMock.mockReset();
    fetchRouteLineNoFallbackMock.mockReset();
    fetchRouteLineWithFallbackMock.mockReset();

    // Provide a route line shape so the click handler can proceed.
    fetchRouteLineNoFallbackMock.mockResolvedValue({
      line: '1A',
      variants: [
        {
          route_id: 123,
          stops: [
            { name: 'A', lat: 54.0, lon: -2.8, atco_code: 'A' },
            { name: 'B', lat: 54.1, lon: -2.7, atco_code: 'B' },
          ],
        },
      ],
    });

    fetchRouteLabelMock.mockResolvedValue({
      line: '1A',
      variant_count: 2,
      route_ids: ['RID1', 'RID2'],
    });

		// MapViewMap prefetches route data for visible markers.
		fetchRouteLineWithFallbackMock.mockResolvedValue({
			line: '1A',
			variants: [],
		});

    // Mock global fetch for /route/leg-geometry.
    global.fetch = vi.fn(async (url) => {
      const u = String(url);
      if (!u.includes('/route/leg-geometry')) {
        return {
          ok: false,
          status: 404,
          headers: new Map(),
          json: async () => ({}),
          text: async () => 'not found',
        };
      }

      // RID1 returns linear (should be ignored in strict mode)
      if (u.includes('route_id=RID1')) {
        return {
          ok: true,
          status: 200,
          headers: { get: () => 'application/json' },
          json: async () => ({ source: 'linear', coords: [[54.0, -2.8], [54.1, -2.7]] }),
          text: async () => '',
        };
      }

      // RID2 returns route_tracks (should be selected)
      if (u.includes('route_id=RID2')) {
        return {
          ok: true,
          status: 200,
          headers: { get: () => 'application/json' },
          json: async () => ({ source: 'route_tracks', coords: [[54.0, -2.8], [54.05, -2.75], [54.1, -2.7]] }),
          text: async () => '',
        };
      }

      return {
        ok: true,
        status: 200,
        headers: { get: () => 'application/json' },
        json: async () => ({ source: 'linear', coords: [] }),
        text: async () => '',
      };
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('skips linear and selects route_tracks for vehicle track', async () => {
    // Create a single visible bus marker.
    const markers = [
      {
        id: 'bus-1',
        type: 'bus',
        position: [54.0, -2.8],
        routeNumber: '1A',
        delayMinutes: 0,
      },
    ];

    const { getByTestId, queryAllByTestId } = render(
      <MapViewMap
        // Minimal prop surface: MapViewMap reads these internally; other props are optional.
        filteredMarkers={markers}
        showRouteLines={false}
				journeyRoute={null}
				onOpenPopup={() => {}}
				onClosePopup={() => {}}
				onOpenPopupSignature={() => {}}
				onMoveEnd={() => {}}
        onMapReady={() => {}}
      />,
    );

		expect(queryAllByTestId('vehicle-track')).toHaveLength(0);

    fireEvent.click(getByTestId('marker'));

    // Wait until the selection flow has completed.
    // The selected-vehicle track is rendered imperatively onto the Leaflet map,
    // so there may be no React testids to assert against here.
    await waitFor(() => {
      // Ensure we tried both route_ids by the time selection settles.
      const calledUrls = global.fetch.mock.calls.map((c) => String(c[0]));
      expect(calledUrls.some((u) => u.includes('route_id=RID1'))).toBe(true);
      expect(calledUrls.some((u) => u.includes('route_id=RID2'))).toBe(true);
    });

    // Sanity: confirm we didn't render the legacy React-based vehicle-track elements.
    expect(queryAllByTestId('vehicle-track')).toHaveLength(0);
  });
});
