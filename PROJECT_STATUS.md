# SCC-200 Project Status (Backend + Frontend)

**Date:** February 5, 2026  
**Audience:** Developers/Maintainers  
**Scope:** End-to-end status, run instructions, setup requirements, and known issues.

---

## 1) What has been done

### Backend (transport-backend)
- FastAPI app provides a proxy layer to the SCC transport API plus local endpoints.
- Proxy endpoints implemented for alerts, bus live, bus times/arrivals, rail departures, pricing, weather.
- Local journey planning endpoint wired to a planner implementation.
- Stop search backed by local database search utilities.
- WebSocket endpoint (`/ws/live`) that relays STOMP messages to connected clients.
- Postgres connection utilities and ingest pipeline for NaPTAN + TXC data.

### Frontend (transport-frontend)
- React + Vite SPA with MUI, Leaflet, and React Router.
- Feature-complete UI for dashboard and map views.
- Service layer for API calls, route planning (RAPTOR), and live updates (STOMP).
- Custom hooks for data fetching, favorites, and live updates.
- Accessibility improvements and responsive design.
- Error boundaries and structured component library.

---

## 2) What is left to do (highest priority first)

### Integration and consistency
- Unify API access so all pages use `transportApi.js` (remove direct fetches).
- Ensure API base URL uses HTTPS everywhere to avoid mixed-content failures.
- Confirm API response formats and adjust mapping/transformations.

### Real-time updates
- Validate STOMP broker credentials and topics.
- Activate WebSocket-driven updates in UI (replace polling where appropriate).
- Improve reconnect/backoff handling for transient outages.

### Route planning
- Wire RAPTOR router into UI using real timetable data (currently unused in UI).

### Testing and deployment
- Add integration tests for pages and core flows.
- Add E2E tests (Cypress/Playwright) for main journeys and map.
- Configure CI/CD and production environment variables.

---

## 3) How to run the program

### Prerequisites
- Node.js 18+ (for frontend)
- Python 3.10+ (for backend)
- PostgreSQL 13+ (for backend data)

### Backend (API + WebSocket relay)
```bash
cd transport-backend
python -m venv .venv
. .venv/Scripts/activate
pip install -r requirements.txt

# Required DB env vars
set DB_HOST=localhost
set DB_PORT=5432
set DB_NAME=transport
set DB_USER=postgres
set DB_PASSWORD=your_password

# Optional STOMP vars (for live relay)
set STOMP_HOST=transport.scc.lancs.ac.uk
set STOMP_PORT=61613
set STOMP_USER=
set STOMP_PASSWORD=
set STOMP_TOPICS=/topic/TRAIN_MVT_ALL_TOC,/topic/TD_ALL_SIG_AREA

# Run the API
python main.py
# or
python -m uvicorn main:app --reload --port 8000
```

### Data ingest (optional, for local DB)
```bash
cd transport-backend
# Load NaPTAN (downloads data)
python ingest.py --naptan

# Load TXC XMLs
python ingest.py --txc-dir ./data/18047
```

### Frontend (SPA)
```bash
cd transport-frontend
npm install
npm run dev
```

---

## 4) Environment/setup requirements

### Backend env vars
- `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` (Postgres)
- `SCC_API_BASE_URL` (default: https://transport.scc.lancs.ac.uk)
- `FRONTEND_ORIGIN` (default: http://localhost:5173)
- `STOMP_HOST`, `STOMP_PORT`, `STOMP_USER`, `STOMP_PASSWORD`, `STOMP_TOPICS`

### Frontend configuration
- `src/services/transportApi.js` defines API base URL.
- `src/services/liveUpdates.js` defines STOMP broker URL.

---

## 5) Known issues / bugs (as of Feb 5, 2026)

### Frontend
- Mixed HTTP/HTTPS usage: some API calls or config use `http` while pages use `https`, causing mixed-content failures in browsers.
- Real API integration is partial; several screens still fall back to mock data when responses fail or are incomplete.
- STOMP/WebSocket live updates are present but not fully validated against broker credentials/topics.

### Backend
- No documented migration/seed script beyond `ingest.py`; local DB setup requires manual Postgres provisioning.

---

## 6) Quick sanity checks

- Backend health: GET `http://localhost:8000/health`
- Frontend dev server: `http://localhost:5173`
- API proxy: GET `http://localhost:8000/alerts`

---

## 7) References

- Frontend implementation details: transport-frontend/IMPLEMENTATION.md
- Frontend status (detailed): transport-frontend/PROJECT_STATUS.md
- Project scope and requirements: PROJECT_BRAIN.md
