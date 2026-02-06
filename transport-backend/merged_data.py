from bus_data import BusData
from train_data import TrainData
from atco import Atco

class MergedData:
    def __init__( self, bus_data: BusData, train_data: TrainData ):
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
            remapped = [ (stop_id + stop_offset, atime) for (stop_id, atime) in journey ]
            train_journey_times_remapped.append(remapped)
        self.journey_times = list(bus_journey_times) + train_journey_times_remapped

        # remap train stop_to_routes: increment route ids by route_offset
        train_stop_to_routes_remapped = [ [ rid + route_offset for rid in lst ] for lst in train_stop_to_routes ]
        self.stop_to_routes = list(bus_stop_to_routes) + train_stop_to_routes_remapped

        # remap train journey_to_route: increment route id references by route_offset
        train_journey_to_route_remapped = [ (r + route_offset) if (r is not None and r >= 0) else -1 for r in train_journey_to_route ]
        self.journey_to_route = list(bus_journey_to_route) + train_journey_to_route_remapped
        # --- build stop metadata in merged space -------------------------
        # For each merged stop index, convert back to the external ATCO code
        # using the appropriate mapper (bus or train) and query the Atco
        # database for the stop name. Store a small metadata dict indexed by
        # the merged stop int.
        atco = Atco()
        total_stops = len(self.stop_to_routes)
        self.stop_metadata = []
        for i in range(total_stops):
            code = None
            if i < stop_offset:
                # bus-side: internal id maps directly to bus mapper index
                try:
                    code = self.bus_data.map_stops.get_code(i)
                except Exception:
                    code = None
            else:
                # train-side: compute train-local index
                train_idx = i - stop_offset
                try:
                    code = self.train_data.map_stops.get_code(train_idx)
                except Exception:
                    code = None

            name = None
            if code is not None:
                stop_row = atco.get_stop(code)
                if stop_row:
                    name = stop_row.get('name') if isinstance(stop_row, dict) else None

            # store only strings: name if available, else code if present, else empty string
            if name:
                entry = name
            elif code is not None:
                entry = code
            else:
                entry = ""

            self.stop_metadata.append(entry)

        # to be implemented
        self.route_metadata = []
        self.journey_metadata = []


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
        return Empty()

    def journey_type( self, journey_id_int: int ) -> str:
        return "bus" if journey_id_int < len( self.bus_data.journey_times ) else "train"