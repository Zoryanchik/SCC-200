from merged_data import MergedData
from raptor_router import RaptorRouter

class Timetable:
    def __init__( self, bus_data_yesterday, train_data_yesterday,
                        bus_data_today,   train_data_today,
                        bus_data_tomorrow,train_data_tomorrow,
                        stop_name_fn=None ):
        self.bus_data_yesterday = bus_data_yesterday
        self.train_data_yesterday = train_data_yesterday
        self.bus_data_today = bus_data_today
        self.train_data_today = train_data_today
        self.bus_data_tomorrow = bus_data_tomorrow
        self.train_data_tomorrow = train_data_tomorrow
        self.stop_name_fn = stop_name_fn
        self.raptor_router = None
        
    def build_network( self, mode: str ):
        def _pick(mode, bus, train):
            if mode == "bus":
                return (bus, None)
            elif mode == "train":
                return (None, train)
            else:
                return (bus, train)

        args_y = _pick(mode, self.bus_data_yesterday, self.train_data_yesterday)
        args_t = _pick(mode, self.bus_data_today,     self.train_data_today)
        args_m = _pick(mode, self.bus_data_tomorrow,  self.train_data_tomorrow)

        self.yesterday = MergedData(*args_y, stop_name_fn=self.stop_name_fn, time_offset=-86400)
        self.today     = MergedData(*args_t, stop_name_fn=self.stop_name_fn, time_offset=0)
        self.tomorrow  = MergedData(*args_m, stop_name_fn=self.stop_name_fn, time_offset=86400)
        #get a router
        self.raptor_router = RaptorRouter( self.yesterday, self.today, self.tomorrow )