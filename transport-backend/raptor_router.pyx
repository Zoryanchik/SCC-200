# cython: boundscheck=False, nonecheck=False
import math
import bisect
from typing import List, Any, Set, Optional, Tuple
import cython
from walking import Walking
from array import array
from modes import WALKING, int_to_name, all_transit_modes, name_to_int

class RaptorRouter:
    def __init__( self, yesterday, today, tomorrow ):
        self.yesterday = yesterday
        self.today = today
        self.tomorrow = tomorrow

    def route( self,
               n_transfer_limit: int,
               walking: Walking,
               start_time: int,
               start_point: Tuple[float, float],
               destination: Tuple[float, float],
               allowed_modes: Optional[Set[Any]] = None ) -> dict:
        # declare C variables up-front (required by Cython)
        cdef Py_ssize_t n_stops
        cdef object stop_to_routes
        cdef double[:] arrival_mv
        cdef int[:] prev_mv
        cdef int[:] mode_mv
        cdef int[:] journey_mv
        cdef double[:] a
        cdef int[:] p
        cdef int[:] mo
        cdef int[:] jo
        cdef Py_ssize_t i, llen
        cdef Py_ssize_t stop
        cdef Py_ssize_t walk_stop
        # create typed storage for reachability to reduce Python-level overhead
        inf = math.inf
        n_stops = len(self.today.stop_to_routes)
        # local reference to stop_to_routes to avoid repeated attribute lookups
        stop_to_routes = self.today.stop_to_routes
        # arrival times (double), prev_stop (int, -1 == None), mode (int, -1 == None), journey id (int, -1==None)
        arrival = array('d', [inf]) * n_stops
        prev = array('i', [-1]) * n_stops
        mode = array('i', [-1]) * n_stops
        journey = array('i', [-1]) * n_stops
        # day references (Python objects) kept in a list
        day_refs = [None] * n_stops
        # memoryviews for fast C access and local aliases (assign after arrays exist)
        arrival_mv = arrival
        prev_mv = prev
        mode_mv = mode
        journey_mv = journey
        a = arrival_mv
        p = prev_mv
        mo = mode_mv
        jo = journey_mv
        n_transfer = -1
        # reachable_stops returns list of (stop, walk_seconds)
        initial_list = walking.reachable_stops( start_point )
        initial_stops = dict(initial_list)
        switch_a = []
        switch_b = []
        # keep a set for fast membership checks while preserving list order
        switch_a_set = set()
        llen = len(initial_list)
        for i in range(llen):
            tup = initial_list[i]
            stop = <Py_ssize_t> tup[0]
            walk_time = tup[1]
            a[stop] = start_time + walk_time
            mo[stop] = WALKING
            # prev remains -1 for origin
            if stop not in switch_a_set:
                switch_a.append(stop)
                switch_a_set.add(stop)
        # Normalize allowed_modes: accept either string names or int codes
        if allowed_modes is None:
            allowed_modes = all_transit_modes()
        else:
            normalized = set()
            for m in allowed_modes:
                if isinstance(m, str):
                    mi = name_to_int(m)
                    if mi is not None:
                        normalized.add(mi)
                elif isinstance(m, int):
                    normalized.add(m)
            allowed_modes = normalized
        self.recursive_raptor(
            n_transfer,
            n_transfer_limit,
            arrival_mv,
            prev_mv,
            mode_mv,
            journey_mv,
            day_refs,
            walking,
            switch_a,
            switch_b,
            allowed_modes,
        )
        final_list = walking.reachable_stops(destination)
        if not final_list:
            return {}
        final_stops = dict(final_list)
        # Find the reached stop that gives the earliest arrival at destination
        # (transit arrival at stop + walking time from stop to destination)
        best_final_stop = None
        final_walk_seconds = None
        best_total_arrival = math.inf
        for tup in final_list:
            stop = <Py_ssize_t> tup[0]
            walk_time = tup[1]
            if a[stop] < math.inf:
                if mo[stop] == WALKING:
                    pre = p[stop]
                    if pre == -1:
                        continue
                    wal = walking.walking_time_between(walking.get_loc_coords(pre), destination)
                    total_arrival = a[pre] + wal
                    if total_arrival < best_total_arrival:
                        best_total_arrival = total_arrival
                        best_final_stop = pre
                        final_walk_seconds = wal
                else:
                    total_arrival = a[stop] + walk_time
                    if total_arrival < best_total_arrival:
                        best_total_arrival = total_arrival
                        best_final_stop = stop
                        final_walk_seconds = walk_time
        if best_final_stop is None:
            return {}
        arrival_time = best_total_arrival
        dir_walking = walking.walking_time_between( start_point, destination )
        if dir_walking is not None and dir_walking + start_time < arrival_time + 10:
            # Direct walking is faster than any transit route found
            return {
                '_meta': {
                    'start_walk_seconds': dir_walking,
                    'end_walk_seconds': 0,
                    'total_arrival': start_time + dir_walking,
                    'start_point': start_point,
                    'destination': destination,
                }
                # No transit legs, just a direct walk
            }
        # return a dict of dicts storing stops on the route from destination
        fastest_route = {}
        track = best_final_stop
        visited = set()
        while track is not None and track != -1:
            if track in visited:
                break
            visited.add(track)
            prev_stop = p[track]
            arrival_time_val = a[track]
            mode_code = mo[track] if mo[track] != -1 else None
            journey_id = jo[track] if jo[track] != -1 else None
            day_obj = day_refs[track]
            # convert internal representation into the dict returned to callers
            fastest_route[track] = {
                "prev_stop": (prev_stop if prev_stop != -1 else None),
                "arrival_time": arrival_time_val,
                "mode": int_to_name(mode_code),
                "journey": journey_id,
                "day": day_obj,
            }
            track = prev_mv[track] if prev_mv[track] != -1 else None
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
                        # Use destination_display from journey metadata if available, else last stop
                        journey_meta = day.journey_metadata[j_id] if j_id < len(day.journey_metadata) else {}
                        dest_display = journey_meta.get('destination_display', '')
                        info['journey_destination'] = dest_display or (day.stop_metadata[last_stop] if last_stop < len(day.stop_metadata) else f"stop#{last_stop}")
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
                          n_transfer: int,
                          transfer_limit: int,
                          arrival_mv,
                          prev_mv,
                          mode_mv,
                          journey_mv,
                          day_refs,
                          walking: Walking,
                          switch_a: list,
                          switch_b: list,
                          allowed_modes: Set[int] ):
        if len(switch_a) == 0 or n_transfer == transfer_limit:
            return
        n_transfer += 1
        switch_b = []
        # local references to avoid attribute lookups inside loops
        cdef object stop_to_routes = self.today.stop_to_routes
        cdef object networks = (self.yesterday, self.today, self.tomorrow)
        # local aliases for memoryviews
        cdef double[:] a = arrival_mv
        cdef int[:] p = prev_mv
        cdef int[:] mo = mode_mv
        cdef int[:] jo = journey_mv
        cdef Py_ssize_t i, la, jidx, lr, k, m, lb
        la = len(switch_a)
        for i in range(la):
            stop = switch_a[i]
            # get routes passing by the stop
            routes = stop_to_routes[stop]
            # for each route, get first journey after arrival_time at stop
            lr = len(routes)
            for jidx in range(lr):
                route = routes[jidx]
                # Search all three day-networks (times are already offset)
                # and pick the earliest valid journey
                best = None
                for k in range(3):
                    network = networks[k]
                    j = self.first_journey(
                        network,
                        route,
                        stop,
                        arrival_mv,
                        prev_mv,
                        mode_mv,
                        journey_mv,
                        day_refs,
                        switch_b,
                        allowed_modes,
                    )
                    if j is not None:
                        best = j
                        break  # networks are in time order, first hit is earliest
        # process walking for switch_b
        walking_additions = set()
        lb = len(switch_b)
        for m in range(lb):
            stop = <Py_ssize_t> switch_b[m]
            walk_stops = walking.inter_walk(stop)
            # iterate dict entries using Python iterator (walk_stops likely small)
            for kv in walk_stops.items():
                walk_stop = <Py_ssize_t> kv[0]
                wtime = kv[1]
                walk_arrival = a[stop] + wtime
                if a[walk_stop] > walk_arrival:
                    a[walk_stop] = walk_arrival
                    p[walk_stop] = stop
                    mo[walk_stop] = WALKING
                    # preserve day reference from originating stop
                    day_refs[walk_stop] = day_refs[stop]
                    walking_additions.add(walk_stop)
        for w in walking_additions:
            if w not in switch_b:
                switch_b.append(w)
        self.recursive_raptor(
            n_transfer,
            transfer_limit,
            arrival_mv,
            prev_mv,
            mode_mv,
            journey_mv,
            day_refs,
            walking,
            switch_b,
            switch_a,
            allowed_modes,
        )
            
    def first_journey( self, network, route, stop, arrival_mv, prev_mv, mode_mv, journey_mv, day_refs, switch_b, allowed_modes ):
        # declare C variables up-front for Cython
        cdef Py_ssize_t arrival_start
        cdef Py_ssize_t idx
        cdef Py_ssize_t dep_time
        cdef Py_ssize_t journey_id
        cdef Py_ssize_t stop_deps_len
        # local aliases for memoryviews
        cdef double[:] a
        cdef int[:] p
        cdef int[:] mo
        cdef int[:] jo
        # Use precomputed route_stop_departures for O(log n) lookup
        rsd = network.route_stop_departures
        if route >= len(rsd):
            return None
        stop_deps = rsd[route].get(stop)
        if not stop_deps:
            return None
        # assign memoryview aliases now that parameters are available
        a = arrival_mv
        p = prev_mv
        mo = mode_mv
        jo = journey_mv
        arrival_start = <Py_ssize_t> int(a[stop] + 90)
        # Binary search for first departure >= arrival
        idx = bisect.bisect_left(stop_deps, (arrival_start,))

        # Scan forward to find a journey whose mode is allowed
        stop_deps_len = len(stop_deps)
        while idx < stop_deps_len:
            # unpack candidate departure (dep_time, journey_id)
            dep_time = stop_deps[idx][0]
            journey_id = stop_deps[idx][1]
            if network.journey_type(journey_id) not in allowed_modes:
                idx += 1
                continue
            # Propagate arrival times to subsequent stops
            journey_times = network.journey_times[journey_id]
            jsi = network.journey_stop_index[journey_id]
            start_pos = jsi.get(stop, None)
            if start_pos is None:
                # This journey doesn't have the boarding stop in its index.
                # Skip to the next candidate departure instead of aborting
                # the whole search for this route/network.
                idx += 1
                continue
            # iterate over subsequent stops (slice cost acceptable here)
            for tup in journey_times[start_pos + 1:]:
                subsequent_point = <Py_ssize_t> tup[0]
                subsequent_a_time = tup[1]
                subsequent_d_time = tup[2]
                if subsequent_point == stop:
                    continue  # don't loop back to boarding stop
                if subsequent_a_time < a[subsequent_point]:
                    a[subsequent_point] = subsequent_a_time
                    p[subsequent_point] = stop
                    mo[subsequent_point] = network.journey_type(journey_id)
                    jo[subsequent_point] = journey_id
                    day_refs[subsequent_point] = network
                    if subsequent_point not in switch_b:
                        switch_b.append(subsequent_point)
            return journey_id
        return None
