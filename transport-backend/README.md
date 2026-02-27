Inside transport-backend:

First run OSRM server:
./osrm/run_osrm.sh

Open a new terminal:
./run_backend.sh


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

```zsh
./transport-backend/osrm/run_osrm.sh -d
```

This will extract/customize the map (if needed) and start a container
named `scc200-osrm` that listens on container port `5012`. By default the
script attaches the container to the `scc200-net` network so other
containers can reach it by name.

2. Start the backend container (build if necessary):

```zsh
./transport-backend/run_backend.sh
```

`run_backend.sh` calls `start_container.sh` which by default places the
backend container on the same user network (`scc200-net`) and injects the
environment variable `OSRM_URL=http://scc200-osrm:5012` into the backend
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

If you want, I can add a short dev note to the top-level README describing
this workflow as well.