import math
from walking import Walking

class RaptorRouter:
    def __init__( self, yesterday, today, tomorrow ):
        self.yesterday = yesterday
        self.today = today
        self.tomorrow = tomorrow

    def route( self,
               n_transfer_limit: int,
               walking: Walking,
               start_date: str,
               start_time: int,
               start_point: str,
               destination: str ) -> dict:
        #creat a list of dicts of stops storing earliest arrival time, previous stop,
        #type of transport from previous stop, and journey id 
        inf = math.inf
        reach_stops = list( range( len( self.today.stop_to_routes ) ) )
        for stop in range( len( reach_stops ) ):
            reach_stops[ stop ] = { "prev_stop": None,
                                    "arrival_time": inf,
                                    "type": None,
                                    "journey": None,
                                    "day": None }
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
        visited = set()
        while track is not None:
            if track in visited:
                break
            visited.add(track)
            fastest_route[ track ] = reach_stops[ track ]
            track = reach_stops[ track ][ "prev_stop" ]
        if final_stop in fastest_route:
            fastest_route[ final_stop ][ "is_destination" ] = True
        for stop, info in fastest_route.items():
            if info.get("prev_stop") is None:
                info["is_origin"] = True
        for stop, info in fastest_route.items():
            day = info.get("day")
            if day:
                info["stop_name"] = day.stop_metadata[stop]
                journey_id = info.get("journey")
                if journey_id is not None:
                    info["journey"] = day.journey_metadata[journey_id]
            else:
                info["stop_name"] = ""
        return fastest_route


    def recursive_raptor( self,
                          start_date,
                          n_transfer: int,
                          transfer_limit: int,
                          reach_stops: dict,
                          walking: Walking,
                          switch_a: set,
                          switch_b: set ):
        if len( switch_a ) == 0 or n_transfer == transfer_limit:
            return
        else:
            n_transfer += 1
            switch_b = set()
            for stop in switch_a:
                #get routes passing by the stop
                routes = self.today.stop_to_routes[ stop ]
                #for each route, get first journey after arrival_time at stop
                for route in routes:
                    #if before noon, search yesterday, if none then search today
                    if reach_stops[ stop ][ "arrival_time" ] < 237600:
                        first_journey = self.first_journey( self.yesterday,
                                                            route,
                                                            stop,
                                                            reach_stops,
                                                            switch_b )
                        if first_journey is None:
                            first_journey = self.first_journey( self.today,
                                                                route,
                                                                stop,
                                                                reach_stops,
                                                                switch_b )
                    #if after noon, search today, if none then search tomorrow
                    else:
                        first_journey = self.first_journey( self.today,
                                                            route,
                                                            stop,
                                                            reach_stops,
                                                            switch_b )
                        if first_journey is None:
                            first_journey = self.first_journey( self.tomorrow,
                                                                route,
                                                                stop,
                                                                reach_stops,
                                                                switch_b )
            # process walking for switch_b
            walking_additions = set()
            for stop in switch_b:
                walk_stops = walking.inter_walk( stop )
                for walk_stop in walk_stops:
                    walk_arrival = reach_stops[ stop ][ "arrival_time" ] + walk_stops[ walk_stop ]
                    if reach_stops[ walk_stop ][ "arrival_time" ] > walk_arrival:
                        reach_stops[ walk_stop ][ "arrival_time" ] = walk_arrival
                        reach_stops[ walk_stop ][ "prev_stop" ] = stop
                        reach_stops[ walk_stop ][ "type" ] = "walking"
                        walking_additions.add( walk_stop )
            switch_b = switch_b.union( walking_additions )
            self.recursive_raptor( start_date,
                                   n_transfer,
                                   transfer_limit,
                                   reach_stops,
                                   walking,
                                   switch_b,
                                   switch_a )
            
    def first_journey( self, network, route, stop, reach_stops, switch_b ):
        journeys = network.route_journeys[ route ]
        first_journey = None
        # get first journey after arrival_time at stop
        for journey in journeys:
            # a list of ( atco_code_int, arrival_time )
            journey_times = network.journey_times[ journey ]
            for i, ( point, arrival_time ) in enumerate( journey_times ):
                if point == stop and arrival_time >= reach_stops[ stop ][ "arrival_time" ]:
                    # found the first journey
                    first_journey = journey
                    for subsequent_point, subsequent_time in journey_times[ i: ]:
                        if subsequent_time < reach_stops[ subsequent_point ][ "arrival_time" ]:
                            reach_stops[ subsequent_point ][ "arrival_time" ] = subsequent_time
                            reach_stops[ subsequent_point ][ "prev_stop" ] = stop
                            reach_stops[ subsequent_point ][ "type" ] = network.journey_type( first_journey )
                            reach_stops[ subsequent_point ][ "journey" ] = first_journey
                            reach_stops[ subsequent_point ][ "day" ] = network
                            switch_b.add( subsequent_point )
                    break
            else:
                continue
            break
        return first_journey