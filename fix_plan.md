# Transport Backend — Fix Plan

Updated: 2026-03-05

---

## Open Tasks

## High Priority
- [x] Implement frontend for routing (journey results display)
- [x] Auto input user location as start point
- [x] Allow arbitrary start date-time input in fronttend
- [x] Number of transfer limit button in frontend
- [x] Fix bus routes not working: 1, 1A, 42, 942, 11, 2X (and others)
- [x] Add clear button / clear feature (e.g. click map) to remove toggled bus routes
- [x] Add search to map page to avoid scrolling
- [x] After searching, display the route on the map
- [x] Show multiple route options from a search so the user can choose and select one
- [ ] When sharing location, show closest bus stops and nearest bus (check map page)
- [ ] Map page: remove weather, favourite routes, and "schedule for nearest bus" sections
- [x] Pricing: figure out pricing model (prices vary by time, stop, etc.)
- [x] Estimated cost for route
- [ ] Check milestone deliverables
- [ ] Write milestone report
- [x] `/rail/departures/{station}` Implementation

## Medium Priority
- [x] Duplicate line shown in label when cliking on a bus, replace "line XX ->" with "To"
- [x] Refresh only map for live locations, not the whole page
- [x] Delay handling for bus
- [ ] Delay handling for train
- [x] Make weather widget nicer
- [x] Add number icon for tracked buses on the map page and make icons smaller
- [x] Widen search bar — allow location text shown in one line
- [x] Implement `/rail/departures/{station}` endpoint
- [ ] Implement timetable filtering by time and service
- [ ] Implement station selection via map interaction (tap stop → select as origin/destination)
- [ ] Implement display of estimated arrival times at stations
- [x] Implement pricing display for routes and ticket types (frontend)
- [x] Implement `/pricing` endpoint (distance-based stub)
- [ ] Implement frequently / recently used routes feature
- [ ] UI refinement for desktop and mobile layouts
- [x] Implement accessibility features (contrast, scaling, map clarity) *(high-contrast toggle + font-size cycling in navbar, persisted to localStorage, ARIA attributes on nav buttons)*
- [x] **P6:** Make CORS origins env-configurable (currently hardcoded to localhost:3000/5075)
- [x] Implement `/alerts` endpoint *(stub — returns empty list)*
- [ ] Implement `GET /bus/times/{stopCode}` endpoint *(not implemented)*
- [x] Implement `GET /bus/arrivals/{stopCode}` endpoint *(implemented)*

## Bus Stops on Map
- [x] Create `busStopsApi.js` service with mock fallback (12 Lancaster-area stops)
- [x] Create `useBusStops` React hook (loading/error/refetch state)
- [x] Create `BusStopLayer` component (colour-coded markers with popup info)
- [x] Integrate `BusStopLayer` into `MapViewMap.jsx` (additive only)
- [x] Write tests for all new modules (44 tests passing)
- [x] Fix mock fallback — API returns geocoded locations not bus stops
- [x] Add backend `GET /stops/geo` endpoint — merges classification data with NaPTAN lat/lon coords
- [x] Update frontend `busStopsApi.js` to consume `/stops/geo` for real classified stops
- [x] Add `?bbox=south,west,north,east` viewport filter to `/stops/geo`
- [x] Strip internal line IDs → human-readable route names (1, 1A, 100, etc.)
- [x] Replace CircleMarker with SVG bus-stop sign icon (circle on post, scales with zoom)
- [x] Raise minZoom to 14 — stops only appear at street-level to avoid map clutter
- [x] Pass viewport bbox from `BusStopLayer` → hook → API to reduce data transfer (currently fetches all, filters client-side)
- [x] Show real-time arrival data in stop popup (requires `GET /bus/arrivals/{stopCode}` backend endpoint)

## Bus Route Lines on Map
- [x] Add backend `GET /routes/line/{line}` endpoint — returns ordered stop sequences with coords
- [x] Use journey-level stop sequences (not merged route_stops) to avoid interleaved inbound/outbound
- [x] Mean-gap heuristic to pick tightest single-direction journey per route
- [x] Post-filter: drop variants with max gap >2.5 km or mean gap >1.8× best
- [x] Create `routeLineApi.js` service — fetches route data, converts stops to LatLng arrays
- [x] Create `useRouteLine` hook — toggle state with ref+tick pattern (no infinite re-renders)
- [x] Create `RouteLineLayer` component — coloured polylines + circle markers at stops
- [x] Wire popup route buttons to toggle route lines via capture-phase pointerdown listener
- [x] Fix duplicate React key errors (useBusStops dedup, unique keys in BusStopLayer/RouteLineLayer)
- [x] Add mock route data fallback in `routeLineApi.js` — when backend is unreachable, return hardcoded Lancaster-area route so the feature is testable without a live server
- [x] Highlight selected bus stop marker when popup is open (enlarge/glow/color change)
- [ ] Road-following route lines via OSRM/transport API (`bus/times/{stopCode}`) — replace straight stop-to-stop with road geometry
- [x] Verify button highlight color toggle works (dark blue + ✓ on click)
- [ ] Update/write frontend tests for BusStopLayer, RouteLineLayer, useRouteLine, routeLineApi
- [ ] Update/write backend tests for `/routes/line/{line}` endpoint

## Low Priority
- [ ] Harden production config (CORS, auth, rate limits) *(not implemented — no auth, no rate limits, CORS hardcoded)*
- [ ] Add analytics and frequent routes endpoint *(not implemented)*
- [x] **P7:** Remove or wrap legacy `POST /api/route` — now delegates to `build_journey_plan_response`
- [ ] Add developer docs and Docker compose *(READMEs and Dockerfile exist; docker-compose.yml still missing)*

