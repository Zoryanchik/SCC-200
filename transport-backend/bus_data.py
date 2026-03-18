from dense_mapper import DenseMapper


class BusData:
    """Container for per-date bus timetable data.

    Each instance holds the route/journey/stop graph for a single
    operating day.  External identifiers (ATCO codes, route IDs,
    journey IDs) are mapped to contiguous integers via DenseMapper so
    the data can be merged by MergedData with simple offset arithmetic.

    Data layout (all lists indexed by the corresponding dense int):
        route_stops      – ordered stop-ints per route
        route_journeys   – journey-ints per route
        journey_times    – list of (stop_int, arrival, departure) per journey
        stop_to_routes   – route-ints serving each stop
        journey_to_route – route-int for each journey
        route_metadata   – {route_id, line_name, …} per route
        journey_metadata – {journey_id, …} per journey
        route_link_tracks – dict[(from_stop_int,to_stop_int)] -> [(lat,lon),...]
               per route (stop-to-stop fragments for geometry)
    """

    def __init__(self, num_routes: int, num_journeys: int, num_stops: int):
        # Indexed by route_id_int — ordered lists of stop ints
        self.route_stops = [[] for _ in range(num_routes)]
        # Indexed by route_id_int — lists of journey_id_ints
        self.route_journeys = [[] for _ in range(num_routes)]
        # Indexed by journey_id_int — lists of (stop_int, arrival_s, departure_s)
        self.journey_times = [[] for _ in range(num_journeys)]
        # Indexed by stop_int — lists of route_id_ints passing through
        self.stop_to_routes = [[] for _ in range(num_stops)]
        # Indexed by journey_id_int — the owning route_id_int (-1 = unset)
        self.journey_to_route = [-1 for _ in range(num_journeys)]

        # Dense bidirectional mappers (external code ↔ int)
        self.map_stops = DenseMapper()
        self.map_routes = DenseMapper()
        self.map_journeys = DenseMapper()

        # Indexed by route_id_int — metadata dicts {route_id, line_name, …}
        self.route_metadata = [None for _ in range(num_routes)]
        # Indexed by journey_id_int — metadata dicts
        self.journey_metadata = [None for _ in range(num_journeys)]
        # Indexed by route_id_int — dict mapping stop-int pairs to polyline
        # fragments (lat,lon). These reflect RouteLink boundaries.
        self.route_link_tracks = [{} for _ in range(num_routes)]

    # --- auto-resize helpers -----------------------------------------------
    def _ensure_route_capacity(self, route_id_int):
        """Ensure route-based lists can hold index route_id_int."""
        while route_id_int >= len(self.route_stops):
            self.route_stops.append([])
            self.route_journeys.append([])
            self.route_metadata.append(None)
            self.route_link_tracks.append({})

    def _ensure_journey_capacity(self, journey_id_int):
        """Ensure journey-based lists can hold index journey_id_int."""
        while journey_id_int >= len(self.journey_times):
            self.journey_times.append([])
            self.journey_to_route.append(-1)
            self.journey_metadata.append(None)

    def _ensure_stop_capacity(self, atco_code_int):
        """Ensure stop-based lists can hold index atco_code_int."""
        while atco_code_int >= len(self.stop_to_routes):
            self.stop_to_routes.append([])

    def add_route_stop( self, route_id, atco_codes ):
        atco_code_ints = [ self.map_stops.get_int(code) for code in atco_codes ]
        route_id_int = self.map_routes.get_int(route_id)
        # auto-resize to hold route and stops
        self._ensure_route_capacity(route_id_int)
        for atco in atco_code_ints:
            self._ensure_stop_capacity(atco)
        # set the ordered list of stops for this route
        self.route_stops[route_id_int] = list(atco_code_ints)
        # ensure each stop knows about this route
        for atco in self.route_stops[route_id_int]:
            if route_id_int not in self.stop_to_routes[atco]:
                self.stop_to_routes[atco].append(route_id_int)

    def add_route_journeys( self, route_id, journey_ids ):
        """Set the list of journeys that belong to a route and update reverse mappings."""
        route_id_int = self.map_routes.get_int(route_id)
        self._ensure_route_capacity(route_id_int)
        journey_id_ints = []
        for jid in journey_ids:
            j_int = self.map_journeys.get_int(jid)
            self._ensure_journey_capacity(j_int)
            journey_id_ints.append(j_int)
        self.route_journeys[route_id_int] = journey_id_ints
        # update reverse mapping: journey -> route
        for j_int in journey_id_ints:
            self.journey_to_route[j_int] = route_id_int

    def add_journey_times( self, journey_id, arrival_times, departure_times=None ):
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
                raise ValueError("arrival_times must be an iterable of (atco_code, arrival_time)")
            atco_code, atime = item[0], item[1]
            if departure_times is not None:
                dtime = departure_times[i]
            else:
                dtime = atime
            atco = self.map_stops.get_int(atco_code)
            self._ensure_stop_capacity(atco)
            validated.append((atco, atime, dtime))
        self.journey_times[journey_id_int] = validated

    # NOTE: full-route polylines are intentionally removed. Geometry is provided via
    # per-link fragments in `route_link_tracks`.