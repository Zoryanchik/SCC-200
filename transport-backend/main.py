"""Transport Backend — system initialisation and per-date network builder.

``initialize_base()``
    One-time startup: opens database connections for bus, walking and
    train stores, downloads timetable data if needed, and starts
    background precomputation of walking transfers.

``build_for_date(loader, walking_raw, date_str, …)``
    Builds a date-specific MergedData + RaptorRouter + Walking triple.
    Two adjacent day-halves are merged (yesterday+today or
    today+tomorrow) depending on the departure time.

``main()``
    Interactive CLI loop for testing — reads start/end coordinates,
    departure date/time, runs RAPTOR and pretty-prints the result.
"""

from bus_loader import BusLoader
from walking_loader import WalkingLoader
from train_loader import TrainLoader
from merged_data import MergedData
from raptor_router import RaptorRouter
from walking import Walking
from time_utils import seconds_since_midnight, seconds_to_time
import os
from datetime import date as _date, timedelta as _timedelta

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
# Postgres-first: prefer environment DSNs, but provide a sensible
# local default for development so the app runs without extra setup.
DEFAULT_PG = "postgresql://pguser:pgpass@127.0.0.1:5011/transport"
BUS_DB_PATH   = os.environ.get("BUS_DB_DSN") or DEFAULT_PG
TRAIN_DB_PATH = os.environ.get("TRAIN_DB_DSN") or DEFAULT_PG
WALK_DB_PATH  = os.environ.get("WALK_DB_DSN") or DEFAULT_PG


