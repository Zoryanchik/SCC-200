"""
Transport Backend - Main Entry Point
Initializes the transit routing system, takes user input for
start/end points and departure time, and prints the route result.
"""

from bus_loader import BusLoader
from timetable import Timetable
from walking import Walking
from time_utils import seconds_since_midnight, seconds_to_time
import os
from datetime import date as _date, timedelta as _timedelta

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
DB_PATH   = os.path.join(CACHE_DIR, "bus_timetable.db")


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
    print("\n[1/2] Loading bus timetable data...")
    os.makedirs(CACHE_DIR, exist_ok=True)
    loader = BusLoader(DB_PATH)
    loader.ensure_db()
    loader.create_schema()

    import sqlite3 as _sql
    _conn = _sql.connect(DB_PATH)
    _count = _conn.execute("SELECT COUNT(*) FROM route_stops").fetchone()[0]
    _conn.close()
    data_changed = False
    if _count == 0:
        print("  Database empty — downloading timetable data...")
        datasets = loader._fetch_dataset_info()
        print(f"  Got {len(datasets)} download URLs")
        for ds in datasets:
            loader.download_and_load(ds['download_url'])
            conn = _sql.connect(DB_PATH)
            conn.execute(
                "INSERT OR REPLACE INTO dataset_meta (source_url, download_url, modified) VALUES (?,?,?)",
                (ds['source_url'], ds['download_url'], ds['modified']),
            )
            conn.commit()
            conn.close()
        print("  ✓ Timetable data loaded")
        data_changed = True
    else:
        data_changed = loader.check_for_updates()

    print("  ✓ Database ready")

    # 2. Walking — coords, precomputed transfers, OSRM ----------------
    print("\n[2/2] Preparing walking data...")

    import urllib.request as _ur
    osrm_ok = False
    # Allow the OSRM endpoint to be overridden by env var so containers can
    # address an OSRM sidecar by name (e.g. http://osrm:5000) or use host
    # networking. Default remains the historical localhost:5321 for host runs.
    OSRM_URL = os.environ.get("OSRM_URL", "http://localhost:5321")
    try:
        probe_url = OSRM_URL.rstrip("/") + "/nearest/v1/foot/0,0"
        _r = _ur.urlopen(probe_url, timeout=3)
        _r.close()
        osrm_ok = True
    except Exception:
        pass

    if not osrm_ok:
        print(f"  ⚠  OSRM not reachable at {OSRM_URL}")
        print("     Walking transfers will be unavailable.")
        print("     To enable, run an OSRM server and ensure the backend can reach it.")
        print("     Examples:")
        print("       # Run OSRM on the host (backend running on host will reach it):")
        print("       docker run -d -p 5321:5000 -v /path/to/data:/data \\")
        print("         osrm/osrm-backend osrm-routed --algorithm mld /data/nw-england.osrm")
        print("       # Run OSRM as a separate container and point backend to it:")
        print("       docker network create scc-net || true")
        print("       docker run -d --name osrm --network scc-net osrm/osrm-backend \\")
        print("         osrm-routed --algorithm mld /data/nw-england.osrm")
        print("       # Then run the backend on the same network and set OSRM_URL=http://osrm:5000")

    if data_changed:
        loader.clear_walking_transfers()
    loader.download_stop_coords()
    if osrm_ok:
        loader.precompute_walking_transfers(osrm_base=OSRM_URL)

    # Build inter_walk table keyed by ATCO codes (will be remapped
    # to per-date integer IDs when the network is built)
    raw_transfers = loader.get_walking_transfers()
    raw_coords    = loader.get_all_stop_coords()

    walking_raw = {
        "transfers": raw_transfers,   # {atco: {atco: secs}}
        "coords":    raw_coords,      # {atco: (lat, lon)}
        "osrm_url":  OSRM_URL,        # propagated to Walking()
    }

    print(f"  ✓ Walking ready  ({len(raw_transfers)} stops with transfers, "
          f"{len(raw_coords)} with coords)")

    print("\n" + "=" * 60)
    print("Base Initialization Complete!")
    print("=" * 60)

    return {
        "loader": loader,
        "walking_raw": walking_raw,
    }


