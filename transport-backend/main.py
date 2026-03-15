"""Transport Backend — system initialisation and per-date network builder.

``initialize_base()``
    One-time startup: opens database connections for bus, walking and
    train stores, downloads timetable data if needed, and precomputes
    walking transfers.  All heavy work runs concurrently — startup
    blocks until every task is complete.

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
from atco_loader import AtcoLoader
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
    """One-time setup: database, downloads, walking precompute.

    All heavy work (bus download, stop-coord download, walking
    precompute, train init) runs concurrently.  This function blocks
    until everything is complete — "Base Initialization Complete!" is
    only printed once all data is ready.

    Returns a dict with 'loader', 'walking_raw', and 'walking_loader'.
    """
    print("=" * 60)
    print("Initializing Transport Backend System")
    print("=" * 60)

    print("\nStarting all tasks concurrently...")
    os.makedirs(CACHE_DIR, exist_ok=True)

    from concurrent.futures import ThreadPoolExecutor
    import urllib.request as _ur
    import urllib.error as _ue

    # Allow the OSRM endpoint to be overridden by env var so containers
    # can address an OSRM sidecar by name (e.g. http://osrm:5012).
    OSRM_URL = os.environ.get("OSRM_URL", "http://localhost:5012")

    # ── Task 1: Bus — per-dataset concurrent download + load ─────
    # Returns (loader, bus_data_changed: bool)
    # Each dataset URL is fetched in its own sub-thread; the task only
    # returns True if at least one dataset was newly downloaded.

    def _bus_task():
        loader = BusLoader(BUS_DB_PATH, walking_db_path=WALK_DB_PATH)
        loader.ensure_db()
        loader.create_schema()
        conn = loader._connect()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM bus_route_stops")
        _count = cur.fetchone()[0]
        conn.close()
        bus_changed = False
        if _count == 0:
            print("  [bus] DB empty — fetching dataset list...")
            datasets = loader._fetch_dataset_info()
            print(f"  [bus] Got {len(datasets)} download URLs — downloading concurrently...")
            from concurrent.futures import ThreadPoolExecutor, as_completed

            def _download_and_save(ds, retries=3, backoff=1.0):
                import time, traceback
                # Extract short tag from source URL (e.g. 'ARCT' from '.../ARCT')
                tag = ds['source_url'].rstrip('/').split('/')[-1]
                for attempt in range(1, retries + 1):
                    try:
                        loader.download_and_load(ds['download_url'], tag=tag)
                        conn2 = loader._connect()
                        cur2 = conn2.cursor()
                        cur2.execute(
                            "INSERT INTO bus_dataset_meta (source_url, download_url, modified) "
                            "VALUES (%s, %s, %s) ON CONFLICT (source_url) DO UPDATE SET "
                            "download_url = EXCLUDED.download_url, modified = EXCLUDED.modified",
                            (ds['source_url'], ds['download_url'], ds['modified']),
                        )
                        conn2.commit()
                        conn2.close()
                        return (ds, None)
                    except Exception as e:
                        if attempt < retries:
                            time.sleep(backoff * (2 ** (attempt - 1)))
                        else:
                            return (ds, (e, traceback.format_exc()))

            max_workers = min(4, max(1, len(datasets)))
            successes, failures = [], []
            with ThreadPoolExecutor(max_workers=max_workers) as dex:
                futs = {dex.submit(_download_and_save, ds): ds for ds in datasets}
                for fut in as_completed(futs):
                    ds, result = fut.result()
                    (successes if result is None else failures).append((ds, result))

            if successes:
                print(f"  [bus] ✓ Loaded {len(successes)}/{len(datasets)} dataset(s)")
                bus_changed = True
            if failures:
                print(f"  [bus] ⚠ Failed {len(failures)}/{len(datasets)} dataset(s):")
                for ds, (exc, _tb) in failures:
                    print(f"    - {ds.get('source_url')} → {exc}")
        else:
            print("  [bus] Data present — checking for updates...")
            bus_changed = loader.check_for_updates()
        print("  [bus] ✓ Ready")
        return loader, bus_changed

    # ── Task 2: Walking — download NaPTAN coords + precompute ────
    # Entirely independent of bus data: runs fully in parallel.
    # Returns (atco_loader, walking_loader, raw_coords)

    def _walking_task():
        al = AtcoLoader(WALK_DB_PATH)
        al.create_schema()

        # Detect whether stop_coords is empty before the download so we
        # know whether NaPTAN data itself changed.
        conn_pre = al._connect()
        cur_pre = conn_pre.cursor()
        cur_pre.execute("SELECT COUNT(*) FROM stop_coords")
        coords_before = cur_pre.fetchone()[0]
        conn_pre.close()

        al.download_stop_coords()   # upserts into stop_coords

        conn_post = al._connect()
        cur_post = conn_post.cursor()
        cur_post.execute("SELECT COUNT(*) FROM stop_coords")
        coords_after = cur_post.fetchone()[0]
        conn_post.close()

        coords_changed = coords_after != coords_before or coords_before == 0
        if coords_changed:
            print(f"  [walking] ✓ Stop coords updated ({coords_after} stops)")
        else:
            print(f"  [walking] ✓ Stop coords unchanged ({coords_after} stops)")

        wl = WalkingLoader(WALK_DB_PATH)
        wl.create_schema()

        raw_coords = al.get_all_stop_coords()

        # ── Precompute walking transfers in parallel with bus ─────
        # Walking is independent of bus timetable data — only NaPTAN
        # stop coordinates are needed, which are already loaded above.
        conn_tx = wl._connect()
        cur_tx  = conn_tx.cursor()
        cur_tx.execute("SELECT COUNT(*) FROM walking_transfers")
        transfers_count = cur_tx.fetchone()[0]
        conn_tx.close()

        needs_precompute = coords_changed or transfers_count == 0

        if needs_precompute:
            # ── Try loading from disk cache first ─────────────────
            if not coords_changed and transfers_count == 0:
                loaded = wl.load_from_cache()
                if loaded:
                    return al, wl, raw_coords

            reasons = []
            if coords_changed:       reasons.append("stop coords changed")
            if transfers_count == 0: reasons.append("walking_transfers empty")
            print(f"  [walking] Precomputing transfers ({', '.join(reasons)})...")

            if coords_changed:
                wl.clear_walking_transfers()

            # Probe OSRM
            osrm_ok = False
            try:
                probe_url = OSRM_URL.rstrip("/") + "/"
                _r = _ur.urlopen(probe_url, timeout=3)
                _r.close()
                osrm_ok = True
            except _ue.HTTPError:
                osrm_ok = True
            except _ue.URLError:
                osrm_ok = False

            if not osrm_ok:
                print(f"  [walking] ⚠  OSRM not reachable at {OSRM_URL} — using haversine fallback")

            if osrm_ok:
                wl.precompute_walking_transfers(raw_coords, osrm_base=OSRM_URL)
            else:
                wl.precompute_walking_transfers_fallback(raw_coords)

            # Save to disk cache for next startup
            wl.save_cache()
        else:
            print("  [walking] Transfers up-to-date — skipping precompute")

        return al, wl, raw_coords

    # ── Task 3: Train — schema init (lightweight) ─────────────────
    # Returns train_loader.  Train data is loaded on-demand per date
    # in build_for_date(), so nothing expensive happens here.

    def _train_task():
        tl = TrainLoader(TRAIN_DB_PATH)
        tl.ensure_db()
        tl.create_schema()
        print("  [train] ✓ Ready")
        return tl

    # ── Run all three concurrently ────────────────────────────────

    with ThreadPoolExecutor(max_workers=3) as ex:
        bus_fut   = ex.submit(_bus_task)
        walk_fut  = ex.submit(_walking_task)
        train_fut = ex.submit(_train_task)

        loader, bus_changed                    = bus_fut.result()
        atco_loader, walking_loader, raw_coords = walk_fut.result()
        train_loader                            = train_fut.result()

    raw_transfers = walking_loader.get_walking_transfers()
    print(f"  [walking] ✓ Ready  ({len(raw_transfers)} stops with transfers, "
          f"{len(raw_coords)} with coords)")

    walking_raw = {
        "transfers": raw_transfers,   # {atco: {atco: secs}}
        "coords":    raw_coords,      # {atco: (lat, lon)}
    }

    # ── Pre-build today's merged timetable ────────────────────────
    # This ensures the first API request doesn't have to wait.
    today_str = _date.today().isoformat()
    yesterday_str = (_date.today() - _timedelta(days=1)).isoformat()
    tomorrow_str = (_date.today() + _timedelta(days=1)).isoformat()
    print(f"\n  [merged] Pre-building timetable for {today_str}...")

    # Load all 3 days' data in parallel (shared between AM and PM)
    train_loader = TrainLoader(TRAIN_DB_PATH)
    # Try to use per-date pickled caches to avoid rebuilding BusData when the
    # underlying DB hasn't changed. Fall back to building and save the cache
    # for future runs. Train loaders keep their existing behaviour.
    def _maybe_cached_bus_load(date_s):
        try:
            cached = loader.load_cache_for_date(date_s)
            if cached is not None:
                return cached
        except Exception:
            pass
        bd = loader.load_busdata_for_date(date_s)
        try:
            loader.save_cache_for_date(date_s, bd)
        except Exception:
            pass
        return bd

    with ThreadPoolExecutor(max_workers=6) as ex:
        bus_yesterday_f = ex.submit(_maybe_cached_bus_load, yesterday_str)
        bus_today_f = ex.submit(_maybe_cached_bus_load, today_str)
        bus_tomorrow_f = ex.submit(_maybe_cached_bus_load, tomorrow_str)
        train_yesterday_f = ex.submit(train_loader.load_traindata_for_date, yesterday_str)
        train_today_f = ex.submit(train_loader.load_traindata_for_date, today_str)
        train_tomorrow_f = ex.submit(train_loader.load_traindata_for_date, tomorrow_str)

        bus_yesterday = bus_yesterday_f.result()
        bus_today = bus_today_f.result()
        bus_tomorrow = bus_tomorrow_f.result()
        train_yesterday = train_yesterday_f.result()
        train_today = train_today_f.result()
        train_tomorrow = train_tomorrow_f.result()

    # Build AM (yesterday + today) and PM (today + tomorrow) in parallel
    def _build_variant(day_a_data, day_b_data, offset_a, offset_b, label):
        bus_a, train_a = day_a_data
        bus_b, train_b = day_b_data
        datasets = [
            (bus_a, offset_a),
            (train_a, offset_a),
            (bus_b, offset_b),
            (train_b, offset_b),
        ]
        merged = MergedData(
            datasets,
            atco_loader=atco_loader,
            stop_name_fn=loader.get_stop_names_bulk,
        )
        router = RaptorRouter(merged)

        # Remap walking data
        from collections import defaultdict
        raw_transfers = walking_raw["transfers"]
        raw_coords = walking_raw["coords"]

        atco_to_merged_all = defaultdict(list)
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

        walking_obj = Walking(inter_table, stop_coords)
        print(f"    ✓ {label} ready")
        return merged, router, walking_obj

    with ThreadPoolExecutor(max_workers=2) as ex:
        am_fut = ex.submit(_build_variant,
            (bus_yesterday, train_yesterday), (bus_today, train_today),
            -86400, 0, "AM")
        pm_fut = ex.submit(_build_variant,
            (bus_today, train_today), (bus_tomorrow, train_tomorrow),
            0, 86400, "PM")

        merged_am, router_am, walking_am = am_fut.result()
        merged_pm, router_pm, walking_pm = pm_fut.result()

    prebuilt_cache = {
        (today_str, "PM"): (merged_pm, router_pm, walking_pm),
        (today_str, "AM"): (merged_am, router_am, walking_am),
    }
    print(f"  [merged] ✓ Today's timetable ready (AM + PM)")

    print("\n" + "=" * 60)
    print("Base Initialization Complete!")
    print("=" * 60)

    return {
        "loader": loader,
        "walking_raw": walking_raw,
        "walking_loader": walking_loader,
        "atco_loader": atco_loader,
        "prebuilt_cache": prebuilt_cache,
    }


def build_for_date(loader, walking_raw, date_str, mode="both",
                   start_time=None, atco_loader=None):
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
    atco_loader : AtcoLoader | None
        If provided, stop names are resolved from NaPTAN first.

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
        atco_loader=atco_loader,
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
    # Be defensive: some route_data values may be non-dict (older callers or
    # unexpected router output); ignore non-dict entries when computing
    # prev_stop chains.
    all_prevs = {info.get("prev_stop") for info in route_data.values() if isinstance(info, dict) and info.get("prev_stop") is not None}
    destinations = [s for s in route_data if s not in all_prevs]
    if not destinations:
        destinations = list(route_data.keys())

    # Trace back from destination to origin
    stop = destinations[0]
    visited = set()
    while stop is not None and stop not in visited:
        info = route_data.get(stop)
        if not isinstance(info, dict):
            # Can't trace further if the entry isn't the expected dict shape
            break
        visited.add(stop)
        legs.append((stop, info))
        stop = info.get("prev_stop")
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
    al          = base.get("atco_loader")

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

        # Max transfers (default 3)
        transfers_input = input("Max transfers    (default 3) : ").strip()
        if transfers_input == "":
            max_transfers = 3
        else:
            try:
                max_transfers = int(transfers_input)
            except ValueError:
                print("✗ Invalid number. Using default (3).")
                max_transfers = 3

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
                    loader, walking_raw, date_str, start_time=start_seconds,
                    atco_loader=al)
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
