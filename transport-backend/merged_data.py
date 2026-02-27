from bus_data import BusData
from train_data import TrainData
from modes import BUS, TRAIN

class MergedData:
    def __init__( self, bus_data: BusData, train_data: TrainData, stop_name_fn=None, time_offset=0 ):
        """
        Args:
            bus_data:      BusData instance (or None).
            train_data:    TrainData instance (or None).
            stop_name_fn:  callable(set_of_atco_codes) -> dict{code: name}.
                           Used to resolve human-readable stop names.
                           If None, raw ATCO codes are used as names.
            time_offset:   seconds to add to every arrival/departure time.
                           Use -86400 for yesterday, 0 for today, +86400
                           for tomorrow so all three days share one timeline.
        """
        self.time_offset = time_offset
        self.bus_data = bus_data if bus_data is not None else self._empty()
        self.train_data = train_data if train_data is not None else self._empty()
        # To merge two datasets that use local integer ids we must remap
        # train ids so they become global in the merged structure. We compute
        # offsets for routes, journeys and stops and apply them to the
        # train-side references before concatenating lists.
        bus_routes = self.bus_data.route_stops
        train_routes = self.train_data.route_stops

        bus_journeys = self.bus_data.route_journeys
        train_journeys = self.train_data.route_journeys

        bus_journey_times = self.bus_data.journey_times
        train_journey_times = self.train_data.journey_times

        bus_stop_to_routes = self.bus_data.stop_to_routes
        train_stop_to_routes = self.train_data.stop_to_routes

        bus_journey_to_route = self.bus_data.journey_to_route
        train_journey_to_route = self.train_data.journey_to_route

        route_offset = len(bus_routes)
        journey_offset = len(bus_journey_times)
        stop_offset = len(bus_stop_to_routes)

        # remap train route_stops: increment stop ids by stop_offset
        train_routes_remapped = [ [ stop_id + stop_offset for stop_id in route ] for route in train_routes ]

        # combined route stops
        self.route_stops = list(bus_routes) + train_routes_remapped

        # remap train route_journeys: increment journey ids by journey_offset
        train_route_journeys_remapped = [ [ jid + journey_offset for jid in route ] for route in train_journeys ]
        self.route_journeys = list(bus_journeys) + train_route_journeys_remapped

        # remap train journey_times: increment stop ids inside journeys by stop_offset
        train_journey_times_remapped = []
        for journey in train_journey_times:
            remapped = [ (stop_id + stop_offset, atime + time_offset, dtime + time_offset) for (stop_id, atime, dtime) in journey ]
            train_journey_times_remapped.append(remapped)

        # Apply time_offset to bus journey times as well
        if time_offset != 0:
            bus_journey_times_shifted = []
            for journey in bus_journey_times:
                shifted = [ (stop_id, atime + time_offset, dtime + time_offset) for (stop_id, atime, dtime) in journey ]
                bus_journey_times_shifted.append(shifted)
            self.journey_times = bus_journey_times_shifted + train_journey_times_remapped
        else:
            self.journey_times = list(bus_journey_times) + train_journey_times_remapped

        # remap train stop_to_routes: increment route ids by route_offset
        train_stop_to_routes_remapped = [ [ rid + route_offset for rid in lst ] for lst in train_stop_to_routes ]
        self.stop_to_routes = list(bus_stop_to_routes) + train_stop_to_routes_remapped

        # remap train journey_to_route: increment route id references by route_offset
        train_journey_to_route_remapped = [ (r + route_offset) if (r is not None and r >= 0) else -1 for r in train_journey_to_route ]
        self.journey_to_route = list(bus_journey_to_route) + train_journey_to_route_remapped
        # --- build stop metadata in merged space -------------------------
        # For each merged stop index, convert back to the external ATCO code
        # using the appropriate mapper (bus or train), then use the provided
        # stop_name_fn to resolve human-readable names.
        total_stops = len(self.stop_to_routes)

        # Step 1: collect all ATCO codes
        codes_by_index = {}
        for i in range(total_stops):
            code = None
            if i < stop_offset:
                try:
                    code = self.bus_data.map_stops.get_code(i)
                except Exception:
                    code = None
            else:
                train_idx = i - stop_offset
                try:
                    code = self.train_data.map_stops.get_code(train_idx)
                except Exception:
                    code = None
            codes_by_index[i] = code

        # Step 2: bulk lookup all codes at once
        all_codes = {c for c in codes_by_index.values() if c is not None}
        name_map = stop_name_fn(all_codes) if stop_name_fn and all_codes else {}

        # Step 3: build the metadata list
        self.stop_metadata = []
        for i in range(total_stops):
            code = codes_by_index[i]
            name = name_map.get(code) if code else None
            if name:
                self.stop_metadata.append(name)
            elif code is not None:
                self.stop_metadata.append(code)
            else:
                self.stop_metadata.append("")

        # --- build route metadata in merged space -------------------------
        # Bus routes keep their original indices; train routes are offset.
        bus_route_meta = getattr(self.bus_data, 'route_metadata', [])
        train_route_meta = getattr(self.train_data, 'route_metadata', [])
        self.route_metadata = list(bus_route_meta) + list(train_route_meta)

        # --- build journey metadata in merged space ----------------------
        bus_journey_meta = getattr(self.bus_data, 'journey_metadata', [])
        train_journey_meta = getattr(self.train_data, 'journey_metadata', [])
        self.journey_metadata = list(bus_journey_meta) + list(train_journey_meta)

        # --- precompute journey stop-position index for fast lookup ------
        # journey_stop_index[j] = { stop_int: position_in_journey }
        self.journey_stop_index = []
        for jt in self.journey_times:
            idx = {}
            for pos, (stop_id, _a, _d) in enumerate(jt):
                if stop_id not in idx:          # keep first occurrence
                    idx[stop_id] = pos
            self.journey_stop_index.append(idx)

        # --- precompute sorted departure time per (route, stop) ----------
        # route_stop_departures[route][stop] = sorted list of
        #   (departure_time_at_stop, journey_id)
        # Allows binary-search in first_journey.
        self.route_stop_departures = []
        for r_idx, journey_ids in enumerate(self.route_journeys):
            stop_map = {}                       # stop -> [(dep_time, journey_id)]
            for j_id in journey_ids:
                jt = self.journey_times[j_id]
                jsi = self.journey_stop_index[j_id]
                for stop_id, pos in jsi.items():  # only iterate unique stops
                    dep_time = jt[pos][2]         # departure_time
                    stop_map.setdefault(stop_id, []).append((dep_time, j_id))
            # sort each list by departure time
            for s in stop_map:
                stop_map[s].sort()
            self.route_stop_departures.append(stop_map)


    def _empty( self ):
        class Empty:
            route_stops = []
            route_journeys = []
            journey_times = []
            stop_to_routes = []
            journey_to_route = []
            stop_metadata = []
            route_metadata = []
            journey_metadata = []
            journey_stop_index = []
            route_stop_departures = []
        return Empty()

    def journey_type( self, journey_id_int: int ) -> int:
        """Return an integer code for the journey's transport type.

        BUS if the journey id belongs to the bus dataset, else TRAIN.
        """
        return BUS if journey_id_int < len(self.bus_data.journey_times) else TRAIN