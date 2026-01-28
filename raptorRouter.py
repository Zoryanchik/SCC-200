from datetime import datetime
import math
from timetable import Timetable

class RaptorRouter:

    def route( self, n_transfer_limit, timetable: Timetable, start_time, start_point, destination ):
        #creat a dict of dicts of stops storing earliest arrival time, previous stop,
        #type of transport from previous stop, and journey id 
        inf = math.inf
        all_stops = timetable.get_stops()
        reach_stops = {}
        for stop in all_stops:
            reach_stops[ stop ] = {"prev_stop": None,
                                    "arrival_time": inf,
                                    "type": "",
                                    "journey": "" }
        now = datetime.now()
        improved = True
        n_tranfer = -1
        reach_stops[ start_point ][ "arrival_time" ] = now.hour * 3600 + now.minute * 60 + now.second
        switch_a = [ start_point ]
        switch_b = []
        self.recursive_raptor( self, improved, n_tranfer, n_transfer_limit, reach_stops, timetable, switch_a, switch_b )
        #return a dict of dicts storing stops on the route from destination
        fastest_route = {}
        track = destination
        while track != start_point:
            fastest_route[ track ] = reach_stops[ track ]
            track = fastest_route[ track ][ "prev_stop" ]
        return fastest_route


    def recurcive_raptor( self, improved, n_transfer, transfer_limit, reach_stops, timetable: Timetable, switch_a: list, switch_b: list):
        if not improved or n_transfer == transfer_limit:
            return
        else:
            improved = False
            switch_b = []
            for stop in switch_a:
                routes = timetable.get_routes( stop )
                for route in routes:
                    first_journey = timetable.get_journey( route, reach_stops[ stop ][ "arrival_time" ], stop )
                    arrival_times = timetable.arrival_time( first_journey, stop )
                    for point, time in arrival_times.items():
                        if reach_stops[ point ][ "arrival_time"] > time:
                            reach_stops[ point ][ "prev_stop" ] = stop
                            reach_stops[ point ][ "arrival_time" ] = time
                            reach_stops[ point ][ "type" ] = first_journey[1]
                            reach_stops[ point ][ "journey" ] = first_journey[0]
                            improved = True
                            switch_b.append( point )
            self.recurcive_raptor( improved, n_transfer, transfer_limit, reach_stops, timetable, switch_b, switch_a )