---

## Known Problems

### P2 — Frontend endpoints missing backend implementation
The frontend calls these endpoints, but `api.py` does not define them. They will 404.

| Frontend function        | Missing endpoint                | Status | Owner |
|--------------------------|----------------------------------|--------|-------|
| `fetchRailDepartures()`  | `GET /rail/departures/{station}` | ✅ done | John  |
| `fetchServiceAlerts()`   | `GET /alerts`                    | ✅ stub | John  |
| `fetchPricing()`         | `GET /pricing?from=&to=`         | ✅ stub | John  |
| `fetchBusTimes()`        | `GET /bus/times/{stopCode}`      | ❌ 404 | John  |
| `fetchBusArrivals()`     | `GET /bus/arrivals/{stopCode}`   | ✅ done | John  |

### P6 — No environment-based CORS configuration
- **File:** `transport-backend/api.py`
- **Problem:** `allow_origins` is hardcoded to `localhost:3000` and `localhost:5075`. Production or other dev ports will be blocked.
- **Fix:** Read origins from an env variable (e.g., `CORS_ORIGINS`). ✅ Done — falls back to localhost defaults.

### P7 — `POST /api/route` (legacy) returns raw RAPTOR dict
- The legacy `/api/route` endpoint returns the raw internal `route_result` dict, which exposes internal stop indices and data structures.
- **Risk:** Not a security issue for local dev, but should not be deployed publicly.
- **Fix:** Either remove the endpoint or wrap it with `build_journey_plan_response()`. ✅ Done — now wraps correctly.

---

## Work Assignments

### John — Backend API Endpoints
Focus: Building out the remaining REST endpoints and data layer.

| # | Task | Priority | Status |
|---|------|----------|--------|
| P2 | `GET /alerts` endpoint | **High** | ❌ open |
| P2 | `GET /bus/times/{stopCode}` endpoint | **High** | ❌ open |
| P2 | `GET /bus/arrivals/{stopCode}` endpoint | **High** | ✅ done |
| — | Delay handling for train (timetable comparison) | **Medium** | ❌ open |
| 5 | `GET /rail/departures/{station}` endpoint | Medium | ❌ open |
| 6 | `GET /pricing?from=&to=` endpoint (distance-based stub) | Medium | ❌ open |
| — | Implement timetable / bus filtering | Medium | ❌ open |
| P7 | Remove or wrap legacy `POST /api/route` — leaks raw RAPTOR dict | Low | ❌ open |

### Anton — Real-time Systems, Routing & Geometry
Focus: Live data, routing infrastructure, bus tracking, and station features.

| # | Task | Priority | Status |
|---|------|----------|--------|
| — | Refresh only map for live locations (not full page) | **Medium** | ❌ open |
| — | Bus timetable filtering on map | Medium | ❌ open |
| 18 | Add analytics and frequent routes endpoint | Low | ❌ open |

### Jamie — Frontend Integration & DevOps
Focus: Connecting the frontend to the backend, UI, docs, and hardening.

| # | Task | Priority | Status |
|---|------|----------|--------|
| — | Implement frontend routing UI (journey results display) | **High** | ❌ open |
| — | Widen search bar — allow location text on one line | Medium | ✅ done |
| P6 | Make CORS origins env-configurable (`CORS_ORIGINS` env var) | Medium | ❌ open |
| 14 | Add developer docs and Docker compose | Medium | ❌ open (~done: READMEs exist; docker-compose missing) |
| 16 | Harden production config (CORS, auth, rate limits) | Low | ❌ open |


---

## 5. Endpoint: /rail/departures/{station}
- Summary: Return upcoming departures for a station CRS code.
- Files: `api.py`, `timetable.py`, `merged_data.py`.
- Steps:
  1. Map station CRS code to merged stop index.
  2. Search `MergedData.route_stop_departures` to compute upcoming departures.
  3. Return records like `{serviceId, destination, scheduledTime, departureTime, status, lat, lon}`.
- Acceptance: `useLiveDepartures` hook works with this response.

## 6. Endpoint: /pricing
- Summary: Fare calculation endpoint.
- Files: `api.py`
- Steps:
  1. Implement `GET /pricing?from=<>&to=<>`.
  2. Implement distance-based stub: compute Haversine distance and apply simple fare bands.
  3. Return `{price, currency, fares:[{type, price}]}`.
- Acceptance: `usePricing` receives a consistent structure.

## 7. Endpoint: /alerts
- Summary: Provide service alert endpoint.
- Files: `api.py`
- Steps:
  1. Implement `/alerts` returning `[{id, title, severity, description, affectedLines, start, end}]`.
- Acceptance: Frontend `fetchServiceAlerts()` hook accepts and displays data.

## 14. Documentation & Docker compose
- Summary: Developer docs and a `docker-compose.yml`.
- Files: `README.md`, `docker-compose.yml`, `.env.example`.
- Steps:
  1. Provide a `docker-compose.yml` describing `backend`, `osrm`, `frontend`.
- Acceptance: `docker-compose up` brings up dev stack.

## 16. Production hardening & security
- Summary: Prepare API for production use.
- Files: config in `api.py`.
- Steps:
  1. Add env-based configuration for TLS, CORS, and API keys.
  2. Add rate limiting middleware.
- Acceptance: API rejects invalid input and respects CORS.

## 18. Analytics and frequent routes endpoint
- Summary: Track frequent route planning requests.
- Files: `analytics.py`, `api.py`.
- Steps:
  1. Log successful `/journey/plan` requests.
  2. Aggregate recent requests and return top N via `/analytics/frequent-routes`.
- Acceptance: Frontend can call endpoint to pre-fill routes.
