/**
 * Tests for RouteLineLayer component
 */
import { describe, it, expect, vi } from 'vitest';
import React from 'react';
import { act, render, waitFor } from '@testing-library/react';

// Mock react-leaflet components
vi.mock('react-leaflet', () => ({
  Polyline: ({ positions, pathOptions, children }) => (
    <div
      data-testid="polyline"
      data-positions={JSON.stringify(positions)}
      data-color={pathOptions?.color}
      data-dash-array={pathOptions?.dashArray}
    >
      {children}
    </div>
  ),
  CircleMarker: ({ center, children }) => (
    <div data-testid="circle-marker" data-center={JSON.stringify(center)}>
      {children}
    </div>
  ),
  Tooltip: ({ children }) => <div data-testid="tooltip">{children}</div>,
}));

import RouteLineLayer, { SingleRouteLine } from './RouteLineLayer';

const MOCK_ROUTE = {
  line: '100',
  variants: [
    {
      route_id: 1,
      stops: [
        { name: 'Stop A', lat: 54.0, lon: -2.8, atco_code: 'A' },
        { name: 'Stop B', lat: 54.05, lon: -2.79, atco_code: 'B' },
        { name: 'Stop C', lat: 54.1, lon: -2.7, atco_code: 'C' },
      ],
    },
  ],
};

const MOCK_ROUTE_WITH_GEOMETRY = {
  line: '100',
  variants: [
    {
      route_id: 1,
      geometry_source: 'route_link_tracks',
      stops: [
        { name: 'Stop A', lat: 54.0, lon: -2.8, atco_code: 'A' },
        { name: 'Stop B', lat: 54.05, lon: -2.79, atco_code: 'B' },
        { name: 'Stop C', lat: 54.1, lon: -2.7, atco_code: 'C' },
      ],
      geometry: [
        [54.0, -2.8],
        [54.01, -2.795],
        [54.02, -2.79],
        [54.05, -2.79],
        [54.07, -2.75],
        [54.1, -2.7],
      ],
    },
  ],
};

describe('RouteLineLayer', () => {
  it('renders nothing when no active routes', () => {
    const { container } = render(<RouteLineLayer activeRoutes={new Map()} />);
    expect(container.querySelector('[data-testid="polyline"]')).toBeNull();
  });

  it('renders a polyline for an active route', () => {
    const routes = new Map([['100', MOCK_ROUTE]]);
    const { getAllByTestId } = render(<RouteLineLayer activeRoutes={routes} />);

    const polylines = getAllByTestId('polyline');
    expect(polylines.length).toBeGreaterThanOrEqual(1);
  });

  it('renders a dashed polyline when route is marked dashed', () => {
    const routes = new Map([
      ['100', { data: MOCK_ROUTE_WITH_GEOMETRY, style: { dashed: true } }],
    ]);
    const { getAllByTestId } = render(<RouteLineLayer activeRoutes={routes} />);
    const polylines = getAllByTestId('polyline');
    // Underlay + top line.
    expect(polylines.length).toBeGreaterThanOrEqual(2);
    for (const p of polylines) {
      expect(p.getAttribute('data-dash-array')).toBe('8 6');
    }
  });

  it('renders circle markers for stops', () => {
    const routes = new Map([['100', MOCK_ROUTE]]);
    const { getAllByTestId } = render(<RouteLineLayer activeRoutes={routes} />);

    const markers = getAllByTestId('circle-marker');
    expect(markers).toHaveLength(3); // 3 stops
  });

  it('renders tooltips with stop names', () => {
    vi.useFakeTimers();
    const routes = new Map([['100', MOCK_ROUTE]]);
    const { getAllByTestId } = render(<RouteLineLayer activeRoutes={routes} />);

    // Tooltips are delayed by 500ms to avoid instant hover popups.
    act(() => {
      vi.advanceTimersByTime(500);
    });
    
    const tooltips = getAllByTestId('tooltip');
    expect(tooltips).toHaveLength(5); // 3 stops + 2 polyline labels (one for underlay, one for top line)
    const texts = tooltips.map(t => t.textContent);
    expect(texts).toContain('Line 100');
    expect(texts).toContain('Stop A');
    expect(texts).toContain('Stop B');
    expect(texts).toContain('Stop C');
    vi.useRealTimers();
  });  it('renders multiple active routes', () => {
    const route2 = {
      line: '1',
      variants: [
        {
          route_id: 10,
          stops: [
            { name: 'X', lat: 53.0, lon: -2.5, atco_code: 'X' },
            { name: 'Y', lat: 53.1, lon: -2.4, atco_code: 'Y' },
          ],
        },
      ],
    };
    const routes = new Map([
      ['100', MOCK_ROUTE],
      ['1', route2],
    ]);
    const { getAllByTestId } = render(<RouteLineLayer activeRoutes={routes} />);

    const polylines = getAllByTestId('polyline');
    // Each variant renders two polylines: a cyan underlay + the colored route.
    // With 2 routes (1 variant each) we therefore expect 4 polylines.
    expect(polylines).toHaveLength(4);
  });
});

