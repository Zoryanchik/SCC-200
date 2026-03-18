# Transport App Frontend

The frontend is a SPA built using React.

Stack/Libraries:
- Vite - bundler
- React - UI Framework
- React-Router - Client-side routing
- (MUI) Material UI - Component & Styling
- React-Leaflet/Leaflet - Map Components

## Routes:
- / -> Home Page (possible Dashboard)
- /map-view -> The primary map overview page

## Running

First ensure dependencies are installed:

```bash
npm install
```

Then open a local server via:
```bash
npm run dev
```

By default the development server binds to port 5075 for this project. To open the app in a browser use:

```bash
http://localhost:5075
```

### Run in a Podman container (port 5075)

If you're already running the backend container on the default user network (`scc200-net-edillocnon`), you can start the frontend in a matching container too:

```bash
cd transport-frontend
./run_frontend.sh
```

Notes:

- The script will free host port **5075** if it’s already in use.
- The container joins the same network as the backend (default: `scc200-net-edillocnon`).
- By default it points the frontend at the backend container name:
	- `VITE_API_BASE_URL=http://transport-backend-edillocnon:5050`
- If OSRM is running on the same network, it also sets:
	- `VITE_OSRM_BASE=http://transport-osrm-edillocnon:5012`

You can override any of these with environment variables:

```bash
NETWORK_NAME=... VITE_API_BASE_URL=... VITE_OSRM_BASE=... ./run_frontend.sh
```
