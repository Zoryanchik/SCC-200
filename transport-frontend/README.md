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
- By default it points the frontend at a host-reachable backend URL:
	- `VITE_API_BASE_URL=http://localhost:5050`
- OSRM road-snapping is performed via the backend proxy endpoint by default:
	- the frontend calls `GET /osrm/route` on the backend
	- the backend forwards to its configured `OSRM_URL` (can be a docker-only hostname)

If you *really* want the browser to call OSRM directly, you can set:
	- `VITE_OSRM_BASE=http://127.0.0.1:5012`

You can override any of these with environment variables:

```bash
NETWORK_NAME=... VITE_API_BASE_URL=... VITE_OSRM_BASE=... ./run_frontend.sh
```

## Development notes: node_modules volume and fast container starts

The `run_frontend.sh` script mounts a named volume for `node_modules` (named
`transport-frontend-node_modules-edillocnon`). This avoids reinstalling all
dependencies on every dev container start, which significantly speeds up the
startup time.

Recommended workflows:

- Pre-populate the named volume once (fast subsequent starts):

```bash
# from the transport-frontend/ directory
podman run --rm -v "$PWD":/app -v transport-frontend-node_modules-edillocnon:/app/node_modules -w /app \
	--user "$(id -u):$(id -g)" node:20-alpine /bin/sh -c 'npm ci --silent --no-audit --progress=false'
```

- Install locally on your host (alternative):

```bash
cd transport-frontend
npm ci
```

- If you let the script install inside a container, it may take several
	minutes on first run. `run_frontend.sh` has a configurable wait timeout
	controlled by `READY_TIMEOUT_SECS` (default now 600 seconds). To override:

```bash
READY_TIMEOUT_SECS=600 ./run_frontend.sh
```

Permission notes:
- Avoid running installs as root into the host-mounted workspace; files
	created by root inside `node_modules` will require chown on the host.
- Pre-populating the named volume (first example above) runs npm as your
	user inside the container and prevents ownership problems.
