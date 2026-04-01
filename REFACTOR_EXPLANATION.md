# Pytest Final Resolution

This document explains the set of fixes applied to resolve the heavy test suite regressions and hangs.

## The Issues
Over the course of the backend refactoring, several functions and variables were accidentally pruned. 

### 1. pi.py missing features
* **Geometry Processing**: The function uild_journey_plan_response lost the include_geometry support and the entire algorithm for matching legs to router sequences.
* **Stop Coordinations**: _get_stop_coord was dropped, causing exceptions when routing fell back to stops.
* **Legacy Mappings**: Polyline segment processing logic such as _subsegment_from_coords and _haversine algorithms, plus unstable mappings such as _live_vehicle_should_suppress_match went missing.
* **Missing APIs**: Missing endpoints like /debug/route-tracks and /routes/line_at_stop were dropped. 

### 2. Missing test properties in pytest mocks
* Several mock classes in tests failed to simulate an empty oute_tracks list structure, throwing errors when verifying fallback algorithms.
* operator_ref overrides in tests crashed out _compute_delay_from_timetable due to unmatched kwarg signatures.
* Missing mocked initializations in the pi (from heavy caching and database instantiation loops) caused tests to hang indefinitely when calling PostgreSQL servers inside nested loops.

## The Solutions Applied
1. Stitched and extracted safely back into pi.py the _get_stop_coord method and the uild_journey_plan_response code blocks from pi_40c9ba0.py that pertained to outeGeometries.
2. Extracted legacy helpers (_subsegment_from_coords, oute_leg_geometry, _haversine, _live_vehicle_should_suppress_match) from the old commit state and cleanly injected them into pi.py.
3. Adapted and aligned Pytest unit test patches from .venv\Lib\site-packages such as mocking heavy cache engines inside unit testing suites. 
4. Forced dummy cache representations of PostgreSQL variables (oute_tracks) in local test DummyMerged objects. 
