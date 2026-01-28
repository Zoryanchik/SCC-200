class Timetable:
    #type: bus, train, combined
    type: str

    def __init__( self, type ):
        self.type = type

    def time_to_seconds( time_str: str ) -> int:
        h, m, s = map( int, time_str.split( ":" ) )
        return h * 3600 + m * 60 + s
    
    def seconds_to_time( seconds: int ) -> str:
        h = seconds // 3600
        m = ( seconds % 3600 ) // 60
        s = seconds % 60
        return f"{h:02}:{m:02}:{s:02}"

    #get a list of all stops in the timetable
    def get_stops( self ) -> list:
        return []

    #get a list of routes passing by the point
    def get_routes( self, point ) -> list:
        return [ route1, route2 ]

    #get the first journey passing by the stoppoint on the route after the time
    def get_journey( self, route, time, point) -> tuple:
        return ( journey, "bus"/"train" )

    #get arrivalTime for each stopPoint on the journey after the given stopPoint
    def arrival_time( self, journey, point ) -> dict:
        return { point: arrivalTime }

    #get journey details for a specific journey
    def get_journey_detail( self, type, journey, hop_on_Point, hop_off_point ) -> dict:
        return { vehicle: "", origin: "", destination: "", hop_on_point: time, hop_off_point: time }