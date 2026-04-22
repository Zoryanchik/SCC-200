"""MergedData — merge one or more Data objects into a single searchable network.

Each input is a ``(data, time_offset)`` pair.  Stop, route, and journey
indices from later entries are remapped so the merged structure uses a
single contiguous index space.  Journey times are shifted by the
corresponding ``time_offset`` (e.g. ``-86400`` for yesterday, ``0`` for
today, ``+86400`` for tomorrow).

The mode of each journey (BUS or TRAIN) is inferred from the concrete
type of the Data object that contributed it (BusData → BUS, TrainData →
TRAIN).

An optional ``atco_loader`` (``AtcoLoader``) can be supplied; it is used
to resolve stop names and coordinates via ATCO codes looked up through
each Data object's ``map_stops`` mapper.
"""

from modes import BUS, TRAIN

import os
import time
import threading


def _shift_journey_times_row_inplace(jt_row, stop_offset: int, time_offset: int):
    """Fast path for shifting a single journey time row.

    Keeps the external representation identical (list of (sid, atime, dtime)),
    but avoids some Python overhead in the tight merge loop by using locals.
    """
    out = [None] * len(jt_row)
    so = int(stop_offset)
    to = int(time_offset)
    for i, (sid, atime, dtime) in enumerate(jt_row):
        out[i] = (sid + so, atime + to, dtime + to)
    return out


def _shift_journey_times_row(args):
    """Worker for MERGE_PARALLEL_SHIFT.

    Must be module-level so it can be pickled by multiprocessing.
    """
    jt_row, stop_offset, time_offset = args
    return [(sid + stop_offset, atime + time_offset, dtime + time_offset) for (sid, atime, dtime) in jt_row]


