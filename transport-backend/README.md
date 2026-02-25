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

## OSRM (Routing) service

This backend expects an OSRM routed server to be available at http://localhost:5321 by default. You can override the endpoint used by the backend with the `OSRM_URL` environment variable (for example `export OSRM_URL=http://127.0.0.1:5321`).

Quick options to provide an OSRM instance:

- Use a host container mapped to port 5321 (Docker or Podman):

```sh
# Build or acquire an OSRM dataset and run the routed server (host port 5321 -> container 5000)
docker run -d -p 5321:5000 -v /path/to/data:/data osrm/osrm-backend \
	osrm-routed --algorithm mld /data/your-area.osrm
```

- If you're on macOS and prefer Podman note that Podman uses a small Linux VM. Either start that VM first (`podman machine init` then `podman machine start`) or use Docker as a fallback. The backend will read `OSRM_URL` and connect to the running service.

Notes:
- The default port previously used was 5001 for historical reasons — this repository now expects 5321 to avoid colliding with other local services.
- If you run OSRM in a container, make sure the container has access to the prepared `.osrm` files (from the extract/partition/customize steps).


Using `--sample` is handy for quick iteration; once you need real routes you
can rebuild with `--pbf-url` pointed at a larger extract (or just omit the
option to get the default UK coverage).

The cache directory (`transport-backend/cache/osrm`) is reused across runs and
is not checked into git; copy it from another machine if you’d rather avoid
downloading the same extract multiple times.
first start podman virtual environment:
./run_backend.sh

to run mock frontend:
python3 api.py

go to http://localhost:5005


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