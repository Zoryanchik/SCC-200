# Transport Backend — Fix Plan

Updated: 2026-03-04

---

## Open Tasks

## High Priority
- [ ] Implement frontend for routing (journey results display)

## Medium Priority
- [ ] Refresh only map for live locations, not the whole page
- [ ] Delay handling for train
- [ ] Widen search bar — allow location text shown in one line
- [ ] Implement `/rail/departures/{station}` endpoint *(not implemented)*
- [ ] Implement timetable / bus filtering
- [ ] Implement `/pricing` endpoint (distance-based stub ok) *(not implemented)*
- [ ] **P6:** Make CORS origins env-configurable (currently hardcoded to localhost:3000/5173) *(not implemented — still hardcoded in api.py)*
- [ ] Implement `/alerts` endpoint *(not implemented)*
- [ ] Implement `GET /bus/times/{stopCode}` endpoint *(not implemented)*
- [ ] Implement `GET /bus/arrivals/{stopCode}` endpoint *(not implemented)*

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
- [ ] Pass viewport bbox from `BusStopLayer` → hook → API to reduce data transfer (currently fetches all, filters client-side)
- [ ] Add click-to-filter: click a bus route badge to highlight all stops on that route
- [ ] Show real-time arrival data in stop popup (requires `GET /bus/arrivals/{stopCode}` backend endpoint)

## Low Priority
- [ ] Harden production config (CORS, auth, rate limits) *(not implemented — no auth, no rate limits, CORS hardcoded)*
- [ ] Add analytics and frequent routes endpoint *(not implemented)*
- [ ] **P7:** Remove or wrap legacy `POST /api/route` — leaks raw internal RAPTOR dict *(still exists unwrapped in api.py)*
- [ ] Add developer docs and Docker compose *(READMEs and Dockerfile exist; docker-compose.yml still missing)*

---

## Known Problems

### P2 — Frontend endpoints missing backend implementation
The frontend calls these endpoints, but `api.py` does not define them. They will 404.

| Frontend function        | Missing endpoint                | Status | Owner |
|--------------------------|----------------------------------|--------|-------|
| `fetchRailDepartures()`  | `GET /rail/departures/{station}` | ❌ 404 | John  |
| `fetchServiceAlerts()`   | `GET /alerts`                    | ❌ 404 | John  |
| `fetchPricing()`         | `GET /pricing?from=&to=`         | ❌ 404 | John  |
| `fetchBusTimes()`        | `GET /bus/times/{stopCode}`      | ❌ 404 | John  |
| `fetchBusArrivals()`     | `GET /bus/arrivals/{stopCode}`   | ❌ 404 | John  |

### P6 — No environment-based CORS configuration
- **File:** `transport-backend/api.py`
- **Problem:** `allow_origins` is hardcoded to `localhost:3000` and `localhost:5173`. Production or other dev ports will be blocked.
- **Fix:** Read origins from an env variable (e.g., `CORS_ORIGINS`).

### P7 — `POST /api/route` (legacy) returns raw RAPTOR dict
- The legacy `/api/route` endpoint returns the raw internal `route_result` dict, which exposes internal stop indices and data structures.
- **Risk:** Not a security issue for local dev, but should not be deployed publicly.
- **Fix:** Either remove the endpoint or wrap it with `build_journey_plan_response()`.

---

## Work Assignments

### John — Backend API Endpoints
Focus: Building out the remaining REST endpoints and data layer.

| # | Task | Priority | Status |
|---|------|----------|--------|
| P2 | `GET /alerts` endpoint | **High** | ❌ open |
| P2 | `GET /bus/times/{stopCode}` endpoint | **High** | ❌ open |
| P2 | `GET /bus/arrivals/{stopCode}` endpoint | **High** | ❌ open |
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
| — | Widen search bar — allow location text on one line | Medium | ❌ open |
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