class MergedData:
    """Merge one or more Data objects into a flat, searchable structure.

    Parameters
    ----------
    datasets : list[tuple[Data, int]]
        Each element is ``(data_object, time_offset_seconds)``.
        Data objects must expose: ``route_stops``, ``route_journeys``,
        ``journey_times``, ``stop_to_routes``, ``journey_to_route``,
        ``route_metadata``, ``journey_metadata``, ``map_stops``.
    atco_loader : AtcoLoader | None
        If provided, stop names are resolved via
        ``atco_loader.get_stop_names_bulk()``.
    stop_name_fn : callable | None
        Legacy callback ``(set_of_codes) -> {code: name}``.  Used only
        when *atco_loader* is ``None``.
    """

    def __init__(self, datasets, atco_loader=None, stop_name_fn=None, build_flatten=True):
        timing = os.getenv('MERGE_BUILD_TIMING', '0') == '1'
        t0_all = time.perf_counter() if timing else None

        def _ts():
            return time.perf_counter()

        def _log(label, dt):
            # Keep it simple (stdout) so it works both in scripts and server logs.
            print(f"[merge_timing] {label}: {dt:.3f}s")

        # Keep a reference for callers that need coord/name lookups later
        self.atco = atco_loader

        # Accumulators
        self.route_stops = []
        self.route_journeys = []
        self.journey_times = []
        self.stop_to_routes = []
        self.journey_to_route = []
        self.route_metadata = []
        self.journey_metadata = []
        self.route_link_tracks = []

        # Per-journey mode (BUS or TRAIN), filled during merge
        self._journey_mode = []

        # Per-stop mode (BUS or TRAIN), parallel to merged stop indices.
        # Filled during merge so callers can inspect which source a stop
        # originated from (mirrors journey mode behavior).
        self._stop_mode = []

        route_offset = 0
        journey_offset = 0
        stop_offset = 0

        # Store mappers per group for stop-code resolution later
        self._group_mappers = []   # [(stop_offset, stop_count, map_stops), ...]

        # Local aliases to avoid repeated attribute lookups in hot loops
        route_stops_local = self.route_stops
        route_journeys_local = self.route_journeys
        journey_times_local = self.journey_times
        stop_to_routes_local = self.stop_to_routes
        journey_to_route_local = self.journey_to_route
        route_metadata_local = self.route_metadata
        journey_metadata_local = self.journey_metadata
        route_link_tracks_local = self.route_link_tracks
        journey_mode_local = self._journey_mode
        stop_mode_local = self._stop_mode
        group_mappers_local = self._group_mappers

        # Optional: parallelize the most allocation-heavy step (journey time shifting)
        # using a process pool. This is guarded because it can increase memory use
        # (data pickling) and is only beneficial for very large datasets.
        parallel_shift = os.getenv('MERGE_PARALLEL_SHIFT', '0') == '1'

        t0_phase = _ts() if timing else None

        for data, time_offset in datasets:
            if data is None:
                data = _Empty()

            # Determine the transport mode from the concrete data type.
            # Import here to avoid circular imports at module level.
            from train_data import TrainData
            mode = TRAIN if isinstance(data, TrainData) else BUS

            n_routes = len(data.route_stops)
            n_journeys = len(data.journey_times)
            n_stops = len(data.stop_to_routes)

            # --- route_stops: remap stop ids (avoid intermediate list objects where possible) ---
            for route in data.route_stops:
                # list comprehension is faster than a Python-level loop for simple numeric remapping
                route_stops_local.append([r + stop_offset for r in route])

            # --- route_journeys: remap journey ids ---
            for rj in data.route_journeys:
                route_journeys_local.append([jid + journey_offset for jid in rj])

            # --- journey_times: remap stop ids + shift times ---
            # This can dominate runtime due to tuple allocations.
            if not parallel_shift:
                # Local aliases for speed in the tight loop.
                jt_append = journey_times_local.append
                so = int(stop_offset)
                to = int(time_offset)
                for jt in data.journey_times:
                    # Use a list comprehension (C-optimized) to remap and shift triplets.
                    jt_append([(sid + so, atime + to, dtime + to) for (sid, atime, dtime) in jt])
            else:
                # Parallel path: shift each journey in workers.
                # Note: this pickles each journey list; enable only when beneficial.
                from concurrent.futures import ProcessPoolExecutor

                with ProcessPoolExecutor(max_workers=os.cpu_count() or 2) as ex:
                    it = ((jt_row, stop_offset, time_offset) for jt_row in data.journey_times)
                    for out in ex.map(_shift_journey_times_row, it, chunksize=256):
                        journey_times_local.append(out)

            # --- stop_to_routes: remap route ids ---
            for routes_for_stop in data.stop_to_routes:
                stop_to_routes_local.append([r + route_offset for r in routes_for_stop])

            # --- journey_to_route: remap route ids ---
            for r in data.journey_to_route:
                journey_to_route_local.append((r + route_offset) if (r is not None and r >= 0) else -1)

            # --- metadata (copy as-is) ---
            route_metadata_local.extend(
                getattr(data, "route_metadata", []) or [None] * n_routes
            )
            journey_metadata_local.extend(
                getattr(data, "journey_metadata", []) or [None] * n_journeys
            )

            # --- route_link_tracks (remap stop ints + routes) ---
            # Per route: dict[(from_stop_int,to_stop_int)] -> [(lat,lon),...]
            # Stop ints must be offset into merged stop index space.
            group_links = getattr(data, 'route_link_tracks', None) or [{} for _ in range(n_routes)]
            for r_links in group_links:
                if not r_links:
                    route_link_tracks_local.append({})
                    continue
                out = {}
                for (fs, ts), pts in r_links.items():
                    # Important: we only need to remap stop ids on the key.
                    # The point list itself is immutable for our purposes here.
                    # Avoid copying potentially-large `pts` lists during merge.
                    out[(fs + stop_offset, ts + stop_offset)] = pts
                route_link_tracks_local.append(out)

            # Record the transport mode for every journey in this group
            journey_mode_local.extend([mode] * n_journeys)

            # Record the transport mode for each stop contributed by this
            # data group so the merged stop index space has an associated
            # mode value per stop (exactly like journey mode above).
            stop_mode_local.extend([mode] * n_stops)

            # Mapper bookkeeping
            mapper = getattr(data, "map_stops", None)
            group_mappers_local.append((stop_offset, n_stops, mapper))

            # Advance offsets
            route_offset += n_routes
            journey_offset += n_journeys
            stop_offset += n_stops

        if timing:
            _log('phase_merge_remap_and_shift', _ts() - t0_phase)

        # --- build stop_metadata via ATCO codes ----------------------
        t0_phase = _ts() if timing else None
        total_stops = len(self.stop_to_routes)
        codes_by_index = {}
        for g_offset, g_count, mapper in self._group_mappers:
            if mapper is None:
                continue
            for local_i in range(g_count):
                global_i = g_offset + local_i
                try:
                    codes_by_index[global_i] = mapper.get_code(local_i)
                except Exception:
                    pass

        all_codes = {c for c in codes_by_index.values() if c}

        # Resolve stop names: prefer AtcoLoader (NaPTAN) first, then
        # fall back to bus-derived stop names for any codes the ATCO
        # source doesn't cover.
        name_map = {}
        if atco_loader and all_codes:
            name_map = atco_loader.get_stop_names_bulk(all_codes)
        # Fill gaps with bus stop names (stop_name_fn)
        if stop_name_fn and all_codes:
            missing_codes = all_codes - set(name_map)
            if missing_codes:
                bus_names = stop_name_fn(missing_codes)
                name_map.update(bus_names)

        self.stop_metadata = []
        for i in range(total_stops):
            code = codes_by_index.get(i)
            name = name_map.get(code) if code else None
            self.stop_metadata.append(name or code or "")

        if timing:
            _log('phase_stop_metadata', _ts() - t0_phase)

        # --- build flattened route->journey mapping for faster iteration ---
        # Many downstream algorithms iterate all journey ids for a route.
        # Creating a flat array with per-route offsets avoids nested list
        # overhead and can be faster in hot loops. This step is optional
        # for benchmarking (controlled by *build_flatten*).
        t0_phase = _ts() if timing else None

        if build_flatten:
            flat = []
            offsets = [0]
            for r in route_journeys_local:
                flat.extend(r)
                offsets.append(len(flat))
            self._flat_route_journeys = flat
            self._route_journey_offsets = offsets
        else:
            # Provide empty structures when disabled so callers can still
            # access attributes without conditional checks.
            self._flat_route_journeys = []
            self._route_journey_offsets = [0]

        if timing:
            _log('phase_flatten_route_journeys', _ts() - t0_phase)

        # --- precompute journey_stop_index ----------------------------
        t0_phase = _ts() if timing else None
        self.journey_stop_index = []
        jsi_append = self.journey_stop_index.append
        for jt in journey_times_local:
            idx = {}
            # local reference for speed
            idx_set = idx.__setitem__
            for pos, triplet in enumerate(jt):
                sid = triplet[0]
                if sid not in idx:
                    idx_set(sid, pos)
            jsi_append(idx)

        if timing:
            _log('phase_journey_stop_index', _ts() - t0_phase)

        # --- route_stop_departures (lazy by default) -------------------
        # This used to be fully precomputed during merge, but profiling
        # shows it dominates merge time. Make it lazy by default and
        # build per-route on first use.
        self._route_stop_departures_cache = [None] * len(route_journeys_local)
        self._route_stop_departures_lock = threading.Lock()

        eager_rsd = os.getenv('EAGER_ROUTE_STOP_DEPARTURES', '0') == '1'
        if eager_rsd:
            t0_phase = _ts() if timing else None
            for rid in range(len(route_journeys_local)):
                self._route_stop_departures_cache[rid] = self._build_route_stop_departures(rid)
            if timing:
                _log('phase_route_stop_departures', _ts() - t0_phase)
        elif timing:
            _log('phase_route_stop_departures', 0.0)

        if timing:
            _log('total', _ts() - t0_all)

        # In-memory store for logged journeys (populated by router)
        # Keyed by a generated id (string) -> dict with journey details
        self.logged_journeys = {}

        # --- route_link_tracks (lazy per-route builder) ----------------
        # Building per-link fragments for every route during day load is
        # expensive. Many requests never need fragments (they display the
        # full route polyline), so keep link fragments lazy and build them
        # only when geometry slicing needs them.
        #
        # Contract:
        # - `get_route_link_tracks(route_int)` returns a dict mapping
        #   (from_stop_int, to_stop_int) -> list[(lat, lon)].
    # - If fragments already exist (loaded eagerly), it returns them.
    # - If not, it attempts to fetch DB-backed fragments via an attached
    #   BusLoader (merged.bus_loader).
        self._route_link_tracks_lock = threading.Lock()

    def _build_route_stop_departures(self, route_id_int: int):
        """Build and return {stop_int: [(dep_time, journey_id), ...sorted...]} for a route."""
        from collections import defaultdict

        if route_id_int < 0 or route_id_int >= len(self.route_journeys):
            return {}

        journey_ids = self.route_journeys[route_id_int]
        stop_map = defaultdict(list)
        jt_local = self.journey_times
        jsi_local = self.journey_stop_index
        jt_len = len(jt_local)

        for j_id in journey_ids:
            if j_id < 0 or j_id >= jt_len:
                continue
            jt = jt_local[j_id]
            jsi = jsi_local[j_id]
            jsi_get = jsi.get
            for sid in jsi:
                pos = jsi_get(sid)
                dep_time = jt[pos][2]
                stop_map[sid].append((dep_time, j_id))

        for lst in stop_map.values():
            lst.sort()

        return dict(stop_map)

    def get_route_stop_departures(self, route_id_int: int):
        """Lazy accessor for route_stop_departures for a given route."""
        if route_id_int < 0 or route_id_int >= len(self._route_stop_departures_cache):
            return {}
        cached = self._route_stop_departures_cache[route_id_int]
        if cached is not None:
            return cached

        # Double-checked locking: route builds are independent and safe.
        with self._route_stop_departures_lock:
            cached2 = self._route_stop_departures_cache[route_id_int]
            if cached2 is not None:
                return cached2
            built = self._build_route_stop_departures(route_id_int)
            self._route_stop_departures_cache[route_id_int] = built
            return built

    @property
    def route_stop_departures(self):
        """Backwards-compatible view.

        Note: iterating this property forces a full build of all routes.
        Prefer `get_route_stop_departures(route_id)` in hot code.
        """
        # Materialize all routes on demand.
        for rid in range(len(self._route_stop_departures_cache)):
            if self._route_stop_departures_cache[rid] is None:
                self._route_stop_departures_cache[rid] = self._build_route_stop_departures(rid)
        return self._route_stop_departures_cache

    # ── Helpers ───────────────────────────────────────────────────

    def journey_type(self, journey_id_int: int) -> int:
        """Return the transport mode (BUS or TRAIN) for a merged journey."""
        if journey_id_int < len(self._journey_mode):
            return self._journey_mode[journey_id_int]
        return BUS  # fallback

    def stop_type(self, merged_stop_int: int) -> int:
        """Return the transport mode (BUS or TRAIN) for a merged stop.

        Mirrors :meth:`journey_type` and returns ``BUS`` when the index
        is out of range.
        """
        if merged_stop_int < len(self._stop_mode):
            return self._stop_mode[merged_stop_int]
        return BUS  # fallback

    def get_atco_code(self, merged_stop_int: int):
        """Return the ATCO code for a merged stop index, or None."""
        for g_offset, g_count, mapper in self._group_mappers:
            if g_offset <= merged_stop_int < g_offset + g_count:
                local = merged_stop_int - g_offset
                try:
                    return mapper.get_code(local)
                except Exception:
                    return None
        return None

    # ── Route link track fragments ──────────────────────────────────

    def get_route_link_tracks(self, route_id_int: int):
        """Lazy accessor for per-link route fragments for a given route."""
        if route_id_int < 0 or route_id_int >= len(self.route_link_tracks):
            return {}

        cached = self.route_link_tracks[route_id_int]
        # The merged structure initializes per-route slots to {}.
        # An empty dict means "not built yet", not "built and empty".
        if isinstance(cached, dict) and len(cached) > 0:
            return cached

        with self._route_link_tracks_lock:
            cached2 = self.route_link_tracks[route_id_int]
            if isinstance(cached2, dict) and len(cached2) > 0:
                return cached2

            # Preferred lazy path (ATCO-driven): if a BusLoader is attached,
            # fetch section tracks for this single route_id from the DB and
            # map them onto (from_stop_int,to_stop_int) keys.
            built = None
            try:
                loader = getattr(self, 'bus_loader', None)
                meta = self.route_metadata[route_id_int] if route_id_int < len(self.route_metadata) else None
                rid = meta.get('route_id') if isinstance(meta, dict) else None
                trace = os.environ.get('ROUTE_GEOM_TRACE') == '1'
                if trace:
                    try:
                        print('[link_tracks] build_start', {
                            'route_int': int(route_id_int),
                            'route_id': rid,
                            'has_bus_loader': bool(loader),
                            'cached_empty': True,
                        })
                    except Exception:
                        pass
                if loader and rid:
                    raw = loader.get_route_link_tracks_for_route(rid)
                    if trace:
                        try:
                            print('[link_tracks] db_fetch', {
                                'route_int': int(route_id_int),
                                'route_id': rid,
                                'raw_is_none': raw is None,
                                'raw_len': (len(raw) if raw else 0),
                            })
                        except Exception:
                            pass
                    if raw:
                        # Build ATCO->stop_int map within this route.
                        route_stops = self.route_stops[route_id_int] if route_id_int < len(self.route_stops) else []
                        atco_to_stop = {}
                        for s in route_stops:
                            try:
                                c = self.get_atco_code(s)
                            except Exception:
                                c = None
                            if c:
                                atco_to_stop.setdefault(c, s)

                        out = {}
                        for (fa, ta), pts in raw.items():
                            fs = atco_to_stop.get(fa)
                            ts = atco_to_stop.get(ta)
                            if fs is None or ts is None:
                                continue
                            if pts and len(pts) >= 2:
                                out[(fs, ts)] = list(pts)
                        built = out
            except Exception:
                built = None

            # Policy: never derive fragments from full-route polylines. If DB-backed
            # fragments aren't available, keep it empty and let callers decide the
            # next-best geometry source explicitly.
            if built is None:
                built = {}
            if os.environ.get('ROUTE_GEOM_TRACE') == '1':
                try:
                    print('[link_tracks] built', {
                        'route_int': int(route_id_int),
                        'built_len': (len(built) if built else 0),
                    })
                except Exception:
                    pass
            # Store result. Note that an empty dict means "built but no fragments".
            self.route_link_tracks[route_id_int] = built
            return built


class _Empty:
    """Placeholder for a missing / None data object."""
    route_stops = []
    route_journeys = []
    journey_times = []
    stop_to_routes = []
    journey_to_route = []
    route_metadata = []
    journey_metadata = []
    # full-route polylines intentionally removed