def initialize_base():
    """One-time setup: database, downloads, walking infrastructure.

    Returns a dict with 'loader' and 'walking_raw'.  Walking data
    is stored by ATCO code and remapped to per-date stop integers
    each time build_for_date() is called.
    """
    print("=" * 60)
    print("Initializing Transport Backend System")
    print("=" * 60)

    # 1. Database & downloads ------------------------------------------
    print("\n[1/2] Initialising bus, walking and train stores (concurrent)...")
    os.makedirs(CACHE_DIR, exist_ok=True)

    # We'll run bus, walking (download only), and train init concurrently.
    from concurrent.futures import ThreadPoolExecutor
    # SQLite fallback removed — backend is Postgres-first. We keep
    # DSN-based defaults so local development still works without env
    # variables being set.

    def _bus_task():
        loader = BusLoader(BUS_DB_PATH, walking_db_path=WALK_DB_PATH)
        loader.ensure_db()
        loader.create_schema()
        # Use the loader's connection helper so DB DSNs (Postgres) work
        conn = loader._connect()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM bus_route_stops")
        _count = cur.fetchone()[0]
        conn.close()
        data_changed = False
        if _count == 0:
            print("  Bus DB empty — downloading timetable data...")
            datasets = loader._fetch_dataset_info()
            print(f"  Got {len(datasets)} download URLs")
            # Download and load datasets concurrently (one worker per URL).
            # Each worker will perform the download/load and then persist the
            # dataset metadata using its own DB connection to avoid sharing
            # cursors between threads.
            from concurrent.futures import ThreadPoolExecutor, as_completed

            def _download_and_save(ds, retries=3, backoff=1.0):
                import time, traceback
                last_exc = None
                for attempt in range(1, retries + 1):
                    try:
                        loader.download_and_load(ds['download_url'])
                        # Persist dataset metadata using a fresh connection
                        conn2 = loader._connect()
                        cur2 = conn2.cursor()
                        cur2.execute(
                            "INSERT INTO bus_dataset_meta (source_url, download_url, modified) VALUES (%s, %s, %s) "
                            "ON CONFLICT (source_url) DO UPDATE SET download_url = EXCLUDED.download_url, modified = EXCLUDED.modified",
                            (ds['source_url'], ds['download_url'], ds['modified']),
                        )
                        conn2.commit()
                        conn2.close()
                        return (ds, None)
                    except Exception as e:
                        last_exc = e
                        # simple backoff
                        if attempt < retries:
                            time.sleep(backoff * (2 ** (attempt - 1)))
                        else:
                            # final failure, return exception info
                            tb = traceback.format_exc()
                            return (ds, (e, tb))

            max_workers = min(4, max(1, len(datasets)))
            successes = []
            failures = []
            with ThreadPoolExecutor(max_workers=max_workers) as dex:
                futures = {dex.submit(_download_and_save, ds): ds for ds in datasets}
                for fut in as_completed(futures):
                    ds, result = fut.result()
                    if result is None:
                        successes.append(ds)
                    else:
                        failures.append((ds, result))

            if successes:
                print(f"  ✓ Timetable data loaded for {len(successes)}/{len(datasets)} dataset(s)")
                data_changed = True
            if failures:
                print(f"  ⚠ Failed to load {len(failures)}/{len(datasets)} dataset(s):")
                for ds, (exc, tb) in failures:
                    print(f"    - {ds.get('source_url')} -> error: {exc}")
        else:
            data_changed = loader.check_for_updates()
        print("  ✓ Bus DB ready")
        return loader, data_changed

    def _walking_download_task():
        wl = WalkingLoader(WALK_DB_PATH)
        wl.create_schema()
        wl.download_stop_coords()
        return wl

    def _train_task():
        tl = TrainLoader(TRAIN_DB_PATH)
        tl.ensure_db()
        tl.create_schema()
        print("  ✓ Train DB ready")
        return tl

    with ThreadPoolExecutor(max_workers=3) as ex:
        bus_fut = ex.submit(_bus_task)
        walk_fut = ex.submit(_walking_download_task)
        train_fut = ex.submit(_train_task)

        # Wait for bus and walking download to finish; train is a lightweight init
        loader, data_changed = bus_fut.result()
        walking_loader = walk_fut.result()
        train_loader = train_fut.result()

    print("  ✓ Databases initialised")

    # 2. Walking — coords, precomputed transfers, OSRM ----------------
    print("\n[2/2] Preparing walking data...")

    import urllib.request as _ur
    import urllib.error as _ue
    
    osrm_ok = False
    # Allow the OSRM endpoint to be overridden by env var so containers can
    # address an OSRM sidecar by name (e.g. http://osrm:5012) or use host
    # networking. Default for local development is http://localhost:5012.
    OSRM_URL = os.environ.get("OSRM_URL", "http://localhost:5012")
    try:
        # Probe the server root and treat any HTTP response (including
        # HTTPError) as evidence the service is reachable.
        probe_url = OSRM_URL.rstrip("/") + "/"
        _r = _ur.urlopen(probe_url, timeout=3)
        _r.close()
        osrm_ok = True
    except _ue.HTTPError:
        osrm_ok = True
    except _ue.URLError:
        osrm_ok = False

    if not osrm_ok:
        print(f"  ⚠  OSRM not reachable at {OSRM_URL}")
        print("     Walking transfers will be unavailable.")
        print("     To enable, run an OSRM server and ensure the backend can reach it.")
        print("     Examples:")
        print("       # Run OSRM on the host (backend running on host will reach it):")
        print("       docker run -d -p 5012:5012 -v /path/to/data:/data \\")
        print("         osrm/osrm-backend osrm-routed --algorithm mld -p 5012 /data/nw-england.osrm")
        print("       # Run OSRM as a separate container and point backend to it:")
        print("       docker network create scc-net || true")
        print("       docker run -d --name osrm --network scc-net osrm/osrm-backend \\")
        print("         osrm-routed --algorithm mld -p 5012 /data/nw-england.osrm")
        print("       # Then run the backend on the same network and set OSRM_URL=http://osrm:5012")

    # Reuse the WalkingLoader from step 1 (already has schema + coords)
    if data_changed:
        walking_loader.clear_walking_transfers()
    # Start precompute in background so startup is non-blocking.
    if osrm_ok:
        walking_loader.start_precompute_background(osrm_base=OSRM_URL)
    else:
        # If OSRM is not available, run the haversine fallback in background
        walking_loader.start_precompute_background(use_fallback=True)

    # Build inter_walk table keyed by ATCO codes (will be remapped
    # to per-date integer IDs when the network is built)
    raw_transfers = walking_loader.get_walking_transfers()
    raw_coords    = walking_loader.get_all_stop_coords()

    walking_raw = {
        "transfers": raw_transfers,   # {atco: {atco: secs}}
        "coords":    raw_coords,      # {atco: (lat, lon)}
    }

    print(f"  ✓ Walking ready  ({len(raw_transfers)} stops with transfers, "
          f"{len(raw_coords)} with coords)")

    print("\n" + "=" * 60)
    print("Base Initialization Complete!")
    print("=" * 60)

    return {
        "loader": loader,
        "walking_raw": walking_raw,
        "walking_loader": walking_loader,
    }


