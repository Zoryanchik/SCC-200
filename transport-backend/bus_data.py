class BusData:
    def __init__( self, num_routes:int, num_journeys:int, num_stops:int ):
        #indexed by route_id_int, store ordered lists of atco_code_ints
        self.route_stops = [ [] for _ in range( num_routes ) ]
        #indexed by route_id_int, store lists of journey_id_ints
        self.route_journeys = [ [] for _ in range( num_routes ) ]
        #indexed by journey_id_int, store lists of ( atco_code_int, arrival_time )
        self.journey_times = [ [ () ] for _ in range( num_journeys ) ]
        #indexed by atco_code_int, store lists of route_id_int passing by each atco_code_int
        self.stop_to_routes = [ [] for _ in range( num_stops ) ]
        #indexed by journey_id_int, store route_id_ints
        self.journey_to_route = [ int ]

    #get a list of routes passing by the stop
    def get_routes( self, atco_code_int ) -> list:
        return self.stop_to_routes[ atco_code_int]

    #get the first journey passing by the stoppoint on the route after the time
    #deal with operational days
    #handle delays and cancellations
    #if time > 86400s, date += 1
    def get_journey( self, route, date, time, atco_code: str ) -> tuple:
        return ( journey, "bus"/"train" )

    #get journey details for a specific journey
    def get_journey_detail( self, type, journey, hop_on_Point, hop_off_point ) -> dict:
        return { vehicle: "", origin: "", destination: "", hop_on_point: time, hop_off_point: time }
