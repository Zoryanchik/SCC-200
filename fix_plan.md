---

### File 2: `transport-backend/TRANSPORT_BACKEND_MY_TASKS.md` (Task List)

```markdown
# Transport Backend â€” My Tasks

Generated: 2026-02-12

This document contains the full, actionable task list (19 items) with implementation details, acceptance criteria, and suggested next steps so we can implement the backend and integrate it with the frontend.

---

## Taskbar (status + priority)

## High Priority
- [x] Scaffold FastAPI server and `/health` (with tests).
- [x] Implement `/search/stops` endpoint (with tests).
- [x] Implement `/bus/live/{operator}` endpoint (with tests).
- [x] Connect frontend bus live with backend using map center `latitude` and `longitude`.
- [x] Expand search to support any location (prompted), not just stations.
- [x] Align search UI and map on the same page; prioritize map locations.
 - [x] Review mock FastAPI frontend (`api.py`, http://localhost:5050) for integration cues.
- [x] Implement `/journey/plan` endpoint with `routeGeometries`.
- [x] **P1:** Fix `API_BASE_URL` — hardcoded to external host, frontend never hits local backend.
- [ ] **P2:** Implement 6 missing backend endpoints called by frontend (rail, weather, alerts, pricing, bus times, bus arrivals).

## Medium Priority
- [ ] Implement `/rail/departures/{station}` endpoint.
- [ ] Implement `/pricing` endpoint (distance-based stub ok).
- [ ] Implement `/weather` and `/alerts` endpoints.
- [ ] Add WebSocket or STOMP live updates adapter. **[Anton]**
- [ ] Ensure multi-leg route geometry export for map polylines. **[Anton]**
- [ ] Add station classification (P27) and filtering (P28). **[Anton]**
- [ ] Add OSRM integration with walking fallback. **[Anton]**
- [ ] Update frontend services and hooks to match API responses. **[Jamie]**
- [ ] Add developer docs and Docker compose. **[Jamie]**
- [x] **P3:** Deprecate/remove duplicate `POST /api/bus_live` endpoint (different shape from `GET /bus/live/{operator}`).
- [ ] **P5:** Fix `/journey/plan` error response — returns `null` arrays instead of `[]`/`{}`, will crash frontend `.map()`.
- [ ] **P6:** Make CORS origins env-configurable (currently hardcoded to localhost:3000/5173).

## Low Priority
- [ ] Ingest and parse train data to populate `TrainData`.
- [ ] Harden production config (CORS, auth, rate limits). **[Jamie]**
- [ ] Add performance caching for routing. **[John]**
- [ ] Add analytics and frequent routes endpoint. **[Jamie]**
- [x] **P4:** Document `[lat, lon]` vs GeoJSON `[lon, lat]` coord order in `routeGeometries` (add code comments).
- [ ] **P7:** Remove or wrap legacy `POST /api/route` — leaks raw internal RAPTOR dict.

## Completed
- [x] Scaffold FastAPI server and `/health` (with tests).
- [x] Implement `/journey/plan` endpoint with `routeGeometries`.
- [x] Expand search to support any location (prompted), not just stations.
 - [x] Review mock FastAPI frontend (`api.py`, http://localhost:5050) for integration cues.

---

## Known Problems (from integration review 2026-02-19)

### P1 — API_BASE_URL hardcoded to external host (BLOCKING)
- **File:** `transport-frontend/src/services/transportApi.js`
- **Problem:** `API_BASE_URL` is `https://transport.scc.lancs.ac.uk`. The React frontend never hits `localhost:8000` during local dev. All calls go to the university server.
- **Fix:** Switch to an env variable (`VITE_API_BASE_URL`) with a `.env` default of `http://localhost:5050`, or configure a Vite proxy.

### P2 — 6 frontend endpoints have no backend implementation
The frontend calls these endpoints, but `api.py` does not define them. They will 404.