def build_for_date(loader, walking_raw, date_str, mode="both",
                   start_time=None):
    """Build a date-specific MergedData + Router + Walking.

    Two adjacent day-halves are merged so that cross-midnight services
    are visible:

    * *start_time* < noon  → merge yesterday (−86 400 s) with today (0 s).
    * *start_time* ≥ noon or ``None`` → merge today (0 s) with tomorrow
      (+86 400 s).

    Bus and train data are loaded in parallel.  Walking transfers are
    remapped from ATCO codes to the combined MergedData stop integers
    so the router's walking lookups remain consistent.

    Parameters
    ----------
    loader : BusLoader
    walking_raw : dict
        ``{"transfers": {atco: {atco: secs}}, "coords": {atco: (lat, lon)}}``
    date_str : str   (YYYY-MM-DD)
    mode : str
        ``"bus"``, ``"train"``, or ``"both"``
    start_time : int | None
        Seconds since midnight.

    Returns
    -------
    tuple[MergedData, RaptorRouter, Walking]
    """
    NOON = 43200  # seconds since midnight
    if start_time is not None and start_time < NOON:
        # Morning: yesterday + today
        day_a_str = (_date.fromisoformat(date_str) - _timedelta(days=1)).isoformat()
        day_b_str = date_str
        offset_a, offset_b = -86400, 0
        label = "AM (yesterday + today)"
    else:
        # Afternoon / default: today + tomorrow
        day_a_str = date_str
        day_b_str = (_date.fromisoformat(date_str) + _timedelta(days=1)).isoformat()
        offset_a, offset_b = 0, 86400
        label = "PM (today + tomorrow)"

    print(f"\n  Building network for {date_str} — {label}")

    from concurrent.futures import ThreadPoolExecutor
    train_loader = TrainLoader(TRAIN_DB_PATH)

    with ThreadPoolExecutor(max_workers=4) as ex:
        bus_a_f = ex.submit(loader.load_busdata_for_date, day_a_str)
        train_a_f = ex.submit(train_loader.load_traindata_for_date, day_a_str)
        bus_b_f = ex.submit(loader.load_busdata_for_date, day_b_str)
        train_b_f = ex.submit(train_loader.load_traindata_for_date, day_b_str)

        bus_a = bus_a_f.result()
        train_a = train_a_f.result()
        bus_b = bus_b_f.result()
        train_b = train_b_f.result()

    print(f"    Day A ({day_a_str}): {len(bus_a.map_journeys)} bus journeys")
    print(f"    Day B ({day_b_str}): {len(bus_b.map_journeys)} bus journeys")

    # Build datasets list respecting mode filter
    datasets = []
    if mode != "train":
        datasets.append((bus_a, offset_a))
    if mode != "bus":
        datasets.append((train_a, offset_a))
    if mode != "train":
        datasets.append((bus_b, offset_b))
    if mode != "bus":
        datasets.append((train_b, offset_b))

    merged = MergedData(
        datasets,
        stop_name_fn=loader.get_stop_names_bulk,
    )
    router = RaptorRouter(merged)

    # ── Remap walking data to MergedData stop integers ───────────
    # MergedData._group_mappers gives us (offset, count, mapper) per
    # dataset group, so we iterate all of them for full coverage.
    raw_transfers = walking_raw["transfers"]
    raw_coords = walking_raw["coords"]

    # Build a combined ATCO→merged-int mapping from all groups.
    # A single ATCO code may appear in multiple datasets (e.g. bus_a
    # and bus_b both serve the same physical stop).  We map every
    # occurrence so walking transfers work regardless of which
    # day-half the router reached a stop through.
    from collections import defaultdict
    atco_to_merged_all = defaultdict(list)  # {atco: [merged_int, ...]}
    for g_offset, g_count, mapper in merged._group_mappers:
        if mapper is None:
            continue
        for local_i in range(g_count):
            try:
                code = mapper.get_code(local_i)
            except Exception:
                continue
            if code:
                atco_to_merged_all[code].append(g_offset + local_i)

    inter_table = {}
    for from_atco, dests in raw_transfers.items():
        from_ints = atco_to_merged_all.get(from_atco, [])
        for from_int in from_ints:
            inner = {}
            for to_atco, secs in dests.items():
                to_ints = atco_to_merged_all.get(to_atco, [])
                for to_int in to_ints:
                    inner[to_int] = secs
            if inner:
                inter_table[from_int] = inner

    stop_coords = {}
    for atco, (lat, lon) in raw_coords.items():
        for s_int in atco_to_merged_all.get(atco, []):
            stop_coords[s_int] = (lat, lon)

    walking = Walking(inter_table, stop_coords)

    print(f"  ✓ Network & Router ready for {date_str}")
    print(f"    Walking: {len(inter_table)} stops with transfers, "
          f"{len(stop_coords)} with coords")
    return merged, router, walking


