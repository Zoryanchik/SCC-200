from merged_data import MergedData
from raptor_router import RaptorRouter

class Timetable:
    def __init__( self, bus_data_yesterday, train_data_yesterday,
                        bus_data_today,   train_data_today,
                        bus_data_tomorrow,train_data_tomorrow ):
        self.bus_data_yesterday = bus_data_yesterday
        self.train_data_yesterday = train_data_yesterday
        self.bus_data_today = bus_data_today
        self.train_data_today = train_data_today
        self.bus_data_tomorrow = bus_data_tomorrow
        self.train_data_tomorrow = train_data_tomorrow
        self.raptor_router = None
        
    def build_network( self, mode: str ):
        if mode == "bus":
            self.yesterday = MergedData( self.bus_data_yesterday, None )
            self.today = MergedData( self.bus_data_today, None )
            self.tomorrow = MergedData( self.bus_data_tomorrow, None )
        elif mode == "train":
            self.yesterday = MergedData( None, self.train_data_yesterday )
            self.today = MergedData( None, self.train_data_today )
            self.tomorrow = MergedData( None, self.train_data_tomorrow )
        else:
            self.yesterday = MergedData( self.bus_data_yesterday, self.train_data_yesterday )
            self.today = MergedData( self.bus_data_today, self.train_data_today )
            self.tomorrow = MergedData( self.bus_data_tomorrow, self.train_data_tomorrow )
        #get a router
        self.raptor_router = RaptorRouter( self.yesterday, self.today, self.tomorrow )