| Frontend function        | Missing endpoint               | Owner |
|--------------------------|---------------------------------|-------|
| `fetchRailDepartures()`  | `GET /rail/departures/{station}`| John  |
| `fetchWeatherData()`     | `GET /weather?lat=&lon=`        | John  |
| `fetchServiceAlerts()`   | `GET /alerts`                   | John  |
| `fetchPricing()`         | `GET /pricing?from=&to=`        | John  |
| `fetchBusTimes()`        | `GET /bus/times/{stopCode}`     | —     |
| `fetchBusArrivals()`     | `GET /bus/arrivals/{stopCode}`  | —     |

### P3 — Duplicate bus live endpoints (confusion risk)
- `POST /api/bus_live` — used only by the mock HTML frontend (`index.html`).
- `GET /bus/live/{operator}` — used by the React frontend.
- **Risk:** New developers may call the wrong one. The `POST` endpoint returns a different response shape (`line_ref`/`latitude`/`longitude` vs `line`/`lat`/`lon`).
- **Fix:** Deprecate or remove `POST /api/bus_live` once the mock frontend is no longer needed, or add a clear docstring warning.

### P4 — `routeGeometries` uses `[lat, lon]`, not GeoJSON `[lon, lat]`
- **File:** `transport-backend/api.py` → `build_journey_plan_response()`
- **Problem:** Leaflet and most map libraries expect `[lat, lon]` for `L.polyline`, so this actually works. But GeoJSON spec uses `[lon, lat]`. If the frontend ever switches to GeoJSON-based rendering this will break silently.
- **Fix:** Document clearly in `INTEGRATION_SCHEMAS.md` (done). Add a comment in `api.py`.

### P5 — `/journey/plan` error response has `null` arrays
- When the router fails, `legs`, `meta`, and `routeGeometries` are all `null` (not empty arrays/objects).
- **Risk:** Frontend code doing `result.legs.map(...)` will throw `TypeError: Cannot read properties of null`.
- **Fix:** Either return `[]`/`{}` from the backend, or add null guards in the frontend hooks (`useJourneyPlans`).

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

### John — Backend API Endpoints (8 tasks)
Focus: Building out the remaining REST endpoints and data layer.

| # | Task | Priority |
|---|------|----------|
| P2 | Implement 6 missing backend endpoints (rail, weather, alerts, pricing, bus times, bus arrivals) | **High** |
| P5 | Fix `/journey/plan` error response — return `[]`/`{}` instead of `null` | **Medium** |
| P7 | Remove or wrap legacy `POST /api/route` — leaks raw RAPTOR dict | Low |
| 5 | Implement /rail/departures/{station} endpoint | Medium |
| 6 | Implement /pricing endpoint (distance-based stub) | Medium |
| 7 | Implement /weather and /alerts endpoints | Medium |
| 12 | Ingest and parse train data to populate TrainData | Low |
| 17 | Add performance caching for routing | Low |

### Anton — Real-time Systems, Routing & Geometry (8 tasks)
Focus: Live data, routing infrastructure, and station features.

| # | Task | Priority |
|---|------|----------|
| P3 | ~~Deprecate/remove duplicate `POST /api/bus_live` endpoint (different shape from GET)~~ ✅ | **Medium** |
| P4 | ~~Document `[lat, lon]` vs GeoJSON `[lon, lat]` coord order in code comments~~ ✅ | Low |
| — | ~~Review mock FastAPI frontend for integration cues~~ (done) | ~~High~~ |
| 8 | Add WebSocket/STOMP live updates adapter | Medium |
| 9 | Ensure multi-leg route geometry export for map polylines | Medium |
| 10+11 | Add station classification (P27) and filtering (P28) | Medium |
| 13 | Add OSRM integration with walking fallback | Medium |

### Jamie — Frontend Integration & DevOps (7 tasks)
Focus: Connecting the frontend to the new backend, docs, and hardening.

| # | Task | Priority |
|---|------|----------|
| P1 | Fix `API_BASE_URL` — switch to `VITE_API_BASE_URL` env variable (BLOCKING) | **High** |
| P6 | Make CORS origins env-configurable (currently hardcoded) | **Medium** |
| 15 | Update frontend services and hooks to match API responses | Medium |
| 14 | Add developer docs and Docker compose | Medium |
| 16 | Harden production config (CORS, auth, rate limits) | Low |
| 18 | Add analytics and frequent routes endpoint | Low |

---

