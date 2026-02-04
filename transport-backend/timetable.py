from merged_data import MergedData
from raptor_router import RaptorRouter

class Timetable:
    def __init__( self, bus_data, train_data=None ):
        # Support being called with a mode string (legacy main.py usage)
        if isinstance(bus_data, str):
            self.mode = bus_data
            self.bus_data = None
            self.train_data = None
            self._stops = []
        else:
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

    def get_stops(self):
        # Return cached stops if this was created with a mode string, otherwise
        # attempt to return bus stops list if available.
        if hasattr(self, '_stops'):
            return self._stops
        if self.bus_data is None:
            return []
        # If bus_data has route information, return a best-effort list of stops
        try:
            # If bus_data provides route_stops, flatten and return unique stop codes
            stops = []
            for route in getattr(self.bus_data, 'route_stops', []):
                for s in route:
                    stops.append(s)
            return list(dict.fromkeys(stops))
        except Exception:
            return []