import { describe, expect, test } from 'vitest';

// Mirrors the small selection logic in HomePage: prefer legs[].geometry.coords over opt.routeGeometries.
// We keep this test lightweight and framework-agnostic (no React render needed).
const normalizeCoords = (raw) => {
  if (!Array.isArray(raw)) return [];
  const out = [];
  for (const pt of raw) {
    if (!Array.isArray(pt) || pt.length < 2) continue;
    const a = Number(pt[0]);
    const b = Number(pt[1]);
    if (!Number.isFinite(a) || !Number.isFinite(b)) continue;
    if (a < -90 || a > 90) out.push([b, a]);
    else out.push([a, b]);
  }
  return out;
};

const journeyRouteFromOption = (opt) => {
  if (!opt) return null;
  const firstSrc = opt.sources ? Object.values(opt.sources)[0] : null;
  const plan = firstSrc ? (firstSrc.route ? firstSrc.route : firstSrc) : null;
  const legs = Array.isArray(plan?.legs) ? plan.legs : null;

  if (legs && legs.length > 0) {
    const segments = [];
    for (let i = 0; i < legs.length; i++) {
      const leg = legs[i];
      const rawMode = (leg?.mode && String(leg.mode).toLowerCase()) || '';
      const isWalk = rawMode === 'walking' || rawMode === 'walk';
      const mode = rawMode || (leg?.line_name ? 'transit' : 'walking');
      const embeddedCoords = leg?.geometry && Array.isArray(leg.geometry.coords) ? leg.geometry.coords : null;
      if (!embeddedCoords || embeddedCoords.length < 2) continue;
      const norm = normalizeCoords(embeddedCoords);
      if (norm.length < 2) continue;
      segments.push({
        id: isWalk ? `walk-${i}` : `seg-${i}`,
        name: leg?.line_name || mode || `Segment ${i}`,
        coords: norm,
        color: isWalk ? '#000000' : '#1a73e8',
        mode,
      });
    }
    if (segments.length > 0) return segments;
  }

  if (Array.isArray(opt?.routeGeometries)) return opt.routeGeometries;
  if (Array.isArray(opt?.route?.routeGeometries)) return opt.route.routeGeometries;
  return null;
};

describe('HomePage journeyRoute geometry preference', () => {
  test('prefers embedded legs[].geometry over routeGeometries fallback', () => {
    const opt = {
      sources: {
        main: {
          route: {
            legs: [
              {
                mode: 'bus',
                line_name: '42',
                geometry: { coords: [[54.0, -2.8], [54.01, -2.81], [54.02, -2.82]] },
              },
            ],
          },
        },
      },
      // A straight line fallback we'd like to avoid using when embedded coords exist.
      routeGeometries: [{ id: 'seg-0', name: '42', coords: [[54.0, -2.8], [54.02, -2.82]] }],
    };

    const segs = journeyRouteFromOption(opt);
    expect(segs).not.toBeNull();
    expect(segs).toHaveLength(1);
    expect(segs[0].coords).toHaveLength(3);
  });

  test('falls back to routeGeometries when embedded geometry missing', () => {
    const opt = {
      sources: {
        main: {
          route: {
            legs: [{ mode: 'bus', line_name: '42' }],
          },
        },
      },
      routeGeometries: [{ id: 'seg-0', name: '42', coords: [[54.0, -2.8], [54.02, -2.82]] }],
    };

    const segs = journeyRouteFromOption(opt);
    expect(segs).toHaveLength(1);
    expect(segs[0].coords).toHaveLength(2);
  });

  test('falls back to nested opt.route.routeGeometries (walk + bus + walk)', () => {
    const opt = {
      sources: {
        main: {
          route: {
            // No embedded geometry here, so we need fallback.
            legs: [{ mode: 'bus', line_name: '100' }],
          },
        },
      },
      route: {
        routeGeometries: [
          { id: 'walk-0', mode: 'walking', coords: [[54.0, -2.8], [54.001, -2.801]] },
          { id: 'bus-1', mode: 'bus', coords: [[54.001, -2.801], [54.01, -2.81]] },
          { id: 'walk-2', mode: 'walking', coords: [[54.01, -2.81], [54.02, -2.82]] },
        ],
      },
    };

    const segs = journeyRouteFromOption(opt);
    expect(segs).not.toBeNull();
    expect(segs).toHaveLength(3);
    expect(segs[0].id).toBe('walk-0');
    expect(segs[2].id).toBe('walk-2');
  });
});
