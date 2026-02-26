# Development and local testing (transport-backend)

This directory contains backend helpers and small-area test utilities. The
repository ships a small Lancashire PBF by default so you can run routing
locally without downloading the full UK extract.

Key points
- OSRM routed server (HTTP) — expected at `http://localhost:5012` by default.
  You can override this with the `OSRM_URL` environment variable (for example
  `export OSRM_URL=http://127.0.0.1:5012`).
- Canonical local OSRM data directory: `transport-backend/osrm/data`. The
  helper script `transport-backend/osrm/run_osrm.sh` stores and reads PBF and
  `.osrm` files here by default.
- The included Lancashire extract is intended for development and testing.

Running OSRM locally

The repository includes a helper script that downloads a PBF (Lancashire by
default), runs `osrm-extract` / `osrm-partition` / `osrm-customize`, and starts
`osrm-routed` using the `osrm/osrm-backend` image.

Script location and defaults

```
transport-backend/osrm/run_osrm.sh
Default PBF: https://download.geofabrik.de/europe/united-kingdom/england/lancashire-latest.osm.pbf
Default data dir: transport-backend/osrm/data
Default host port: 5012
```

Basic usage (from the repository root):

```bash
./transport-backend/osrm/run_osrm.sh
```

Override the PBF, data dir and port (positional args):

```bash
./transport-backend/osrm/run_osrm.sh "https://download.geofabrik.de/.../myregion-latest.osm.pbf" ./mydata 5012
```

Notes on container runtimes

- The helper scripts prefer Podman when it is installed and available. If
  Podman is not available or not connected, the scripts fall back to Docker.
  On macOS Podman typically runs inside a small VM; if you are using Podman
  make sure the VM is initialised (`podman machine init`) and started
  (`podman machine start`). Docker is supported as a fallback and will be
  used automatically when Podman is not usable.

Running the backend

Start the backend helper which will attempt to connect to the OSRM server at
the URL provided by `OSRM_URL` (or `http://localhost:5012` by default). The
backend listens on port 5050 by default (container and host mapping when
using the helper):

```bash
./transport-backend/run_backend.sh
```

- Other notes

- The repository keeps an OSRM cache under `transport-backend/cache/osrm`.
  This directory is not checked in to Git.
- Ensure the `transport-backend/osrm/data` directory is writable by the user
  running containers (permissions issues can cause the container to fail to
  write intermediate files).
- The helper start script will create and attach containers to a user
  network called `scc200-net` so the backend and OSRM instances can reach
  each other by name (OSRM default container name: `scc200-osrm`).
- If you'd like, I can add a troubleshooting section and a short
  `transport-backend/osrm/README.md` with common errors and quick fixes.
# Development / small-area testing

The `build_osrm.sh` helper used to default to the full England extract; to keep this
repository lightweight the helper and included scripts now use a Lancashire-only
PBF by default. The Lancashire dataset is much smaller than the full England
extract but still provides realistic coverage for local testing.

If you’re just experimenting or running on a fresh machine you can still avoid
long waits by using an even smaller sample dataset:

```sh
cd transport-backend
# download a tiny Monaco extract (≈2 MB) and build that instead of the UK map
./scripts/build_osrm.sh --sample

# or supply any other valid URL or local file:
./scripts/build_osrm.sh --pbf-url https://download.geofabrik.de/europe/monaco-latest.osm.pbf
```

## OSRM (Routing) service

This backend expects an OSRM routed server to be available at http://localhost:5012 by default. You can override the endpoint used by the backend with the `OSRM_URL` environment variable (for example `export OSRM_URL=http://127.0.0.1:5012`).

Quick options to provide an OSRM instance:

- Use the included helper to build and run OSRM (recommended):

```sh
# build data and run osrm (detached)
./transport-backend/osrm/run_osrm.sh -d
```

- Or run the container manually (example using Podman/Docker):

```sh
# run an osrm-routed container with host port 5012 -> container 5012
podman run -d --name scc200-osrm -p 5012:5012 -v /path/to/data:/data docker.io/osrm/osrm-backend \
  osrm-routed --algorithm mld -p 5012 /data/your-area.osrm
```

Notes:

- Default ports used by the repository:
  - OSRM HTTP API: 5012 (host and container port by default)
  - Backend service (FastAPI / uvicorn): 5050 (container and host mapping when using the helper)
- If you run OSRM in a container, make sure the container has access to the prepared
  `.osrm` files (from the extract/partition/customize steps).


Using `--sample` is handy for quick iteration; once you need wider coverage you
can rebuild with `--pbf-url` pointed at a larger extract. By default the
helper uses the Lancashire extract supplied with this repository.

The cache directory (`transport-backend/cache/osrm`) is reused across runs and
is not checked into git; copy it from another machine if you’d rather avoid
downloading the same extract multiple times.
first start podman virtual environment:
./run_backend.sh

to run mock frontend:
python3 api.py

go to http://localhost:5050


Run main.py in terminal
Waiting to be connected to front-end
Lacking train data
Assumes to deals with bus and walking, including overnight buses, operational days, etc.
Output seems correct, but requires more tests




Router:
- Run main.py in terminal
- Waiting to be connected to front-end
- Lacking train data
- Assumes to deals with bus and walking, including overnight buses, operational days, etc.
- Output seems correct, but requires more tests

Bus Live:
- Run bus_live.py in terminal
- Waiting to be connected to front-end
- Input lat, lon should be the mid-point of the map-window 
- Should add function of getting mid-point and updating every 5 seconds after connected to front-end

Router and Bus Live should be concurrent, maybe using thread.