Inside transport-backend:

Run PGSQL server:
./run_pgsql

Run OSRM server:
./osrm/run_osrm.sh

To run inside a container:
	Open a new terminal:
	./run_backend.sh

//OR
To install requirements and run locally:
pip install -r requirements.txt
python3 api.py


# Development / small-area testing

The `build_osrm.sh` helper will by default download and preprocess the
entire England extract from Geofabrik, which is several hundred megabytes and
takes many minutes to convert.  If you’re just experimenting or running on a
fresh machine you can avoid the long wait by using a much smaller dataset:

```sh
cd transport-backend
# download a tiny Monaco extract (≈2 MB) and build that instead of the UK map
./scripts/build_osrm.sh --sample

# or supply any other valid URL or local file:
./scripts/build_osrm.sh --pbf-url https://download.geofabrik.de/europe/monaco-latest.osm.pbf
```

Using `--sample` is handy for quick iteration; once you need real routes you
can rebuild with `--pbf-url` pointed at a larger extract (or just omit the
option to get the default UK coverage).

The cache directory (`transport-backend/cache/osrm`) is reused across runs and
is not checked into git; copy it from another machine if you’d rather avoid
downloading the same extract multiple times.

## Running OSRM and the backend together (development)

This project includes small helpers to run an OSRM backend (the routing
engine) and the FastAPI transport backend so they can communicate on the
same container network. The recommended development workflow is:

1. Start OSRM (MLD) in a container. From the project root:

````markdown
Postgres-only note
-------------------

This backend is Postgres-only. It expects Postgres DSNs for the BUS/TRAIN/WALK
databases (the default development DSN points at 127.0.0.1:5011). For local
development you can start a Postgres container using the included helper:

```zsh
./run_pgsql.sh
```

Then start the backend container or run locally:

Container (recommended):
```zsh
./run_backend.sh
```

Local (development):
```zsh
cd transport-backend
# start uvicorn locally; server will listen on 127.0.0.1:5050 by default
python3 __main__.py
```

If you need custom DB locations, set `BUS_DB_DSN`, `TRAIN_DB_DSN`, and
`WALK_DB_DSN` in your environment before starting the backend.
````
```zsh
./transport-backend/osrm/run_osrm.sh -d
```

This will extract/customize the map (if needed) and start a container
named `transport-osrm-edillocnon` that listens on container port `5012`. By default the
script attaches the container to the `scc200-net-edillocnon` network so other
containers can reach it by name.

2. Start the backend container (build if necessary):

```zsh
./transport-backend/run_backend.sh
```

`run_backend.sh` calls `start_container.sh` which by default places the
backend container on the same user network (`scc200-net-edillocnon`) and injects the
environment variable `OSRM_URL=http://transport-osrm-edillocnon:5012` into the backend
container so it talks to OSRM by container name. The backend exposes port
`5050` on the host by default (host:5050 -> container:5050).

Environment overrides
- To use a different network name, set `NETWORK_NAME` before running the
	script:

```zsh
NETWORK_NAME=my-net ./transport-backend/run_backend.sh
```

- To force the backend to use host networking instead of the user
	network, set `USE_HOST_NETWORK=1` (useful on Linux):

```zsh
USE_HOST_NETWORK=1 ./transport-backend/run_backend.sh
```

- If you prefer the backend to talk to a local OSRM instance on the host
	rather than the container, set `OSRM_URL` in your environment before
	launching the backend container.

Notes and troubleshooting
- A plain GET to the root path `/` on OSRM returns HTTP 400 — that is
	expected. Use specific endpoints (for example `/route` or `/table`) to
	verify the service is working.
- If your frontend or backend reports "OSRM not reachable", capture the
	exact URL and method being used and verify it against the examples in
	`transport-backend/walking.py` (it uses `/table` and `/route`).
- If you see errors related to very long URLs from `/table`, reduce the
	number of candidate stops or use an alternative approach (nearest-N
	prefiltering) to avoid oversized queries.

System dependencies for XML parsing (lxml)
----------------------------------------

The backend prefers the `lxml` XML parser for performance and robustness. The
project's `requirements.txt` already lists `lxml`, but on some platforms `pip`
may need native development libraries to build it. If you run into install
errors when installing requirements, make sure these packages are present on
your system (Debian/Ubuntu names shown):

```sh
sudo apt-get install -y libxml2-dev libxslt1-dev zlib1g-dev pkg-config
```

If you're using the provided Dockerfile or `run_backend.sh`, the Docker image
already installs these packages so `lxml` will be available in the container.
You only need to install the system libs manually when running locally.

If you want, I can add a short dev note to the top-level README describing
this workflow as well.

## Docker Compose (recommended)

For a simple, reproducible development workflow you can use the provided
docker-compose file which brings up a Postgres database and the backend on a
single user network with sensible defaults.

From the repository root:

```zsh
# start Postgres + backend (builds the backend image)
docker compose -f docker-compose.yml up --build

# bring the services down (stops and removes containers but preserves DB volume)
docker compose -f docker-compose.yml down
```

The compose file defines:
`postgres` — Postgres 15 listening on host port `127.0.0.1:5011` (container
	port `5011`). The data is persisted in a named volume `transport-postgres-data`.
`backend` — the transport backend built from `transport-backend/` and attached
	to the same network; the backend is configured by default to use the
	container hostname `transport-postgres-edillocnon` so it connects to the DB using
	the service name. If you prefer host networking or a different DSN set
	`BUS_DB_DSN`, `TRAIN_DB_DSN` and `WALK_DB_DSN` in the environment (or
	override them in your compose file).

If you experience name resolution issues on your platform (some Podman/macOS
setups), the `start_container.sh` helper will try to detect the Postgres
container IP and forward a working DB DSN into the backend container. Using
docker compose avoids that complexity because Docker provides built-in
service discovery between services declared in the same compose file.

### Running OSRM with Compose

The compose file can also bring up an OSRM routing service so the backend can
perform walking/table queries without additional setup. OSRM requires a
preprocessed dataset (a .osrm bundle) mounted into the container at `/data`.
You can create this dataset locally with the helper script in
`transport-backend/scripts/build_osrm.sh` (use `--sample` for a small test
dataset) or supply your own prepared `.osrm` files under `./osrm/data`.

Example (build a small sample dataset and run the full stack):

```zsh
# build a small test dataset (runs on the host)
cd transport-backend
./scripts/build_osrm.sh --sample

# from the repo root: bring up Postgres, backend, and OSRM
docker compose -f docker-compose.yml up --build
```

When OSRM is running the backend will be configured (by default) with
`OSRM_URL=http://transport-osrm:5012` so it addresses the local OSRM service by
container name.