from bus_data import BusData
from train_data import TrainData

class MergedData:
    def __init__( self, bus_data: BusData, train_data: TrainData ):
        #indexed by route_id_int, store ordered lists of atco_code_ints
        self.route_stops = []
        #indexed by route_id_int, store lists of ODERED journey_id_ints
        self.route_journeys = []
        #indexed by journey_id_int, store lists of ordered ( atco_code_int, arrival_time )
        self.journey_times = []
        #indexed by atco_code_int, store lists of route_id_int passing by each atco_code_int
        self.stop_to_routes = []
        #indexed by journey_id_int, store route_id_ints
        self.journey_to_route = [ int ]
        
    def journey_type( self, journey_id_int: int ) -> str:
        return "bus" if journey_id_int < len( self.bus_data.journey_times ) else "train"

    #really?
    def merge_data( self, bus_data: BusData, train_data: TrainData ):
        #merge route_stops
        self.route_stops = bus_data.route_stops + train_data.route_stops
        #merge route_journeys
        self.route_journeys = bus_data.route_journeys + train_data.route_journeys
        #merge journey_times
        self.journey_times = bus_data.journey_times + train_data.journey_times
        #merge stop_to_routes
        self.stop_to_routes = bus_data.stop_to_routes + train_data.stop_to_routes
        #merge journey_to_route
        self.journey_to_route = bus_data.journey_to_route + train_data.journey_to_route