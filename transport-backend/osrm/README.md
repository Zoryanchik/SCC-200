# OSRM sample runner

This small helper downloads a Geofabrik PBF and runs OSRM (MLD) using the official Docker image, exposing the HTTP API on host port 5321 by default.

Prerequisites
- Docker Desktop installed and running
- At least a few GB free disk space (PBF + OSRM files)

Files added
- `scripts/run_osrm.sh` — downloads the PBF, runs `osrm-extract`, `osrm-partition`, `osrm-customize`, then starts `osrm-routed`.

Usage

Run with defaults (Lancashire PBF, data stored in `./data`, host port 5321):

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

  http://localhost:5321/route/v1/driving/{lon1},{lat1};{lon2},{lat2}

Example query:

```bash
curl "http://localhost:5321/route/v1/driving/-2.123,53.456;-2.234,53.567?overview=false"
```

Troubleshooting
- If Docker fails to run the container due to permissions on the `data` directory, ensure that the directory is writable by your user.
- If the PBF download fails, try downloading it manually (e.g., via browser or `wget`) and place it into the `data` folder with the same filename.

License: public domain (example helper)