def print_route(route_result, merged):
    """Pretty-print the route returned by RaptorRouter.route().

    Non-destructive: does not modify *route_result*.
    """
    if not route_result:
        print("\n  No route found.")
        return

    # Extract walk-leg metadata (use .get() to avoid mutating the dict)
    meta = route_result.get('_meta', {})
    start_walk = meta.get('start_walk_seconds', 0)
    end_walk   = meta.get('end_walk_seconds', 0)
    total_arrival = meta.get('total_arrival')
    start_point = meta.get('start_point', ())
    destination = meta.get('destination', ())

    # Build ordered leg list by walking the prev_stop chain.
    # Filter out the _meta key so we only iterate stop entries.
    route_data = {k: v for k, v in route_result.items() if k != '_meta'}
    legs = []
    # Find the destination (stop with no outgoing — not a prev_stop of anyone else)
    all_prevs = {info["prev_stop"] for info in route_data.values() if info["prev_stop"] is not None}
    destinations = [s for s in route_data if s not in all_prevs]
    if not destinations:
        destinations = list(route_data.keys())

    # Trace back from destination to origin
    stop = destinations[0]
    visited = set()
    while stop is not None and stop not in visited:
        visited.add(stop)
        legs.append((stop, route_data[stop]))
        stop = route_data[stop]["prev_stop"]
    legs.reverse()

    print(f"\n{'='*60}")
    print(f"  Route found — {len(legs)} stop(s)")
    print(f"{'='*60}")

    # ── Initial walk from user's start to first transit stop ─────
    if start_point and legs:
        first_arrival = legs[0][1]["arrival_time"]
        depart_time = first_arrival - start_walk
        print(f"\n  ◎ Start  ({start_point[0]:.5f}, {start_point[1]:.5f})")
        print(f"    Depart at {seconds_to_time(int(depart_time))}")
        walk_min = start_walk / 60
        print(f"    │  🚶 Walk {walk_min:.0f} min ({start_walk}s)")

    for i, (stop_int, info) in enumerate(legs):
        # Resolve stop name / code
        stop_label = merged.stop_metadata[stop_int] if stop_int < len(merged.stop_metadata) else f"stop#{stop_int}"
        arrival = seconds_to_time(int(info["arrival_time"])) if info["arrival_time"] != float("inf") else "--:--:--"
        transport = info.get("mode") if info.get("mode") else "origin"

        if i == 0:
            print(f"  ● {stop_label}")
            print(f"    Arrive at {arrival}")
        else:
            if transport == "walking":
                # Inter-stop walking transfer — compute duration from timestamps
                prev_stop_int, prev_info = legs[i - 1]
                walk_secs = info["arrival_time"] - prev_info["arrival_time"]
                walk_min = walk_secs / 60
                print(f"    │  🚶 Walk {walk_min:.0f} min ({int(walk_secs)}s)")
            else:
                # Build a journey description line
                jinfo = info.get("journey_info")
                line_name = jinfo.get("line_name", "") if jinfo else ""
                # Strip prefix to show only the actual line name (e.g., "1" instead of "PC0002407:417:1")
                if line_name and ":" in line_name:
                    line_name = line_name.split(":")[-1]
                j_origin = info.get("journey_origin", "")
                j_dest   = info.get("journey_destination", "")
                board_dep = info.get("board_departure")

                desc_parts = []
                if transport:
                    desc_parts.append(transport)
                if line_name:
                    desc_parts.append(f"line {line_name}")
                if j_origin and j_dest:
                    desc_parts.append(f"{j_origin} → {j_dest}")
                if board_dep is not None:
                    desc_parts.append(f"departs {seconds_to_time(int(board_dep))}")

                desc = " · ".join(desc_parts) if desc_parts else transport
                print(f"    │  {desc}")
            print(f"  ● {stop_label}")
            print(f"    Arrive at {arrival}")

    # ── Final walk from last transit stop to user's destination ──
    if destination and legs:
        walk_min = end_walk / 60
        print(f"    │  🚶 Walk {walk_min:.0f} min ({end_walk}s)")
        print(f"  ◎ Destination  ({destination[0]:.5f}, {destination[1]:.5f})")
        if total_arrival is not None:
            print(f"    Arrive at {seconds_to_time(int(total_arrival))}")

    print(f"\n{'='*60}")


