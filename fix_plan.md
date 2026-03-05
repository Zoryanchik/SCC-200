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

| # | Task | Priority |
|---|------|----------|
| P3 | ~~Deprecate/remove duplicate `POST /api/bus_live` endpoint (different shape from GET)~~ ✅ | **Medium** |
| P4 | ~~Document `[lat, lon]` vs GeoJSON `[lon, lat]` coord order in code comments~~ ✅ | Low |
| — | ~~Review mock FastAPI frontend for integration cues~~ (done) | ~~High~~ |
| 8 | ~~Add WebSocket/STOMP live updates adapter~~ ✅ | Medium |
| 9 | Ensure multi-leg route geometry export for map polylines | ✅ Done |
| 10+11 | ~~Add station classification (P27) and filtering (P28)~~ | ✅ Done |
| 13 | ~~Add OSRM integration with walking fallback~~ | ✅ Done |

### Jamie — Frontend Integration & DevOps
Focus: Connecting the frontend to the backend, UI, docs, and hardening.

| # | Task | Priority |
|---|------|----------|
| P1 | ~~Fix `API_BASE_URL` — switch to `VITE_API_BASE_URL` env variable~~ ✅ | ~~High~~ |
| P6 | Make CORS origins env-configurable (currently hardcoded) | **Medium** |
| 15 | ~~Update frontend services and hooks to match API responses~~ ✅ *(mode→type normalisation, weather mock fix, 86 tests)* | ~~Medium~~ |
| 14 | Add developer docs and Docker compose | Medium |
| 16 | Harden production config (CORS, auth, rate limits) | Low |
| 18 | Add analytics and frequent routes endpoint | Low |

---

## Recent requests (2026-02-16)
- Tried to connect backend bus live with frontend but it did not work well. The function takes `latitude` and `longitude` as parameters (current map center). Request: modify frontend to adapt. Note: not familiar with frontend.
- Search bar should support any location search (ideally with prompt), not just stations. Suggests putting the map on the same page as search bar and prioritizing locations on the map.
- Created a simple mock frontend using FastAPI to connect with backend; pushed it. Run `api.py` and go to http://localhost:5050. Note: check overall project in case something changed.

## Session log (2026-03-02)
- **Schema audit of `api.py`:** Mapped all endpoint request/response shapes; identified two critical mismatches:
  1. Journey legs backend field is `mode: "walking"/"bus"/"train"` — NOT `type`. Fixed in `getJourneyPlans()` by adding `mode → type` normalisation (`"walking"` → `"walk"`) so UI components reading `leg.type === 'walk'` work correctly.
  2. `/weather` returns OpenWeatherMap-shaped JSON `{weather:[{main,description,icon}], wind:{speed}, main:{temp,humidity}}` — test mocks were wrong shape. Fixed.
- **Tests:** 86 frontend tests, 100% pass rate. `transportApi.js` statement coverage 90.66%, branches 75.38%, functions 100%.
- **Committed:** `chore(api): document mock FastAPI endpoints and integration cues` → pushed to `origin/main`.
- **AGENT.md updated** with all endpoint payload schemas and gotchas.
- **Bus update countdown badge (2026-03-02):** `useLiveBusLocations` now distinguishes `loading` (first-load overlay) from `refreshing` (background re-fetch). A circular countdown ring is pinned top-right on the map showing seconds until next refresh; it spins purple while fetching and turns green while counting down. Full white overlay is removed from 30-second background polls — map stays usable. 86 tests, 100% pass.

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
