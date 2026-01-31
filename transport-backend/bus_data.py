class Bus_data:
    def __init__( self ):
        #route_id -> ordered list of stop_ids
        self.route_stops: dict[ int, list[ int ] ] = {}
        #route_id -> list of journey_ids
        self.route_journeys: dict[ int, list[ int ] ] = {}
        #journey_id -> list of ( arrival_time )
        self.journey_times: dict[ int, list[ int ] ] = {}
        #list of routes passing by each stop_id
        self.stop_to_routes: dict[ int, list[ int ] ] = {}
        #journey_id -> route_id
        self.journey_to_route: dict[ int, int ] = {}
        
    #given an atco_code, return the gazetteer id
    def get_gazetteer_id( self, atco_code: str ) -> str:
        gazetteer_id = self.atco.get_gazetteer_id( atco_code )
        return gazetteer_id
    
    #get a list of routes passing by the point
    def get_routes( self, atco_code: str ) -> list:
        return [ route1, route2 ]

    #get the first journey passing by the stoppoint on the route after the time
    #deal with operational days
    #handle delays and cancellations
    #if time > 86400s, date += 1
    def get_journey( self, route, date, time, atco_code: str ) -> tuple:
        return ( journey, "bus"/"train" )

    #get arrivalTime in seconds from midnight of start_date 
    #for each stopPoint on the journey after the given stopPoint
    #handle delays and cancellations
    def arrival_time( self, date, journey, atco_code: str ) -> dict:
        return { point: arrivalTime }

    #get journey details for a specific journey
    def get_journey_detail( self, type, journey, hop_on_Point, hop_off_point ) -> dict:
        return { vehicle: "", origin: "", destination: "", hop_on_point: time, hop_off_point: time }