def main():
    """Main entry point — takes start/end lat,lon pairs, date & time,
    then runs RAPTOR and prints the result."""
    base = initialize_base()
    loader      = base["loader"]
    walking_raw = base["walking_raw"]

    # Track the current date so we only rebuild when it changes
    current_date = None
    current_time_bucket = None  # "AM" or "PM"
    merged    = None
    router    = None
    walking   = None

    while True:
        print("\n" + "-" * 60)
        print("Route Planner  (type 'q' to quit)")
        print("-" * 60)

        start_input = input("Start  (lat,lon) : ").strip()
        if start_input.lower() == "q":
            break
        end_input = input("End    (lat,lon) : ").strip()
        if end_input.lower() == "q":
            break
        time_str = input("Departure time  (HH:MM:SS) : ").strip()
        date_str = input("Departure date  (YYYY-MM-DD) : ").strip()

        # Max transfers (default 5)
        transfers_input = input("Max transfers    (default 5) : ").strip()
        if transfers_input == "":
            max_transfers = 5
        else:
            try:
                max_transfers = int(transfers_input)
            except ValueError:
                print("✗ Invalid number. Using default (5).")
                max_transfers = 5

        # Transport mode
        mode_input = input("Mode  (bus/train/both, default both) : ").strip().lower()
        if mode_input in ("bus", "train", "both", ""):
            if mode_input == "" or mode_input == "both":
                allowed_modes = {"bus", "train"}
            else:
                allowed_modes = {mode_input}
        else:
            print("✗ Invalid mode. Using 'both'.")
            allowed_modes = {"bus", "train"}

        # Parse lat,lon
        try:
            start_lat, start_lon = (float(x) for x in start_input.split(","))
        except ValueError:
            print("✗ Invalid start format. Expected lat,lon (e.g. 54.046,-2.798)")
            continue
        try:
            end_lat, end_lon = (float(x) for x in end_input.split(","))
        except ValueError:
            print("✗ Invalid end format. Expected lat,lon (e.g. 54.046,-2.798)")
            continue

        # Parse departure time
        try:
            start_seconds = seconds_since_midnight(time_str)
        except Exception:
            print(f"✗ Invalid time format '{time_str}'. Expected HH:MM:SS.")
            continue

        # Build / rebuild the date-specific network if the date or time bucket changed
        time_bucket = "AM" if start_seconds < 43200 else "PM"
        if date_str != current_date or time_bucket != current_time_bucket:
            try:
                merged, router, walking = build_for_date(
                    loader, walking_raw, date_str, start_time=start_seconds)
                current_date = date_str
                current_time_bucket = time_bucket
            except Exception as e:
                print(f"\n✗ Failed to build network for {date_str}: {e}")
                continue

        start_loc = (start_lat, start_lon)
        end_loc   = (end_lat, end_lon)

        print(f"\nSearching route  ({start_lat},{start_lon}) → ({end_lat},{end_lon})")
        print(f"  Depart {time_str} on {date_str}  |  max {max_transfers} transfers  |  mode: {', '.join(sorted(allowed_modes))}")

        try:
            result = router.route(
                n_transfer_limit=max_transfers,
                walking=walking,
                start_time=start_seconds,
                start_point=start_loc,
                destination=end_loc,
                allowed_modes=allowed_modes,
            )
            print_route(result, merged)
        except Exception as e:
            print(f"\n✗ Routing failed: {e}")

    print("Bye!")


if __name__ == "__main__":
    main()
