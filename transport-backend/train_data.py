from dense_mapper import DenseMapper


class TrainData:
    def __init__( self, num_routes:int, num_journeys:int, num_stops:int ):
        #indexed by route_id_int, store ordered lists of atco_code_ints
        self.route_stops = [ [] for _ in range( num_routes ) ]
        #indexed by route_id_int, store lists of journey_id_ints
        self.route_journeys = [ [] for _ in range( num_routes ) ]
        #indexed by journey_id_int, store lists of ( atco_code_int, arrival_time )
        self.journey_times = [ [] for _ in range( num_journeys ) ]
        #indexed by atco_code_int, store lists of route_id_int passing by each atco_code_int
        self.stop_to_routes = [ [] for _ in range( num_stops ) ]
        #indexed by journey_id_int, store route_id_ints
        self.journey_to_route = [ -1 for _ in range( num_journeys ) ]
        #dense mappers for stops, routes, journeys
        self.map_stops = DenseMapper()
        self.map_routes = DenseMapper()
        self.map_journeys = DenseMapper()
    # metadata is maintained only in MergedData

    # --- auto-resize helpers -----------------------------------------------
    def _ensure_route_capacity(self, route_id_int):
        while route_id_int >= len(self.route_stops):
            self.route_stops.append([])
            self.route_journeys.append([])

    def _ensure_journey_capacity(self, journey_id_int):
        while journey_id_int >= len(self.journey_times):
            self.journey_times.append([])
            self.journey_to_route.append(-1)

    def _ensure_stop_capacity(self, atco_code_int):
        while atco_code_int >= len(self.stop_to_routes):
            self.stop_to_routes.append([])

    def add_stop( self, atco_code, route_id ):
        atco_code_int = self.map_stops.get_int(atco_code)
        route_id_int = self.map_routes.get_int(route_id)
        # auto-resize to accept these ids
        self._ensure_route_capacity(route_id_int)
        self._ensure_stop_capacity(atco_code_int)
        # append to route stops if not already present (preserve order)
        if atco_code_int not in self.route_stops[route_id_int]:
            self.route_stops[route_id_int].append(atco_code_int)
        # update stop -> routes mapping
        if route_id_int not in self.stop_to_routes[atco_code_int]:
            self.stop_to_routes[atco_code_int].append(route_id_int)

    def add_route( self, route_id, atco_codes, journey_id ):
        atco_code_ints = [ self.map_stops.get_int(code) for code in atco_codes ]
        route_id_int = self.map_routes.get_int(route_id)
        journey_id_int = self.map_journeys.get_int(journey_id) if journey_id is not None else None
        # auto-resize to accept route and stop ids
        self._ensure_route_capacity(route_id_int)
        for atco in atco_code_ints:
            self._ensure_stop_capacity(atco)

        # set the ordered list of stops for this route
        self.route_stops[route_id_int] = list(atco_code_ints)
        # ensure each stop knows about this route
        for atco in self.route_stops[route_id_int]:
            if route_id_int not in self.stop_to_routes[atco]:
                self.stop_to_routes[atco].append(route_id_int)
        # associate journey to route
        if journey_id_int is not None:
            self._ensure_journey_capacity(journey_id_int)
            # add to route_journeys if not already present
            if journey_id_int not in self.route_journeys[route_id_int]:
                self.route_journeys[route_id_int].append(journey_id_int)
            # set reverse mapping
            self.journey_to_route[journey_id_int] = route_id_int

    def add_journey( self, journey_id, route_id, arrival_times ): 
        # treat journey_id and route_id as external codes and map them
        journey_id_int = self.map_journeys.get_int(journey_id)
        route_id_int = self.map_routes.get_int(route_id)
        # auto-resize to accept these ids
        self._ensure_journey_capacity(journey_id_int)
        self._ensure_route_capacity(route_id_int)
        # Basic validation of arrival_times entries
        validated = []
        for item in arrival_times:
            if not (isinstance(item, (list, tuple)) and len(item) >= 2):
                raise ValueError("arrival_times must be an iterable of (stop_code, arrival_time)")
            atco_code, atime = item[0], item[1]
            atco = self.map_stops.get_int(atco_code)
            if atco < 0 or atco >= len(self.stop_to_routes):
                raise IndexError("atco_code_int in arrival_times out of range")
            validated.append((atco, atime))
        # set journey times
        self.journey_times[journey_id_int] = validated
        # set mapping to route
        self.journey_to_route[journey_id_int] = route_id_int
        # add journey to route_journeys if missing
        if journey_id_int not in self.route_journeys[route_id_int]:
            self.route_journeys[route_id_int].append(journey_id_int)