## Recent requests (2026-02-16)
- Tried to connect backend bus live with frontend but it did not work well. The function takes `latitude` and `longitude` as parameters (current map center). Request: modify frontend to adapt. Note: not familiar with frontend.
- Search bar should support any location search (ideally with prompt), not just stations. Suggests putting the map on the same page as search bar and prioritizing locations on the map.
- Created a simple mock frontend using FastAPI to connect with backend; pushed it. Run `api.py` and go to http://localhost:5050. Note: check overall project in case something changed.

---

## 1. API: Scaffold FastAPI server
- Summary: Add a lightweight HTTP server that exposes the backend functionality to the frontend.
- Files to create/edit: `transport-backend/api.py`, small runner `transport-backend/__main__.py`, `tests/test_api.py`.
- Steps:
  1. Add `FastAPI` and `pytest` dependencies.
  2. Create `api.py` with `app = FastAPI()` and a `/health` endpoint returning `{status: "ok"}`.
  3. Write a test in `test_api.py` using `TestClient` to assert `/health` returns 200.
  4. On startup, call `initialize_base()` from `main.py` to create `BusLoader`.
- Acceptance: `pytest` passes with 100% coverage for new code. `uvicorn transport-backend.api:app --reload` starts successfully.

## 2. Endpoint: /journey/plan
- Summary: POST endpoint to plan journeys; uses RAPTOR router and returns human-friendly JSON plus geometry.
- Files to edit: `api.py`, `raptor_router.py`, `merged_data.py`, `tests/test_journey.py`.
- Payload: `{fromStop, toStop, departureTime (ISO), date, maxTransfers, mode}`
- Steps:
  1. Receive POST request and validate payload.
  2. Write a mock test verifying payload validation and expected JSON return shape.
  3. Call `build_for_date()` and `router.route(...)`.
  4. Convert returned merged stop ids to coordinates using `MergedData`; construct `routeGeometries`.
  5. Return JSON: `{legs: [...], _meta: {...}, routeGeometries: [...]}`.
- Acceptance: Tests pass. Frontend can consume `routeGeometries[*].coords` to draw polylines.

## 3. Endpoint: /search/stops
- Summary: GET search endpoint for stops (NaPTAN) used by frontend autocomplete.
- Files: `api.py`, `tests/test_search.py`.
- Query: `GET /search/stops?q=cent&limit=10`
- Steps:
  1. Write a test mocking `cache/naptan.csv` to ensure search returns correct formatting.
  2. Implement matching by scanning cache or using `BusLoader.get_stop_names_bulk`.
  3. Return list of `{id, name, atco_code, lat, lon}`.
- Acceptance: Pytest passes. Results returned in the same shape the frontend expects.

## 4. Endpoint: /bus/live/{operator}
- Summary: Live vehicle positions for a bus operator using `bus_live.py` logic.
- Files: `api.py`, `tests/test_bus_live.py`.
- Steps:
  1. Create test to assert endpoint returns `[{line, destination, lat, lon}]`.
  2. Create GET route calling `get_bus_live(lat, lon, urls=[...])`.
- Acceptance: Tests pass. Frontend `fetchLiveBusLocations` receives expected JSON.

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

## 7. Endpoint: /weather and /alerts
- Summary: Provide weather and service alert endpoints or proxies.
- Files: `api.py`
- Steps:
  1. Implement `/weather?lat=&lon=` returning `{temperature, summary, icon, forecast: [...]}`.
  2. Implement `/alerts` returning `{id, title, severity, description, affectedLines, start, end}`.
- Acceptance: Frontend hooks accept and display data.

## 8. WebSocket/STOMP live updates adapter
- Summary: Bridge live feeds into a WebSocket or STOMP endpoint for clients.
- Files: `ws_server.py`, integrate into `api.py`.
- Steps:
  1. Implement server that accepts client connections and supports topics `bus`, `train`, `alerts`.
  2. Periodically poll `BusLive` and broadcast to subscribed clients.
- Acceptance: Frontend can connect and receive updates.