def build_for_date(loader, walking_raw, date_str, mode="both"):
    """Build date-specific Timetable + Router + Walking.

    Loads three date-filtered BusData objects (yesterday, today,
    tomorrow) so that only journeys operating on each respective day
    are included in the RAPTOR search.

    Walking data is remapped from ATCO codes to the *today* BusData's
    stop integers so the router's walking lookups stay consistent.

    Returns (timetable, router, walking).
    """
    query = _date.fromisoformat(date_str)
    yesterday_str = (query - _timedelta(days=1)).isoformat()
    tomorrow_str  = (query + _timedelta(days=1)).isoformat()

    print(f"\n  Building network for {date_str} …")
    print(f"    Loading yesterday ({yesterday_str}) …", end=" ", flush=True)
    bus_yesterday = loader.load_busdata_for_date(yesterday_str)
    print(f"{len(bus_yesterday.map_journeys)} journeys")

    print(f"    Loading today     ({date_str}) …", end=" ", flush=True)
    bus_today = loader.load_busdata_for_date(date_str)
    print(f"{len(bus_today.map_journeys)} journeys")

    print(f"    Loading tomorrow  ({tomorrow_str}) …", end=" ", flush=True)
    bus_tomorrow = loader.load_busdata_for_date(tomorrow_str)
    print(f"{len(bus_tomorrow.map_journeys)} journeys")

    train_data = None  # placeholder

    timetable = Timetable(
        bus_yesterday, train_data,
        bus_today,     train_data,
        bus_tomorrow,  train_data,
        stop_name_fn=loader.get_stop_names_bulk,
    )
    timetable.build_network(mode)

    # Remap walking data to today's BusData stop integers.
    # MergedData uses bus stop ints directly (train ints are offset),
    # so bus_today.map_stops gives us the correct mapping.
    stop_map = bus_today.map_stops.code_to_int

    raw_transfers = walking_raw["transfers"]
    inter_table = {}
    for from_atco, dests in raw_transfers.items():
        from_int = stop_map.get(from_atco)
        if from_int is None:
            continue
        inner = {}
        for to_atco, secs in dests.items():
            to_int = stop_map.get(to_atco)
            if to_int is not None:
                inner[to_int] = secs
        if inner:
            inter_table[from_int] = inner

    raw_coords = walking_raw["coords"]
    stop_coords = {}
    for atco, (lat, lon) in raw_coords.items():
        s_int = stop_map.get(atco)
        if s_int is not None:
            stop_coords[s_int] = (lat, lon)

    osrm_url = walking_raw.get("osrm_url")
    walking = Walking(inter_table, stop_coords, osrm_base=osrm_url)

    print(f"  ✓ Timetable & Router ready for {date_str}")
    print(f"    Walking: {len(inter_table)} stops with transfers, "
          f"{len(stop_coords)} with coords")
    return timetable, timetable.raptor_router, walking


def print_route(route_result, merged):
    """Pretty-print the route returned by RaptorRouter.route()."""
    if not route_result:
        print("\n  No route found.")
        return

    # Extract walk-leg metadata
    meta = route_result.pop('_meta', {})
    start_walk = meta.get('start_walk_seconds', 0)
    end_walk   = meta.get('end_walk_seconds', 0)
    total_arrival = meta.get('total_arrival')
    start_point = meta.get('start_point', ())
    destination = meta.get('destination', ())

    # Build ordered leg list by walking the prev_stop chain
    legs = []
    # Find the destination (stop with no outgoing – i.e. not a prev_stop of anyone else)
    all_prevs = {info["prev_stop"] for info in route_result.values() if info["prev_stop"] is not None}
    destinations = [s for s in route_result if s not in all_prevs]
    if not destinations:
        destinations = list(route_result.keys())

    # Trace back from destination to origin
    stop = destinations[0]
    visited = set()
    while stop is not None and stop not in visited:
        visited.add(stop)
        legs.append((stop, route_result[stop]))
        stop = route_result[stop]["prev_stop"]
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
        transport = info["type"] if info["type"] else "origin"

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
    timetable = None
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

        # Build / rebuild the date-specific network if the date changed
        if date_str != current_date:
            try:
                timetable, router, walking = build_for_date(loader, walking_raw, date_str)
                current_date = date_str
            except Exception as e:
                print(f"\n✗ Failed to build network for {date_str}: {e}")
                continue

        merged = timetable.today  # MergedData for today (for display)

        start_loc = (start_lat, start_lon)
        end_loc   = (end_lat, end_lon)

        print(f"\nSearching route  ({start_lat},{start_lon}) → ({end_lat},{end_lon})")
        print(f"  Depart {time_str} on {date_str}  |  max {max_transfers} transfers  |  mode: {', '.join(sorted(allowed_modes))}")

        try:
            result = router.route(
                n_transfer_limit=max_transfers,
                walking=walking,
                start_date=date_str,
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
