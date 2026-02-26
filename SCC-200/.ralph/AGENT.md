# Agent Build Instructions

## Project Setup
```bash
# Install dependencies (example for Node.js project)
npm install

# Or for Python project
pip install -r requirements.txt

# Or for Rust project  
cargo build
```

## Running Tests
```bash
# Node.js
npm test

# Python
pytest

# Rust
cargo test
```

## Build Commands
```bash
# Production build
npm run build
# or
cargo build --release
```

## Development Server
```bash
# Start development server
npm run dev
# or
cargo run
```

## Key Learnings
- Update this section when you learn new build optimizations
- Document any gotchas or special setup requirements
- Keep track of the fastest test/build cycle
- Frontend vitest config does not include the React plugin; components must import React explicitly
- useLiveBusLocations hook debounces lat/lon changes (800ms) to prevent API spam during map panning
- MapViewMap fires onMoveEnd with {lat, lon} on pan/zoom; parent passes center to useLiveBusLocations
- Backend GET /bus/live/{operator}?lat=&lon= requires lat/lon; returns [{line, destination, lat, lon}]
- POST /api/bus_live was removed (P3); all consumers now use GET /bus/live/{operator}
- POST /journey/plan returns {success, legs[], meta, routeGeometries[]} — legs have from_stop/to_stop with name/lat/lon
- routeGeometries coords use [lat, lon] order (NOT GeoJSON [lon, lat])
- /search/stops returns mixed type:"stop" and type:"location" results; locations have atco_code: null
- Frontend API_BASE_URL now reads from VITE_API_BASE_URL env variable; defaults to http://localhost:5050 for local dev
- .env sets local default, .env.production sets https://transport.scc.lancs.ac.uk for builds, .env.local is gitignored for overrides
- 5 endpoints called by frontend are not yet implemented: /rail/departures, /weather, /alerts, /pricing, /bus/times
- Integration schemas documented in transport-backend/INTEGRATION_SCHEMAS.md
- WS /ws/live provides STOMP 1.2 over WebSocket; ws_server.py broker polls BusLive every ~15s and pushes to /topic/BUS_MVT_ALL subscribers
- Frontend liveUpdatesManager must set brokerURL to ws://localhost:5050/ws/live for local dev (default points at external STOMP server)
- Transit leg routeGeometries now include ALL intermediate stop coords (not just 2-point boarding→alighting). _get_transit_coords() extracts full polyline from day.journey_times via journey_stop_index. Falls back to 2-point when day/journey is None or stops not found.
- Each transit leg has an intermediate_stops field: list of {name, lat, lon} for stops between boarding and alighting (exclusive of endpoints). Walking legs omit this field.
- station_classifier.py computes stop classifications (hub/interchange/local/request_stop) from MergedData metrics: degree (routes), frequency (departures), interchange (distinct lines). Default thresholds tuned for NW England networks.
- GET /stops/classify returns full classification data with metrics. GET /search/stops?classification=hub filters search results by class and excludes geocode locations. Classification is cached per-process via _classification_cache (ATCO→class dict).
- Walking class (walking.py) uses OSRM_URL env var (default http://localhost:5001) configurable per container. osrm_available property probes once and caches. When OSRM is down, reachable_stops() falls back to precomputed inter-walk table (originally computed via OSRM during data loading) plus haversine estimates. _haversine_m() is a proper great-circle distance function.
- main.py now passes OSRM_URL through walking_raw dict to build_for_date() so Walking() uses the configured URL, not the default. precompute_walking_transfers() also receives osrm_base explicitly.
- GET /walking/status returns {osrm_url, osrm_available, max_walk_seconds, precomputed_stops, stops_with_coords}. GET /walking/reachable?lat=&lon= returns nearby walkable stops sorted by walk_seconds, with ATCO codes and coords resolved from merged-data.

## Feature Development Quality Standards

**CRITICAL**: All new features MUST meet the following mandatory requirements before being considered complete.

### Testing Requirements

- **Minimum Coverage**: 85% code coverage ratio required for all new code
- **Test Pass Rate**: 100% - all tests must pass, no exceptions
- **Test Types Required**:
  - Unit tests for all business logic and services
  - Integration tests for API endpoints or main functionality
  - End-to-end tests for critical user workflows
- **Coverage Validation**: Run coverage reports before marking features complete:
  ```bash
  # Examples by language/framework
  npm run test:coverage
  pytest --cov=src tests/ --cov-report=term-missing
  cargo tarpaulin --out Html
  ```
- **Test Quality**: Tests must validate behavior, not just achieve coverage metrics
- **Test Documentation**: Complex test scenarios must include comments explaining the test strategy

### Git Workflow Requirements

Before moving to the next feature, ALL changes must be:

1. **Committed with Clear Messages**:
   ```bash
   git add .
   git commit -m "feat(module): descriptive message following conventional commits"
   ```
   - Use conventional commit format: `feat:`, `fix:`, `docs:`, `test:`, `refactor:`, etc.
   - Include scope when applicable: `feat(api):`, `fix(ui):`, `test(auth):`
   - Write descriptive messages that explain WHAT changed and WHY

2. **Pushed to Remote Repository**:
   ```bash
   git push origin <branch-name>
   ```
   - Never leave completed features uncommitted
   - Push regularly to maintain backup and enable collaboration
   - Ensure CI/CD pipelines pass before considering feature complete

3. **Branch Hygiene**:
   - Work on feature branches, never directly on `main`
   - Branch naming convention: `feature/<feature-name>`, `fix/<issue-name>`, `docs/<doc-update>`
   - Create pull requests for all significant changes

4. **Ralph Integration**:
   - Update .ralph/fix_plan.md with new tasks before starting work
   - Mark items complete in .ralph/fix_plan.md upon completion
   - Update .ralph/PROMPT.md if development patterns change
   - Test features work within Ralph's autonomous loop

### Documentation Requirements

**ALL implementation documentation MUST remain synchronized with the codebase**:

1. **Code Documentation**:
   - Language-appropriate documentation (JSDoc, docstrings, etc.)
   - Update inline comments when implementation changes
   - Remove outdated comments immediately

2. **Implementation Documentation**:
   - Update relevant sections in this AGENT.md file
   - Keep build and test commands current
   - Update configuration examples when defaults change
   - Document breaking changes prominently

3. **README Updates**:
   - Keep feature lists current
   - Update setup instructions when dependencies change
   - Maintain accurate command examples
   - Update version compatibility information

4. **AGENT.md Maintenance**:
   - Add new build patterns to relevant sections
   - Update "Key Learnings" with new insights
   - Keep command examples accurate and tested
   - Document new testing patterns or quality gates

### Feature Completion Checklist

Before marking ANY feature as complete, verify:

- [ ] All tests pass with appropriate framework command
- [ ] Code coverage meets 85% minimum threshold
- [ ] Coverage report reviewed for meaningful test quality
- [ ] Code formatted according to project standards
- [ ] Type checking passes (if applicable)
- [ ] All changes committed with conventional commit messages
- [ ] All commits pushed to remote repository
- [ ] .ralph/fix_plan.md task marked as complete
- [ ] Implementation documentation updated
- [ ] Inline code comments updated or added
- [ ] .ralph/AGENT.md updated (if new patterns introduced)
- [ ] Breaking changes documented
- [ ] Features tested within Ralph loop (if applicable)
- [ ] CI/CD pipeline passes

### Rationale

These standards ensure:
- **Quality**: High test coverage and pass rates prevent regressions
- **Traceability**: Git commits and .ralph/fix_plan.md provide clear history of changes
- **Maintainability**: Current documentation reduces onboarding time and prevents knowledge loss
- **Collaboration**: Pushed changes enable team visibility and code review
- **Reliability**: Consistent quality gates maintain production stability
- **Automation**: Ralph integration ensures continuous development practices

**Enforcement**: AI agents should automatically apply these standards to all feature development tasks without requiring explicit instruction for each task.
