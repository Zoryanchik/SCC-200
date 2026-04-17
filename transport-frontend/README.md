# Transport App Frontend

This folder contains the React single-page application for the SCC-200 transport system.

## Tech stack

- Vite (build and dev server)
- React + React Router
- Material UI
- React Leaflet / Leaflet

## App routes

- / : Home page
- /map-view : Map-focused page

## Prerequisites

Install these before running locally:

- Node.js 20 or newer (includes npm)
- Backend API running on port 5050 for full functionality
- Optional: OSRM service if you need direct OSRM integration

## Run frontend only (local dev)

1. Open a terminal in this folder.
2. Install dependencies:

```bash
npm install
```

3. Start the dev server:

```bash
npm run dev
```

4. Open:

```text
http://localhost:5075
```

## Run full program (recommended)

Run backend and frontend in separate terminals from the repository root.

1. Start backend:

```bash
cd transport-backend
./run_backend.sh
```

2. Start frontend:

```bash
cd transport-frontend
npm install
npm run dev
```

3. Open the frontend:

```text
http://localhost:5075
```

The frontend expects the backend API at http://localhost:5050 unless overridden.

## Container run (Podman or Docker)

If the backend container is already running on the shared user network, you can run the frontend container with:

```bash
cd transport-frontend
./run_frontend.sh
```

Defaults used by the script:

- Frontend port: 5075
- Network name: scc200-net-edillocnon
- API base URL: http://localhost:5050
- OSRM base URL (optional direct mode): http://127.0.0.1:5012

Override defaults with environment variables:

```bash
NETWORK_NAME=... VITE_API_BASE_URL=... VITE_OSRM_BASE=... ./run_frontend.sh
```

## Common issues

- npm command not found:
  Install Node.js 20+ and restart the terminal.
- Blank or partial live data:
  Confirm backend is running and reachable at http://localhost:5050.
- Port already in use:
  Stop the process using port 5075, then restart npm run dev.