describe('SingleRouteLine', () => {
  it('renders nothing for null routeData', () => {
    const { container } = render(<SingleRouteLine routeData={null} />);
    expect(container.innerHTML).toBe('');
  });

  it('renders nothing for empty variants', () => {
    const { container } = render(
      <SingleRouteLine routeData={{ line: '100', variants: [] }} />,
    );
    expect(container.innerHTML).toBe('');
  });

  it('skips variants with less than 2 stops', () => {
    const data = {
      line: '100',
      variants: [
        { route_id: 1, stops: [{ name: 'A', lat: 54.0, lon: -2.8, atco_code: 'A' }] },
      ],
    };
    const { container } = render(<SingleRouteLine routeData={data} />);
    expect(container.querySelector('[data-testid="polyline"]')).toBeNull();
  });

  it('uses dashed line for secondary variants', () => {
    const data = {
      line: '100',
      variants: [
        {
          route_id: 1,
          stops: [
            { name: 'A', lat: 54.0, lon: -2.8, atco_code: 'A' },
            { name: 'B', lat: 54.1, lon: -2.7, atco_code: 'B' },
          ],
        },
        {
          route_id: 2,
          stops: [
            { name: 'C', lat: 54.0, lon: -2.6, atco_code: 'C' },
            { name: 'D', lat: 54.1, lon: -2.5, atco_code: 'D' },
          ],
        },
      ],
    };
    const { getAllByTestId } = render(<SingleRouteLine routeData={data} />);
    const polylines = getAllByTestId('polyline');
    // Two variants, each with underlay + main polyline => 4 total.
    expect(polylines).toHaveLength(4);
    // Main lines are at indices 1 and 3 (0 and 2 are cyan underlays).
    expect(polylines[1].dataset.color).toBe('#1976d2');
    expect(polylines[3].dataset.color).toBe('#d32f2f');
  });

  it('uses OSRM geometry for polyline when available', () => {
    const { getAllByTestId } = render(
      <SingleRouteLine routeData={MOCK_ROUTE_WITH_GEOMETRY} />,
    );
    const polylines = getAllByTestId('polyline');
  // Underlay + main polyline.
  expect(polylines).toHaveLength(2);
  const positions = JSON.parse(polylines[1].dataset.positions);
    // When valid track geometry is provided, render the geometry as-is.
    expect(positions).toHaveLength(MOCK_ROUTE_WITH_GEOMETRY.variants[0].geometry.length);
    // Keep endpoint assertions — the path should still begin/end at the
    // original geometry endpoints.
    expect(positions[0]).toEqual([54.0, -2.8]);
    expect(positions[positions.length - 1]).toEqual([54.1, -2.7]);
  });

  test('when geometry is present and OSRM returns a match, uses snapped positions', async () => {
    const snapped = {
      matchings: [
        {
          geometry: {
            // OSRM geojson coords are [lon, lat]
            coordinates: [
              [-2.8, 54.0],
              [-2.795, 54.02],
              [-2.75, 54.07],
              [-2.7, 54.1],
            ],
          },
        },
      ],
    };

    const fetchSpy = vi.spyOn(global, 'fetch').mockResolvedValue({
      ok: true,
      json: async () => snapped,
    });

    try {
      const { getAllByTestId } = render(
        <SingleRouteLine routeData={MOCK_ROUTE_WITH_GEOMETRY} />,
      );

      await waitFor(() => {
        expect(fetchSpy).toHaveBeenCalled();
      });

      // After OSRM returns, the component should render snapped coords.
      const polylines = getAllByTestId('polyline');
      const positions = JSON.parse(polylines[1].dataset.positions);
      expect(positions[0]).toEqual([54.0, -2.8]);
      expect(positions[positions.length - 1]).toEqual([54.1, -2.7]);
      // And it should match the snapped path exactly (4 points).
      expect(positions).toEqual([
        [54.0, -2.8],
        [54.02, -2.795],
        [54.07, -2.75],
        [54.1, -2.7],
      ]);
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it('falls back to stop positions when geometry is absent', () => {
    const { getAllByTestId } = render(
      <SingleRouteLine routeData={MOCK_ROUTE} />,
    );
    const polylines = getAllByTestId('polyline');
  // Underlay + main polyline.
  expect(polylines).toHaveLength(2);
  const positions = JSON.parse(polylines[1].dataset.positions);
    // No geometry → uses 3 stop coordinates
    expect(positions).toHaveLength(3);
    expect(positions[0]).toEqual([54.0, -2.8]);
  });
  
  test('falls back to stop coords when geometry missing and still tries OSRM snapping', async () => {
    const fetchSpy = vi.spyOn(global, 'fetch').mockResolvedValue({
      ok: false,
      status: 404,
      json: async () => ({}),
    });

    try {
      const data = {
        line: 'X1',
        variants: [
          {
            route_id: 'RID-X1',
            // No geometry available
            geometry: [],
            // And no track source, so OSRM must never be called.
            geometry_source: null,
            stops: [
              { name: 'A', lat: 54.0, lon: -2.8, atco_code: 'A' },
              { name: 'B', lat: 54.01, lon: -2.81, atco_code: 'B' },
              { name: 'C', lat: 54.02, lon: -2.82, atco_code: 'C' },
            ],
          },
        ],
      };

      render(<SingleRouteLine routeData={data} />);

       // With no track geometry, we should *not* call OSRM at all.
      await waitFor(() => {
        expect(fetchSpy).not.toHaveBeenCalled();
      });
    } finally {
      fetchSpy.mockRestore();
    }
  });

  test('does not call OSRM when geometry exists but is not marked as track source', async () => {
    const fetchSpy = vi.spyOn(global, 'fetch').mockResolvedValue({
      ok: true,
      json: async () => ({ routes: [] }),
    });

    try {
      const data = {
        line: 'X2',
        variants: [
          {
            route_id: 'RID-X2',
            // Geometry exists but is not track provenance.
            geometry_source: 'stops',
            geometry: [
              [54.0, -2.8],
              [54.01, -2.81],
              [54.02, -2.82],
            ],
            stops: [
              { name: 'A', lat: 54.0, lon: -2.8, atco_code: 'A' },
              { name: 'B', lat: 54.01, lon: -2.81, atco_code: 'B' },
              { name: 'C', lat: 54.02, lon: -2.82, atco_code: 'C' },
            ],
          },
        ],
      };

      render(<SingleRouteLine routeData={data} />);

      await waitFor(() => {
        expect(fetchSpy).not.toHaveBeenCalled();
      });
    } finally {
      fetchSpy.mockRestore();
    }
  });
});
