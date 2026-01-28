from datetime import datetime
import math
from timetable import Timetable

class RaptorRouter:

    def route( self, n_transfer_limit: int, timetable: Timetable, start_time: int, start_point: str, destination: str ) -> dict:
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
        improved = True
        n_transfer = -1
        reach_stops[ start_point ][ "arrival_time" ] = start_time
        switch_a = [ start_point ]
        switch_b = []
        self.recursive_raptor( self, improved, n_transfer, n_transfer_limit, reach_stops, timetable, switch_a, switch_b )
        #return a dict of dicts storing stops on the route from destination
        fastest_route = {}
        track = destination
        while reach_stops[ track ][ "prev_stop" ] is not None and track != start_point:
            fastest_route[ track ] = reach_stops[ track ]
            track = fastest_route[ track ][ "prev_stop" ]
        return fastest_route


    def recursive_raptor( self, improved: bool, n_transfer: int, transfer_limit: int, reach_stops: list, timetable: Timetable, switch_a: list, switch_b: list):
        if not improved or n_transfer == transfer_limit:
            return
        else:
            improved = False
            n_transfer += 1
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
            self.recursive_raptor( improved, n_transfer, transfer_limit, reach_stops, timetable, switch_b, switch_a )