## 9. Journey geometry export & multi-leg polylines
- Summary: Produce coordinate arrays for each route leg for map rendering.
- Files: `merged_data.py`, `api.py`.
- Steps:
  1. Ensure `MergedData` includes stop coordinates. 
  2. For each journey leg, extract stop indices and map them to coordinates.
  3. Return `routeGeometries` in `/journey/plan`.
- Acceptance: Frontend polyline rendering shows multi-leg route correctly.

## 10. Station classification algorithm (P27)
- Summary: Compute station classes (hub, interchange, local, request_stop) and scores.
- Files: `station_classifier.py`, `api.py`.
- Steps:
  1. Compute metrics (degree, freq, interchange_score) offline.
  2. Expose `/stops/classify` returning classification list.
- Acceptance: Frontend can request classifications and filter stops.

## 11. Filtering stations by classification (P28)
- Summary: Server-side filtering endpoints to return stops by class.
- Files: `api.py`.
- Steps:
  1. Add query param `classification` to `/search/stops`.
- Acceptance: `?classification=hub` returns expected stops.

## 12. Train data ingestion & parsing
- Summary: Ensure `train_data.py` structures are populated.
- Files: `bus_loader.py` or `train_loader.py`.
- Steps:
  1. Parse rail timetables into `TrainData` objects.
  2. Validate merges with `MergedData`.
- Acceptance: `/rail/departures` returns real train services.

## 13. OSRM integration & walking fallbacks
- Summary: Improve robustness of walking computations.
- Files: `walking.py`, `bus_loader.py`, API `/walking/reachable`.
- Steps:
  1. Make `Walking` configurable with `OSRM_URL`.
  2. Provide deterministic fallback using precomputed `inter_walk` table.
  3. Add GET `/walking/reachable?lat=&lon=`.
- Acceptance: API returns data even if OSRM is unreachable.

## 14. Documentation & Docker compose
- Summary: Developer docs and a `docker-compose.yml`.
- Files: `README.md`, `docker-compose.yml`, `.env.example`.
- Steps:
  1. Add README with setup steps.
  2. Provide a `docker-compose.yml` describing `backend`, `osrm`, `frontend`.
- Acceptance: `docker-compose up` brings up dev stack.

## 15. Frontend integration tasks
- Summary: Update frontend services to call new APIs.
- Files: `transportApi.js`, `useTransportData.js`, `liveUpdatesManager`.
- Steps:
  1. Update base URL config to point to local backend.
  2. Modify consumers to use returned shapes and `routeGeometries`.
  3. Update `liveUpdatesManager` to connect to WebSocket.
- Acceptance: Map shows real routes and receives live updates.

## 16. Production hardening & security
- Summary: Prepare API for production use.
- Files: config in `api.py`.
- Steps:
  1. Add env-based configuration for TLS, CORS, and API keys.
  2. Add rate limiting middleware.
- Acceptance: API rejects invalid input and respects CORS.

## 17. Performance optimizations
- Summary: Speed up routing and repeated API calls.
- Files: `api.py`, `timetable.py`.
- Steps:
  1. Cache `MergedData` and `RaptorRouter`.
  2. Add HTTP caching headers for route requests.
- Acceptance: Repeated identical `/journey/plan` calls are faster.

## 18. Analytics and frequent routes endpoint
- Summary: Track frequent route planning requests.
- Files: `analytics.py`, `api.py`.
- Steps:
  1. Log successful `/journey/plan` requests.
  2. Aggregate recent requests and return top N via `/analytics/frequent-routes`.
- Acceptance: Frontend can call endpoint to pre-fill routes.

## 19. Iterative Development & Priorities
- Summary: Work in small iterations using TDD; follow priorities.
- Suggested order:
  1. Scaffold `api.py` + `/health` + Write basic `pytest` for `/health`.
  2. Implement `/search/stops` + Write test mocking `cache/naptan.csv`.
  3. Add `/bus/live/{operator}` + Write test for expected JSON shape.
  4. Implement `/journey/plan` with geometry + caching + Write routing logic tests.
  5. Implement WebSocket/stomp adapter for live push + Write connection tests.
  6. Docs, Docker compose, and CI workflow validation.
- Acceptance: All code committed includes tests passing the 85% coverage and 100% pass rate rules mandated in `AGENT.md`.
