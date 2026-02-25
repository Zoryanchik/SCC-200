# OSRM helper (transport-backend/osrm)

This helper automates downloading a Geofabrik PBF, preparing OSRM files, and
starting an `osrm-routed` server for local routing tests.

Defaults and locations

- Script: `transport-backend/osrm/run_osrm.sh`
- Default PBF: https://download.geofabrik.de/europe/united-kingdom/england/lancashire-latest.osm.pbf
- Default data directory: `transport-backend/osrm/data` (absolute path next to the script)
-- Default host port: `5012`

Prerequisites

- Docker (the helper invokes `docker` by default)
- A few GB free disk space for the PBF + OSRM intermediate files

How the helper works

1. Download the PBF into the data directory (skips download if the file exists)
2. Run `osrm-extract` to create the initial `.osrm` file (skips if already present)
3. Run `osrm-partition` and `osrm-customize` (skips if outputs already exist)
4. Start `osrm-routed` using the MLD algorithm and expose the HTTP API on the
  host port you provided (default 5012)

Usage

Run with defaults (Lancashire PBF, data stored in `transport-backend/osrm/data`, host port 5012):

```bash
./transport-backend/osrm/run_osrm.sh
```

Override the PBF URL, data directory and port (positional args):

```bash
./transport-backend/osrm/run_osrm.sh "https://download.geofabrik.de/.../myregion-latest.osm.pbf" ./mydata 5012
```

API example

The router will be available at `http://localhost:5012` by default. Example route query:

```bash
curl "http://localhost:5012/route/v1/driving/-2.123,53.456;-2.234,53.567?overview=false"
```

Podman users

If you prefer Podman, you can either run the container commands yourself under
Podman or edit the script to replace `docker` with `podman`. On macOS make
sure the Podman VM is initialized and started (`podman machine init` and
`podman machine start`). If Podman is installed but the VM is not running,
you may see connection errors; Docker is usually a reliable fallback in that
case.

Troubleshooting

- Permission errors when running containers: ensure the `data` directory is
  writable by the user which runs the container.
- If the PBF download fails, download the file manually and place it in the
  data directory with the same filename.

If you'd like, I can add `--force`, checksum verification, or an explicit
`--runtime` flag (`docker|podman`) to the helper — say “improve script” and I
will implement that.

# OSRM sample runner

This small helper downloads a Geofabrik PBF and runs OSRM (MLD) using the official Docker image, exposing the HTTP API on host port 5012 by default.

Prerequisites
- Docker Desktop installed and running
- At least a few GB free disk space (PBF + OSRM files)

Files added
- `scripts/run_osrm.sh` — downloads the PBF, runs `osrm-extract`, `osrm-partition`, `osrm-customize`, then starts `osrm-routed`.

Usage

Run with defaults (Lancashire PBF, data stored in `./data`, host port 5012):

```bash
./scripts/run_osrm.sh
```

Specify an alternative PBF URL, data directory and port (in that order):

```bash
./scripts/run_osrm.sh "https://download.geofabrik.de/.../myregion-latest.osm.pbf" ./mydata 5321
```

Notes
- The script uses the `osrm/osrm-backend:latest` Docker image. If you prefer a pinned version, edit the `DOCKER_IMAGE` variable in the script.
- The script will skip a step if its expected output already exists (so you can restart the router quickly).
- The server runs with the MLD algorithm. The router will be accessible at:

  http://localhost:5012/route/v1/driving/{lon1},{lat1};{lon2},{lat2}

Example query:

```bash
curl "http://localhost:5012/route/v1/driving/-2.123,53.456;-2.234,53.567?overview=false"
```

Troubleshooting
- If Docker fails to run the container due to permissions on the `data` directory, ensure that the directory is writable by your user.
- If the PBF download fails, try downloading it manually (e.g., via browser or `wget`) and place it into the `data` folder with the same filename.

License: public domain (example helper)
