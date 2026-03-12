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
        self.route_tracks = []

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
        route_tracks_local = self.route_tracks
        journey_mode_local = self._journey_mode
        stop_mode_local = self._stop_mode
        group_mappers_local = self._group_mappers

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

            # --- route_stops: remap stop ids ---
            for route in data.route_stops:
                route_stops_local.append([sid + stop_offset for sid in route])

            # --- route_journeys: remap journey ids ---
            for rj in data.route_journeys:
                route_journeys_local.append([jid + journey_offset for jid in rj])

            # --- journey_times: remap stop ids + shift times ---
            for jt in data.journey_times:
                journey_times_local.append([
                    (sid + stop_offset, atime + time_offset, dtime + time_offset)
                    for sid, atime, dtime in jt
                ])

            # --- stop_to_routes: remap route ids ---
            for routes_for_stop in data.stop_to_routes:
                stop_to_routes_local.append([rid + route_offset for rid in routes_for_stop])

            # --- journey_to_route: remap route ids ---
            for r in data.journey_to_route:
                journey_to_route_local.append(
                    (r + route_offset) if (r is not None and r >= 0) else -1
                )

            # --- metadata (copy as-is) ---
            route_metadata_local.extend(
                getattr(data, "route_metadata", []) or [None] * n_routes
            )
            journey_metadata_local.extend(
                getattr(data, "journey_metadata", []) or [None] * n_journeys
            )

            # --- route_tracks (copy as-is, no remapping needed) ---
            route_tracks_local.extend(
                getattr(data, "route_tracks", []) or [[] for _ in range(n_routes)]
            )

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

        # --- build stop_metadata via ATCO codes ----------------------
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

        # --- build flattened route->journey mapping for faster iteration ---
        # Many downstream algorithms iterate all journey ids for a route.
        # Creating a flat array with per-route offsets avoids nested list
        # overhead and can be faster in hot loops. This step is optional
        # for benchmarking (controlled by *build_flatten*).
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

        # --- precompute journey_stop_index ----------------------------
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

        # --- precompute route_stop_departures -------------------------
        # --- precompute route_stop_departures -------------------------
        from collections import defaultdict
        self.route_stop_departures = []
        rsd_append = self.route_stop_departures.append
        jsi_local = self.journey_stop_index
        jt_local = journey_times_local
        for _r_idx, journey_ids in enumerate(route_journeys_local):
            stop_map = defaultdict(list)
            for j_id in journey_ids:
                if j_id >= len(jt_local):
                    continue
                jt = jt_local[j_id]
                jsi = jsi_local[j_id]
                for sid, pos in jsi.items():
                    dep_time = jt[pos][2]
                    stop_map[sid].append((dep_time, j_id))
            # sort departure lists
            for s, lst in stop_map.items():
                lst.sort()
            rsd_append(dict(stop_map))

        # In-memory store for logged journeys (populated by router)
        # Keyed by a generated id (string) -> dict with journey details
        self.logged_journeys = {}

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


class _Empty:
    """Placeholder for a missing / None data object."""
    route_stops = []
    route_journeys = []
    journey_times = []
    stop_to_routes = []
    journey_to_route = []
    route_metadata = []
    journey_metadata = []
    route_tracks = []