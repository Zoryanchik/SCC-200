from dense_mapper import DenseMapper


class TrainData:
    """Container for per-date train timetable data.

    Mirrors the BusData interface so MergedData can treat bus and train
    datasets identically.
    """

    def __init__(self, num_routes: int, num_journeys: int, num_stops: int):
        # indexed by route_id_int, store ordered lists of atco_code_ints
        self.route_stops = [[] for _ in range(num_routes)]
        # indexed by route_id_int, store lists of journey_id_ints
        self.route_journeys = [[] for _ in range(num_routes)]
        # indexed by journey_id_int, store lists of (atco_code_int, arrival_time, departure_time)
        self.journey_times = [[] for _ in range(num_journeys)]
        # indexed by atco_code_int, store lists of route_id_int
        self.stop_to_routes = [[] for _ in range(num_stops)]
        # indexed by journey_id_int, store route_id_ints
        self.journey_to_route = [-1 for _ in range(num_journeys)]
        # dense mappers for stops, routes, journeys
        self.map_stops = DenseMapper()
        self.map_routes = DenseMapper()
        self.map_journeys = DenseMapper()
        # indexed by route_id_int, store metadata dicts { route_id, line_name }
        self.route_metadata = [None for _ in range(num_routes)]
        # indexed by journey_id_int, store metadata dicts
        self.journey_metadata = [None for _ in range(num_journeys)]

    # --- auto-resize helpers -----------------------------------------------

    def _ensure_route_capacity(self, route_id_int):
        while route_id_int >= len(self.route_stops):
            self.route_stops.append([])
            self.route_journeys.append([])
            self.route_metadata.append(None)

    def _ensure_journey_capacity(self, journey_id_int):
        while journey_id_int >= len(self.journey_times):
            self.journey_times.append([])
            self.journey_to_route.append(-1)
            self.journey_metadata.append(None)

    def _ensure_stop_capacity(self, atco_code_int):
        while atco_code_int >= len(self.stop_to_routes):
            self.stop_to_routes.append([])

    # --- data population ---------------------------------------------------

    def add_route_stop(self, route_id, atco_codes):
        atco_code_ints = [self.map_stops.get_int(code) for code in atco_codes]
        route_id_int = self.map_routes.get_int(route_id)
        self._ensure_route_capacity(route_id_int)
        for atco in atco_code_ints:
            self._ensure_stop_capacity(atco)
        self.route_stops[route_id_int] = list(atco_code_ints)
        for atco in self.route_stops[route_id_int]:
            if route_id_int not in self.stop_to_routes[atco]:
                self.stop_to_routes[atco].append(route_id_int)

    def add_route_journeys(self, route_id, journey_ids):
        route_id_int = self.map_routes.get_int(route_id)
        self._ensure_route_capacity(route_id_int)
        journey_id_ints = []
        for jid in journey_ids:
            j_int = self.map_journeys.get_int(jid)
            self._ensure_journey_capacity(j_int)
            journey_id_ints.append(j_int)
        self.route_journeys[route_id_int] = journey_id_ints
        for j_int in journey_id_ints:
            self.journey_to_route[j_int] = route_id_int

    def add_journey_times(self, journey_id, arrival_times, departure_times=None):
        """Set per-stop arrival (and optionally departure) times for a journey.

        arrival_times:   list of (atco_code, arrival_seconds) tuples
        departure_times: optional parallel list of departure seconds;
                         if omitted each departure defaults to the arrival time.
        """
        journey_id_int = self.map_journeys.get_int(journey_id)
        self._ensure_journey_capacity(journey_id_int)
        validated = []
        for i, item in enumerate(arrival_times):
            if not (isinstance(item, (list, tuple)) and len(item) >= 2):
                raise ValueError(
                    "arrival_times must be an iterable of (atco_code, arrival_time)"
                )
            atco_code, atime = item[0], item[1]
            if departure_times is not None:
                dtime = departure_times[i]
            else:
                dtime = atime
            atco = self.map_stops.get_int(atco_code)
            self._ensure_stop_capacity(atco)
            validated.append((atco, atime, dtime))
        self.journey_times[journey_id_int] = validated
