# Transport Backend — My Tasks

Generated: 2026-02-12

This document contains the full, actionable task list (20 items) with implementation details, acceptance criteria, and suggested next steps so we can implement the backend and integrate it with the frontend.

---

## 1. API: Scaffold FastAPI server
- Summary: Add a lightweight HTTP server that exposes the backend functionality to the frontend.
- Why: Current backend is a CLI; frontend expects REST endpoints.
- Files to create/edit: `transport-backend/api.py`, small runner `transport-backend/__main__.py` or usage with `uvicorn`.
- Steps:
  1. Add `FastAPI` dependency (note it in README or `requirements.txt`).
  2. Create `api.py` with `app = FastAPI()` and a `/health` endpoint returning `{status: "ok"}`.
  3. On startup, call `initialize_base()` from `main.py` to create `BusLoader` and walking data and store in `app.state`.
  4. Expose configuration via env vars: `PORT`, `OSRM_URL`, `DB_PATH`.
- Acceptance: `uvicorn transport-backend.api:app --reload` starts and GET `/health` returns 200.

---

## 2. Endpoint: /journey/plan
- Summary: POST endpoint to plan journeys; uses RAPTOR router and returns human-friendly JSON plus geometry.
- Why: Frontend needs journey planning with route geometry to draw polylines.
- Files to edit: `transport-backend/api.py`, reuse `build_for_date()` from `main.py`, `raptor_router.py`, and `merged_data.py` to map stop ids to coordinates.
- Payload: `{fromStop, toStop, departureTime (ISO), date, maxTransfers, mode}`
- Steps:
  1. Receive POST request and validate payload.
  2. Call `build_for_date(loader, walking_raw, date)` or reuse cached networks to avoid rebuilds.
  3. Call `router.route(...)` with parsed parameters.
  4. Convert returned merged stop ids to coordinates using `MergedData` and either loader's stop coords or `MergedData` mapping; construct `routeGeometries` as array of `{id,name,coords,color}`.
  5. Return JSON: `{legs: [...], _meta: {...}, routeGeometries: [...]}`.
- Acceptance: Frontend can consume `routeGeometries[*].coords` to draw polylines.

---

## 3. Endpoint: /search/stops
- Summary: GET search endpoint for stops (NaPTAN) used by frontend autocomplete.
- Why: Frontend `useStopSearch` expects server-side search results.
- Files: `transport-backend/api.py`, optionally helpers in `bus_loader.py` (it already has NaPTAN cache under `cache/`).
- Query: `GET /search/stops?q=cent&limit=10`
- Steps:
  1. Implement matching by scanning `cache/naptan.csv` or using `BusLoader.get_stop_names_bulk` if available.
  2. Return list of `{id, name, atco_code, lat, lon}`.
  3. Support `limit` parameter and a simple prefix/fuzzy match.
- Acceptance: Results returned in the same shape the frontend expects.

---

## 4. Endpoint: /bus/live/{operator}
- Summary: Live vehicle positions for a bus operator using `bus_live.py` logic.
- Why: Real-time map markers and live updates.
- Files: `transport-backend/api.py`, call into `bus_live.py`.
- Query params: optional `lat`, `lon`, `latTol`, `lonTol` for bounding box.
- Steps:
  1. Create GET route that instantiates `BusLive` or reuses a singleton instance.
  2. Call `get_bus_live(lat, lon, urls=[...])` and produce JSON: `[{line, destination, lat, lon}]`.
- Acceptance: Frontend `fetchLiveBusLocations` receives expected JSON.

---

## 5. Endpoint: /rail/departures/{station}
- Summary: Return upcoming departures for a station CRS code.
- Why: `useLiveDepartures` expects departures array.
- Files: `api.py`, `timetable.py`, `merged_data.py` to inspect `journey_times` and `journey_metadata`.
- Steps:
  1. Map station CRS code to merged stop index (use `BusLoader.map_stops` or `MergedData` mapping).
  2. Search `MergedData.route_stop_departures` or similar and compute upcoming departures (compare departure times to now or requested time).
  3. Return records like `{serviceId, destination, scheduledTime, departureTime, status, lat, lon}`.
- Acceptance: `useLiveDepartures` hook works with this response.

---

## 6. Endpoint: /pricing
- Summary: Fare calculation endpoint.
- Why: Frontend displays pricing for journeys.
- Files: `api.py` (and optional `pricing.py` stub)
- Steps:
  1. Implement `GET /pricing?from=<>&to=<>`.
  2. If external fare data is unavailable, implement distance-based stub: compute Haversine distance and apply simple fare bands (e.g., £1.50 up to 2 km, +£0.50 per additional 2 km) and return optional discounts.
  3. Return `{price, currency, fares:[{type, price}]}`.
