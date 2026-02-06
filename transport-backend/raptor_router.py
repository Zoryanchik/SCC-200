import math
import bisect
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
               destination: str,
               allowed_modes: set = None ) -> dict:
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
        if allowed_modes is None:
            allowed_modes = {"bus", "train"}
        self.recursive_raptor( start_date,
                               n_transfer,
                               n_transfer_limit,
                               reach_stops,
                               walking,
                               switch_a,
                               switch_b,
                               allowed_modes )
        final_stops = walking.reachable_stops( destination )
        if not final_stops:
            return {}
        final_stop = next( iter( final_stops ) )
        arrival_time = reach_stops[ final_stop ][ "arrival_time" ] + final_stops[ final_stop ]
        for stop, walk_time in final_stops.items():
            if walk_time + reach_stops[ stop ][ "arrival_time" ] < arrival_time:
                final_stop = stop
                arrival_time = walk_time + reach_stops[ stop ][ "arrival_time" ]
        final_walk_seconds = final_stops[ final_stop ]
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

        # Identify the origin stop (first stop reached by initial walk)
        origin_stop = None
        for s in fastest_route:
            if fastest_route[s]["prev_stop"] is None:
                origin_stop = s
                break
        start_walk_seconds = initial_stops.get(origin_stop, 0) if origin_stop is not None else 0

        for stop, info in fastest_route.items():
            day = info.get('day')
            # enrich with human-readable metadata without clobbering raw ids
            if day is not None:
                if stop < len(day.stop_metadata):
                    info['stop_name'] = day.stop_metadata[ stop ]
                prev = info.get('prev_stop')
                if prev is not None and prev < len(day.stop_metadata):
                    info['prev_stop_name'] = day.stop_metadata[ prev ]
                j_id = info.get('journey')
                if j_id is not None and j_id < len(day.journey_metadata):
                    info['journey_info'] = day.journey_metadata[ j_id ]
                # enrich with journey detail: line name, origin→destination,
                # and departure time at the boarding (prev) stop
                if j_id is not None and j_id < len(day.journey_times):
                    jt = day.journey_times[j_id]
                    if jt:
                        first_stop = jt[0][0]
                        last_stop  = jt[-1][0]
                        info['journey_origin'] = day.stop_metadata[first_stop] if first_stop < len(day.stop_metadata) else f"stop#{first_stop}"
                        info['journey_destination'] = day.stop_metadata[last_stop] if last_stop < len(day.stop_metadata) else f"stop#{last_stop}"
                    # departure time at the boarding stop (prev_stop)
                    if prev is not None:
                        jsi = day.journey_stop_index[j_id] if j_id < len(day.journey_stop_index) else {}
                        pos = jsi.get(prev)
                        if pos is not None:
                            info['board_departure'] = jt[pos][2]  # departure_time at prev_stop

        # Attach walk-leg metadata so the caller can display them
        fastest_route['_meta'] = {
            'start_walk_seconds': start_walk_seconds,
            'end_walk_seconds':   final_walk_seconds,
            'total_arrival':      arrival_time,
            'start_point':        start_point,
            'destination':        destination,
        }
        return fastest_route


    def recursive_raptor( self,
                          start_date,
                          n_transfer: int,
                          transfer_limit: int,
                          reach_stops: dict,
                          walking: Walking,
                          switch_a: set,
                          switch_b: set,
                          allowed_modes: set ):
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
                    # Search all three day-networks (times are already offset)
                    # and pick the earliest valid journey
                    best = None
                    for network in (self.yesterday, self.today, self.tomorrow):
                        j = self.first_journey( network,
                                                route,
                                                stop,
                                                reach_stops,
                                                switch_b,
                                                allowed_modes )
                        if j is not None:
                            best = j
                            break           # networks are in time order, first hit is earliest
            #process walking for switch_b
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
                                   switch_a,
                                   allowed_modes )
            
    def first_journey( self, network, route, stop, reach_stops, switch_b, allowed_modes ):
        # Use precomputed route_stop_departures for O(log n) lookup
        rsd = network.route_stop_departures
        if route >= len(rsd):
            return None
        stop_deps = rsd[route].get(stop)
        if not stop_deps:
            return None

        arrival = reach_stops[stop]["arrival_time"]
        # Binary search for first departure >= arrival
        idx = bisect.bisect_left(stop_deps, (arrival,))

        # Scan forward to find a journey whose mode is allowed
        while idx < len(stop_deps):
            dep_time, first_journey = stop_deps[idx]
            if network.journey_type(first_journey) not in allowed_modes:
                idx += 1
                continue
            # Propagate arrival times to subsequent stops
            journey_times = network.journey_times[first_journey]
            jsi = network.journey_stop_index[first_journey]
            start_pos = jsi.get(stop, None)
            if start_pos is None:
                return None

            for subsequent_point, subsequent_a_time, subsequent_d_time in journey_times[start_pos + 1:]:
                if subsequent_point == stop:
                    continue                        # don't loop back to boarding stop
                if subsequent_a_time < reach_stops[subsequent_point]["arrival_time"]:
                    reach_stops[subsequent_point]["arrival_time"] = subsequent_a_time
                    reach_stops[subsequent_point]["prev_stop"] = stop
                    reach_stops[subsequent_point]["type"] = network.journey_type(first_journey)
                    reach_stops[subsequent_point]["journey"] = first_journey
                    reach_stops[subsequent_point]["day"] = network
                    switch_b.add(subsequent_point)
            return first_journey
        return None