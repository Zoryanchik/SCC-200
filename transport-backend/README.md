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