- Acceptance: `usePricing` receives a consistent structure.

---

## 7. Endpoint: /weather and /alerts
- Summary: Provide weather and service alert endpoints or proxies.
- Why: The UI shows `WeatherWidget` and service alerts.
- Files: `api.py`, optional `weather.py` / `alerts.py` that proxy to public APIs.
- Steps:
  1. Implement `/weather?lat=&lon=` returning `{temperature, summary, icon, forecast: [...]}` (simple shape acceptable).
  2. Implement `/alerts` returning an array of service incidents with `{id, title, severity, description, affectedLines, start, end}`.
- Acceptance: Frontend hooks accept and display data.

---

## 8. WebSocket/STOMP live updates adapter
- Summary: Bridge live feeds into a WebSocket or STOMP endpoint for clients.
- Why: `liveUpdatesManager` expects real-time topic subscriptions.
- Files: `transport-backend/ws_server.py`, integrate into `api.py` or run separately.
- Options:
  - Simple WebSocket: push JSON messages with `{topic, payload}`.
  - STOMP: use a STOMP server implementation if frontend expects STOMP.
- Steps:
  1. Implement server that accepts client connections and supports simple topics `bus`, `train`, `alerts`.
  2. Periodically poll `BusLive` and/or feed train updates and broadcast to subscribed clients.
  3. Implement heartbeat and reconnect semantics.
- Acceptance: Frontend can connect and receive updates.

---

## 9. Journey geometry export & multi-leg polylines
- Summary: Produce coordinate arrays for each route leg for map rendering.
- Why: Frontend needs accurate polylines for the route, not a simple straight line.
- Files: `merged_data.py` (use stop coords), `api.py` (journey response builder).
- Steps:
  1. Ensure `MergedData` includes stop coordinates (map stop_int -> lat/lon). If not, add a mapping using data loaded by `BusLoader`.
  2. For each journey leg (boarding→alighting), extract stop indices and map them to coordinates to create `coords: [[lat,lon], ...]`.
  3. Return `routeGeometries` in `/journey/plan` as `{id,name,coords,color}`.
- Acceptance: Frontend polyline rendering shows multi-leg route correctly.

---

## 10. Station classification algorithm (P27)
- Summary: Compute station classes (hub, interchange, local, request_stop) and scores.
- Why: P27/P28 frontend filtering depends on classification.
- Files: `transport-backend/station_classifier.py`, `api.py` for endpoint.
- Algorithm suggestions:
  - `degree`: number of unique routes serving stop.
  - `freq`: average departures per hour across routes.
  - `interchange_score`: number of different modes (bus/train) present.
  - Combine into `score = w1*norm(degree) + w2*norm(freq) + w3*norm(interchange)`.
  - Thresholds: score > 0.8 => `hub`, >0.6 => `interchange`, >0.3 => `local`, else `request_stop`.
- Steps:
  1. Compute metrics offline when building `MergedData` and store results in memory or DB.
  2. Expose `/stops/classify` returning classification list and `/search/stops` can include classification field.
- Acceptance: Frontend can request classifications and filter stops.

---

## 11. Filtering stations by classification (P28)
- Summary: Server-side filtering endpoints to return stops by class.
- Why: Improves frontend performance and supports P28.
- Files: `api.py`.
- Steps:
  1. Add query param `classification` to `/search/stops` or new endpoint `/stops?classification=hub`.
  2. Use precomputed classification indices to filter quickly.
- Acceptance: `?classification=hub` returns expected stops.

---

## 12. Train data ingestion & parsing
- Summary: Ensure `train_data.py` structures are populated (currently placeholders exist).
- Why: Full routing and departures require train data.
- Files: extend `bus_loader.py` or create `train_loader.py` to parse rail timetables into `TrainData` objects.
- Steps:
  1. Determine train data source format (e.g., GTFS-RT, CSV, or local dataset). 
  2. Implement parsing to fill `route_stops`, `route_journeys`, `journey_times`, `stop_to_routes`, `journey_metadata`.
  3. Validate merges with `MergedData` and run `build_for_date`.
- Acceptance: `/rail/departures` returns train services.

---

## 13. OSRM integration & walking fallbacks
- Summary: Improve robustness of walking computations using OSRM or precomputed transfers.
- Why: User location and walking transfers required for routing and nearest-stop logic.
- Files: `walking.py`, `bus_loader.py` (precompute), API `/walking/reachable`.
- Steps:
  1. Make `Walking` configurable with `OSRM_URL` and add retries/timeouts.
  2. Provide deterministic fallback using precomputed `inter_walk` table.
  3. Add GET `/walking/reachable?lat=&lon=` endpoint returning reachable stops with seconds.
