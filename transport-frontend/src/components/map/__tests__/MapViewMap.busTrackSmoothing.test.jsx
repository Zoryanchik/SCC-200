import { describe, it } from 'vitest';

// TODO: This needs a proper integration-style test that mounts MapViewMap,
// injects a bus marker with track_coords, triggers the click handler,
// and asserts we call /osrm/route.
//
// The previous attempt didn't execute the click path, so it was flaky/invalid.

describe.skip('MapViewMap live bus track smoothing', () => {
  it('smooths track_coords via OSRM', async () => {});
});
