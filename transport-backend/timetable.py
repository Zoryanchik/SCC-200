from atco import Atco

class Timetable:
    #type: bus, train, combined
    bus:bool
    train:bool

    def __init__( self, bus:bool, train:bool ):
        self.bus = bus
        self.train = train
        self.atco = Atco()

    #get a list of all stops in the timetable in AtcoCode( platform specific )
    def get_stops( self ) -> list:
        if self.type == "bus":
            return self.atco.get_bus_stops()
        elif self.type == "train":
            return self.atco.get_train_stops()
        else:
            return self.atco.get_all_stops()

    #get a list of routes passing by the point
    def get_routes( self, point ) -> list:
        
        return [ route1, route2 ]

    #get the first journey passing by the stoppoint on the route after the time
    #deal with operational days
    #handle delays and cancellations
    #if time > 86400s, date += 1
    def get_journey( self, route, date, time, point) -> tuple:
        return ( journey, "bus"/"train" )

    #get arrivalTime in seconds from midnight of start_date 
    #for each stopPoint on the journey after the given stopPoint
    #handle delays and cancellations
    def arrival_time( self, date, journey, point ) -> dict:
        return { point: arrivalTime }

    #get journey details for a specific journey
    def get_journey_detail( self, type, journey, hop_on_Point, hop_off_point ) -> dict:
        return { vehicle: "", origin: "", destination: "", hop_on_point: time, hop_off_point: time }

    #given an atco_code, return the gazetteer id
    def get_gazetteer_id( self, atco_code: str ) -> str:
        gazetteer_id = self.atco.get_gazetteer_id( atco_code )
        return gazetteer_id