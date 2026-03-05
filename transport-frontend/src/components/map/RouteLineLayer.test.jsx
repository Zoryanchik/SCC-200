/**
 * Tests for RouteLineLayer component
 */
import { describe, it, expect, vi } from 'vitest';
import React from 'react';
import { render } from '@testing-library/react';

// Mock react-leaflet components
vi.mock('react-leaflet', () => ({
  Polyline: ({ positions, pathOptions, children }) => (
    <div data-testid="polyline" data-positions={JSON.stringify(positions)} data-color={pathOptions?.color}>
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

  it('renders circle markers for stops', () => {
    const routes = new Map([['100', MOCK_ROUTE]]);
    const { getAllByTestId } = render(<RouteLineLayer activeRoutes={routes} />);

    const markers = getAllByTestId('circle-marker');
    expect(markers).toHaveLength(3); // 3 stops
  });

  it('renders tooltips with stop names', () => {
    const routes = new Map([['100', MOCK_ROUTE]]);
    const { getAllByTestId } = render(<RouteLineLayer activeRoutes={routes} />);

    const tooltips = getAllByTestId('tooltip');
    expect(tooltips).toHaveLength(3);
    expect(tooltips[0].textContent).toBe('Stop A');
    expect(tooltips[1].textContent).toBe('Stop B');
    expect(tooltips[2].textContent).toBe('Stop C');
  });

  it('renders multiple active routes', () => {
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
    expect(polylines).toHaveLength(2);
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
    expect(polylines).toHaveLength(2);
    // First variant gets blue (primary), second gets red (secondary)
    expect(polylines[0].dataset.color).toBe('#1976d2');
    expect(polylines[1].dataset.color).toBe('#d32f2f');
  });

  it('uses OSRM geometry for polyline when available', () => {
    const { getAllByTestId } = render(
      <SingleRouteLine routeData={MOCK_ROUTE_WITH_GEOMETRY} />,
    );
    const polylines = getAllByTestId('polyline');
    expect(polylines).toHaveLength(1);
    const positions = JSON.parse(polylines[0].dataset.positions);
    // Geometry has 6 points (not 3 from stops)
    expect(positions).toHaveLength(6);
    expect(positions[0]).toEqual([54.0, -2.8]);
    expect(positions[5]).toEqual([54.1, -2.7]);
  });

  it('falls back to stop positions when geometry is absent', () => {
    const { getAllByTestId } = render(
      <SingleRouteLine routeData={MOCK_ROUTE} />,
    );
    const polylines = getAllByTestId('polyline');
    expect(polylines).toHaveLength(1);
    const positions = JSON.parse(polylines[0].dataset.positions);
    // No geometry → uses 3 stop coordinates
    expect(positions).toHaveLength(3);
    expect(positions[0]).toEqual([54.0, -2.8]);
  });
});