- Acceptance: API returns data even if OSRM is unreachable (using fallback table).

---

## 14. Testing: unit & integration
- Summary: Add test coverage for core modules and API.
- Why: Ensure reliability and make refactoring safer.
- Files: new `tests/` directory, CI config (e.g., `.github/workflows/ci.yml`).
- Steps:
  1. Add unit tests for `raptor_router` (multiple scenarios), `merged_data` mapping, and `walking` using mocks for OSRM.
  2. Add API tests using `FastAPI TestClient` for endpoints `/search/stops`, `/journey/plan` (with mocked networks).
  3. Add GitHub Actions workflow to run tests on push/PR.
- Acceptance: Tests pass in CI for base functionality.

---

## 15. Documentation & Docker compose
- Summary: Developer docs and a `docker-compose.yml` for running the stack (backend + OSRM + optional broker).
- Why: Simplifies developer onboarding and deployment.
- Files: `transport-backend/README.md`, `docker-compose.yml` at repo root, `.env.example`.
- Steps:
  1. Add README with steps to seed DB, run API, run tests, and OSRM setup notes.
  2. Provide a `docker-compose.yml` describing services: `backend` (app), `osrm` (image), `frontend` (optional local dev server) and volumes for map data.
- Acceptance: `docker-compose up` brings up dev stack (OSRM requires map file path instructions documented).

---

## 16. Frontend integration tasks
- Summary: Update frontend services to call new APIs and use `routeGeometries`.
- Why: Replace demo route rendering with real server data and wire live updates.
- Files: edit `transport-frontend/src/services/transportApi.js`, `src/hooks/useTransportData.js`, and `liveUpdatesManager`.
- Steps:
  1. Update base URL config in `transportApi.js` to point to local backend.
  2. Modify `getJourneyPlans` / `fetchRailDepartures` consumers to use returned shapes and `routeGeometries` to render polylines (remove demo route builder once server data is used).
  3. Update `liveUpdatesManager` to connect to WebSocket/STOMP endpoint implemented in backend.
- Acceptance: Map shows real routes and receives live updates.

---

## 17. Production hardening & security
- Summary: Prepare API for production use (TLS, auth, rate limits).
- Why: If deployed publicly, need secure configuration.
- Files: config in `api.py`, documentation and infrastructure notes.
- Steps:
  1. Add env-based configuration for TLS, allowed origins (CORS), and optional API keys.
  2. Consider rate limiting middleware and input validation.
- Acceptance: API rejects invalid input and respects CORS settings.

---

## 18. Performance optimizations
- Summary: Speed up routing and repeated API calls.
- Why: RAPTOR planning can be CPU-intensive; caching will help.
- Files: `api.py` caching layer, modifications in `timetable.py` or `raptor_router.py`.
- Steps:
  1. Cache `MergedData` and `RaptorRouter` per date and mode in memory (LRU or TTL-based).
  2. Precompute `route_stop_departures` for the merged network (already done in `MergedData`) and ensure retrieval is efficient.
  3. Add HTTP caching headers for route requests where applicable.
- Acceptance: repeated identical `/journey/plan` calls are significantly faster.

---

## 19. Analytics and frequent routes endpoint
- Summary: Track frequent route planning requests and expose suggestions.
- Why: Help frontend auto-suggest favorites and learn common journeys.
- Files: new `analytics.py`, `api.py` route `/analytics/frequent-routes`.
- Steps:
  1. Log successful `/journey/plan` requests (minimal logging: from,to,date).
  2. Periodically aggregate recent requests (in-memory or persisted) and return top N.
- Acceptance: frontend can call endpoint to pre-fill suggested routes.

---

## 20. Iterative development & priorities
- Summary: Work in small iterations; follow priorities.
- Suggested order:
  1. Scaffold `api.py` + `/health` and `/search/stops` (fast wins).
  2. Add `/bus/live/{operator}` to support live markers.
  3. Implement `/journey/plan` with geometry; add caching.
  4. Implement WebSocket/stomp adapter for live push.
  5. Add classification endpoints and pricing/weather stubs.
  6. Tests, docs, Docker compose, CI.
- Acceptance: incremental PRs with tests and docs.

---

### How I can proceed now
If you want I can scaffold the FastAPI app and implement the `/health` and `/search/stops` endpoints next. This will make a visible, runnable API quickly so the frontend can point to something real.

---

File: [transport-backend/TRANSPORT_BACKEND_MY_TASKS.md](TRANSPORT_BACKEND_MY_TASKS.md)

