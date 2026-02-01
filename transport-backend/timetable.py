from merged_data import MergedData
from raptor_router import RaptorRouter

class Timetable:
    def __init__( self, bus_data, train_data ):
        self.bus_data = bus_data
        self.train_data = train_data
        
    def build_network( self, mode: str ):
        if mode == "bus":
            self.network = MergedData( self.bus_data, MergedDataEmpty() )
        elif mode == "train":
            self.network = MergedData( MergedDataEmpty(), self.train_data )
        else:
            self.network = MergedData( self.bus_data, self.train_data )
            
        self.raptor_router = RaptorRouter( self.network )