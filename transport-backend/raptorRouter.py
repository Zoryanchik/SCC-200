import math
from timetable import Timetable
from walking import Walking

class RaptorRouter:

    def route( self, n_transfer_limit: int, timetable: Timetable, walking: Walking, start_date: str, start_time: int, start_point: str, destination: str ) -> dict:
        #creat a dict of dicts of stops storing earliest arrival time, previous stop,
        #type of transport from previous stop, and journey id 
        inf = math.inf
        all_stops = timetable.get_stops()
        reach_stops = {}
        for stop in all_stops:
            reach_stops[ stop ] = {"prev_stop": None,
                                    "arrival_time": inf,
                                    "type": None,
                                    "journey": None }
        n_transfer = -1
        initial_stops = walking.reachable_stops( start_point )
        switch_a = set()
        switch_b = set()
        for stop, walk_time in initial_stops.items():
            reach_stops[ stop ][ "arrival_time" ] = start_time + walk_time
            reach_stops[ stop ][ "type" ] = "walking"
            switch_a.add( stop )
        self.recursive_raptor( start_date,
                               n_transfer,
                               n_transfer_limit,
                               reach_stops,
                               timetable,
                               walking,
                               switch_a,
                               switch_b )
        final_stops = walking.reachable_stops( destination )
        final_stop = next( iter( final_stops ) )
        arrival_time = reach_stops[ final_stop ][ "arrival_time" ] + final_stops[ final_stop ]
        for stop, walk_time in final_stops.items():
            if walk_time + reach_stops[ stop ][ "arrival_time" ] < arrival_time:
                final_stop = stop
                arrival_time = walk_time + reach_stops[ stop ][ "arrival_time" ]
        #return a dict of dicts storing stops on the route from destination
        fastest_route = {}
        track = final_stop
        while track is not None:
            fastest_route[ track ] = reach_stops[ track ]
            track = fastest_route[ track ][ "prev_stop" ]
        return fastest_route


    def recursive_raptor( self,
                          start_date,
                          n_transfer: int,
                          transfer_limit: int,
                          reach_stops: dict,
                          timetable: Timetable,
                          walking: Walking,
                          switch_a: set,
                          switch_b: set ):
        if len( switch_a ) == 0 or n_transfer == transfer_limit:
            return
        else:
            n_transfer += 1
            switch_b = set()
            for stop in switch_a:
                routes = timetable.get_routes( stop )
                for route in routes:
                    first_journey = timetable.get_journey( route, start_date, reach_stops[ stop ][ "arrival_time" ], stop )
                    arrival_times = timetable.arrival_time( start_date, first_journey, stop )
                    for point, time in arrival_times.items():
                        if reach_stops[ point ][ "arrival_time"] > time:
                            reach_stops[ point ][ "prev_stop" ] = stop
                            reach_stops[ point ][ "arrival_time" ] = time 
                            reach_stops[ point ][ "type" ] = first_journey[1]
                            reach_stops[ point ][ "journey" ] = first_journey[0]
                            switch_b.add( point )
            #process walking for switch_b
            for stop in switch_b:
                walk_stops = walking.inter_walk( stop )
                for walk_stop in walk_stops:
                    reach_stops[ walk_stop ][ "arrival_time" ] = reach_stops[ stop ][ "arrival_time" ] + walk_stops[ walk_stop ]
                    reach_stops[ walk_stop ][ "prev_stop" ] = stop
                    reach_stops[ walk_stop ][ "type" ] = "walking"
                    switch_b.add( walk_stop )
            self.recursive_raptor( start_date,
                                   n_transfer,
                                   transfer_limit,
                                   reach_stops,
                                   timetable,
                                   walking,
                                   switch_b,
                                   switch_a )