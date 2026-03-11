"""RAPTOR router — single-network variant.

The caller builds a MergedData containing two day-halves (e.g.
yesterday + today before noon, today + tomorrow after noon) with their
journey times already shifted.  The router operates on that single flat
network using the standard round-based RAPTOR algorithm.

Performance notes
-----------------
* ``switch_a`` / ``switch_b`` (the "marked stops" sets) are Python
  ``set`` objects so membership tests are O(1).
* ``route_stop_departures`` is pre-sorted by departure time so the
  earliest usable journey is found via ``bisect``.
* A 90-second minimum-connection-time is enforced between alighting
  and the next departure (configurable via ``MIN_TRANSFER_SECONDS``).
"""

import math
import bisect
from typing import List, Any, Set, Optional, Tuple
from walking import Walking
from modes import WALKING, BUS, TRAIN, int_to_name, all_transit_modes, name_to_int

# Boarding buffers (seconds) applied after arrival at a stop before
# allowing boarding of a vehicle.  These replace the previous global
# MIN_TRANSFER_SECONDS / INITIAL_BOARDING_TOLERANCE constants and are
# chosen per-mode to reflect realistic boarding requirements.
BUS_BOARD_BUFFER = 60    # 1 minute for buses
TRAIN_BOARD_BUFFER = 180 # 3 minutes for trains


class RaptorRouter:
    """Round-based RAPTOR on a single MergedData network."""

    def __init__(self, network):
        self.network = network
        self.found = False

    # ── public entry point ───────────────────────────────────────

    def route(self,
              n_transfer_limit: int,
              walking: Walking,
              start_time: int,
              start_point: Tuple[float, float],
              destination: Tuple[float, float],
              allowed_modes: Optional[Set[Any]] = None,
              debug_stop_ids: Optional[Set[int]] = None) -> dict:
        """Run RAPTOR and return the fastest route as a trace-back dict.

        Returns
        -------
        dict
            Keys are stop-ints with trace-back info, plus a ``_meta``
            key with walk durations and arrival time.  Empty dict if no
            route is found.
        """
        inf = math.inf
        net = self.network
        n_stops = len(net.stop_to_routes)

        # Per-stop best-known state:
        #   [prev_stop, arrival_time, mode_int, journey_id, network_ref]
        reach_stops = [[None, inf, None, None, None] for _ in range(n_stops)]

        # Seed reachable stops from the start location
        initial_list = walking.reachable_stops(start_point)
        initial_stops = dict(initial_list)
        switch_a: set = set()
        for stop, walk_time in initial_list:
            reach_stops[stop][1] = start_time + walk_time
            reach_stops[stop][2] = WALKING
            switch_a.add(stop)
            # Debug: record initial seeding for selected stops
            if debug_stop_ids is not None and stop in debug_stop_ids:
                # store a lightweight event list on the router instance for this call
                if not hasattr(self, '_debug_events'):
                    self._debug_events = []
                self._debug_events.append((stop, 'seed_walk', start_time + walk_time))

        # Normalize allowed_modes
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

        final_list = walking.reachable_stops(destination)
        if not final_list:
            return {}

        # Clear any previous debug events and run recursive rounds
        if hasattr(self, '_debug_events'):
            del self._debug_events

        self.recursive_raptor(
            final_list, -1, n_transfer_limit, reach_stops,
            walking, switch_a, allowed_modes, initial_stops,
            debug_stop_ids=debug_stop_ids
        )

        # ── Find best final stop ─────────────────────────────────
        best_final_stop = None
        final_walk_seconds = None
        best_total_arrival = inf
        for stop, walk_time in final_list:
            if reach_stops[stop][1] < inf:
                if reach_stops[stop][2] == WALKING:
                    pre = reach_stops[stop][0]
                    if pre is None:
                        continue
                    wal = walking.walking_time_between(
                        walking.get_loc_coords(pre), destination)
                    total = reach_stops[pre][1] + wal
                    if total < best_total_arrival:
                        best_total_arrival = total
                        best_final_stop = pre
                        final_walk_seconds = wal
                else:
                    total = reach_stops[stop][1] + walk_time
                    if total < best_total_arrival:
                        best_total_arrival = total
                        best_final_stop = stop
                        final_walk_seconds = walk_time

        if best_final_stop is None:
            return {}

        arrival_time = best_total_arrival

        # If a direct walk is competitive (≤ 30 min and no slower than
        # transit + 10 s tolerance), return a walk-only result.
        dir_walking = walking.walking_time_between(start_point, destination)
        if (dir_walking is not None
                and dir_walking <= 1800
                and dir_walking + start_time < arrival_time + 10):
            return {
                '_meta': {
                    'start_walk_seconds': dir_walking,
                    'end_walk_seconds': 0,
                    'total_arrival': start_time + dir_walking,
                    'start_point': start_point,
                    'destination': destination,
                }
            }

        # ── Trace the route back from destination to origin ────────
        fastest_route = {}
        track = best_final_stop
        visited = set()
        while track is not None:
            if track in visited:
                break
            visited.add(track)
            fastest_route[track] = {
                "prev_stop": reach_stops[track][0],
                "arrival_time": reach_stops[track][1],
                "mode": int_to_name(reach_stops[track][2]),
                "journey": reach_stops[track][3],
                "day": reach_stops[track][4],
            }
            track = reach_stops[track][0]

        origin_stop = None
        for s in fastest_route:
            if fastest_route[s]["prev_stop"] is None:
                origin_stop = s
                break
        start_walk_seconds = (initial_stops.get(origin_stop, 0)
                              if origin_stop is not None else 0)

        # Enrich with human-readable metadata
        for stop, info in fastest_route.items():
            day = info.get('day')
            if day is None:
                continue
            if stop < len(day.stop_metadata):
                info['stop_name'] = day.stop_metadata[stop]
            prev = info.get('prev_stop')
            if prev is not None and prev < len(day.stop_metadata):
                info['prev_stop_name'] = day.stop_metadata[prev]
            j_id = info.get('journey')
            if j_id is not None and j_id < len(day.journey_metadata):
                info['journey_info'] = day.journey_metadata[j_id]
            if j_id is not None and j_id < len(day.journey_times):
                jt = day.journey_times[j_id]
                if jt:
                    first_stop = jt[0][0]
                    last_stop = jt[-1][0]
                    info['journey_origin'] = (
                        day.stop_metadata[first_stop]
                        if first_stop < len(day.stop_metadata)
                        else f"stop#{first_stop}")
                    journey_meta = (day.journey_metadata[j_id]
                                    if j_id < len(day.journey_metadata) else {})
                    dest_display = (journey_meta or {}).get('destination_display', '')
                    info['journey_destination'] = dest_display or (
                        day.stop_metadata[last_stop]
                        if last_stop < len(day.stop_metadata)
                        else f"stop#{last_stop}")
                if prev is not None:
                    jsi = (day.journey_stop_index[j_id]
                           if j_id < len(day.journey_stop_index) else {})
                    pos = jsi.get(prev)
                    if pos is not None:
                        info['board_departure'] = jt[pos][2]

        fastest_route['_meta'] = {
            'start_walk_seconds': start_walk_seconds,
            'end_walk_seconds': final_walk_seconds,
            'total_arrival': arrival_time,
            'start_point': start_point,
            'destination': destination,
        }
        return fastest_route

    # ── recursive RAPTOR rounds ──────────────────────────────────

    def recursive_raptor(self,
                         final_list,
                         n_transfer: int,
                         transfer_limit: int,
                         reach_stops: List[List[Any]],
                         walking: Walking,
                         switch_a: set,
                         allowed_modes: Set[int],
                         initial_walk_stops: Optional[dict] = None,
                         debug_stop_ids: Optional[Set[int]] = None
                         ):
        """Perform one RAPTOR round, then recurse until the transfer
        limit is reached or no new stops are improved.

        *switch_a* is the set of stops improved in the previous round.
        *initial_walk_stops* maps stop-int → walk_seconds for stops
        reached directly from the origin; these use a relaxed boarding
        threshold (no MIN_TRANSFER_SECONDS penalty) on the first round.

        **Round isolation** – standard RAPTOR requires that boarding
        decisions within a round are based on arrival times from the
        *previous* round, not the in-progress current round.  We
        snapshot arrival times at the start of each round and pass
        them to ``first_journey`` so that cascading improvements
        within the same round cannot enable extra transfers beyond
        the transfer-limit.
        """
        if not switch_a or n_transfer == transfer_limit or self.found:
            return
        n_transfer += 1
        switch_b: set = set()
        net = self.network

        # ── Snapshot arrival times from the previous round ────────
        # first_journey will use *prev_arrival* for boarding-eligibility
        # checks while writing improvements into *reach_stops*.
        prev_arrival = [row[1] for row in reach_stops]

        # Scan every route passing through each improved stop.
        # Process stops in ascending arrival-time order (nearest first)
        # to ensure propagation follows earliest-known states rather than
        # arbitrary set iteration order.
        ordered_switch_a = sorted(switch_a, key=lambda s: prev_arrival[s])
        for stop in ordered_switch_a:
            routes = net.stop_to_routes[stop]
            for route in routes:
                # On the very first round (n_transfer == 0), stops that
                # were reached by walking from the origin get a relaxed
                # boarding threshold — no 90 s transfer buffer.
                is_initial_walk = (
                    initial_walk_stops is not None
                    and stop in initial_walk_stops
                    and n_transfer == 0
                )
                self.first_journey(
                    net, route, stop, reach_stops, switch_b,
                    allowed_modes, is_initial_walk,
                    prev_arrival=prev_arrival,
                    debug_stop_ids=debug_stop_ids)

        # Walking transfers from newly-improved stops. Process newly
        # improved stops in earliest-arrival order so walk arrivals are
        # considered from the soonest originating stop first.
        ordered_switch_b = sorted(list(switch_b), key=lambda s: reach_stops[s][1])
        for stop in ordered_switch_b:
            if reach_stops[stop][2] == WALKING:
                pre = reach_stops[stop][0]
                if pre is None:
                    continue
                walk_stops = walking.inter_walk(stop)
                for walk_stop, _ in walk_stops.items():
                    walk_arrival = reach_stops[pre][1] + walking.walking_time_between(
                        walking.get_loc_coords(pre), walking.get_loc_coords(walk_stop))
                    if reach_stops[walk_stop][1] > walk_arrival:
                        reach_stops[walk_stop][1] = walk_arrival
                        reach_stops[walk_stop][0] = pre
                        reach_stops[walk_stop][2] = WALKING
                        switch_b.add(walk_stop)
                        # Debug: record walking transfer update
                        if debug_stop_ids is not None and walk_stop in debug_stop_ids:
                            if not hasattr(self, '_debug_events'):
                                self._debug_events = []
                            self._debug_events.append((walk_stop, 'walk_transfer', walk_arrival, pre))        
            else:
                walk_stops = walking.inter_walk(stop)
                for walk_stop, secs in walk_stops.items():
                    walk_arrival = reach_stops[stop][1] + secs
                    if reach_stops[walk_stop][1] > walk_arrival:
                        reach_stops[walk_stop][1] = walk_arrival
                        reach_stops[walk_stop][0] = stop
                        reach_stops[walk_stop][2] = WALKING
                        switch_b.add(walk_stop)
                        # Debug: record walking transfer update
                        if debug_stop_ids is not None and walk_stop in debug_stop_ids:
                            if not hasattr(self, '_debug_events'):
                                self._debug_events = []
                            self._debug_events.append((walk_stop, 'walk_transfer', walk_arrival, stop))
        for stop, _ in final_list:
            if reach_stops[stop][1] < math.inf and reach_stops[stop][2] != WALKING:
                self.found = True
                break
        self.recursive_raptor(
            final_list, n_transfer, transfer_limit, reach_stops, walking,
            switch_b, allowed_modes, initial_walk_stops,
            debug_stop_ids=debug_stop_ids,
        )

    # ── scan a route for the earliest usable journey from *stop* ──

    def first_journey(self, network, route, stop, reach_stops,
                      switch_b, allowed_modes, is_initial_walk=False,
                      prev_arrival: Optional[List[float]] = None,
                      debug_stop_ids: Optional[Set[int]] = None):
        """Find the first journey on *route* that departs from *stop*
        after the current arrival + connection buffer, then propagate
        improved arrival times to downstream stops.

        *prev_arrival* is the snapshot of arrival times from the
        previous round.  Boarding-eligibility (the departure-time
        check) is based on ``prev_arrival[stop]`` so that
        improvements written to *reach_stops* during the current
        round cannot cascade into additional boardings within the
        same round.  This is a core RAPTOR invariant that ensures
        the transfer count is accurate.

        When *is_initial_walk* is True the stop was reached by walking
        from the origin, so the 90 s transfer penalty is dropped and a
        small tolerance (INITIAL_BOARDING_TOLERANCE) is applied instead
        — allowing the passenger to board a service that departs a few
        seconds before the estimated walking arrival.

        Returns the journey-int used, or None if no usable journey was
        found.
        """
        rsd = network.route_stop_departures
        if route >= len(rsd):
            return None
        stop_deps = rsd[route].get(stop)
        if not stop_deps:
            return None

        # Use the previous-round snapshot for boarding eligibility so
        # that in-round cascading is prevented.  Fall back to the live
        # reach_stops value when no snapshot is supplied (e.g. from
        # legacy callers).
        boarding_arrival = (prev_arrival[stop]
                           if prev_arrival is not None
                           else reach_stops[stop][1])

        # We'll search for candidate departures at or after the
        # boarding_arrival time, then apply a per-journey-mode buffer
        # before accepting a departure.
        search_lower = boarding_arrival
        idx = bisect.bisect_left(stop_deps, (search_lower,))

        while idx < len(stop_deps):
            dep_time, first_journey = stop_deps[idx]
            # Determine required buffer based on the journey mode
            jmode = network.journey_type(first_journey)
            required_buffer = TRAIN_BOARD_BUFFER if jmode == TRAIN else BUS_BOARD_BUFFER
            # If this departure is earlier than arrival + required buffer,
            # skip it.  Use boarding_arrival (previous-round snapshot) for
            # round isolation.
            if dep_time < boarding_arrival + required_buffer:
                idx += 1
                continue
            
            # Otherwise this departure is acceptable (subject to mode filter)
            
            if network.journey_type(first_journey) not in allowed_modes:
                idx += 1
                continue
            journey_times = network.journey_times[first_journey]
            jsi = network.journey_stop_index[first_journey]
            start_pos = jsi.get(stop, None)
            if start_pos is None:
                idx += 1
                continue

            for subsequent_point, subsequent_a_time, _d in journey_times[start_pos + 1:]:
                if subsequent_point == stop:
                    continue
                if subsequent_a_time < reach_stops[subsequent_point][1]:
                    reach_stops[subsequent_point][1] = subsequent_a_time
                    reach_stops[subsequent_point][0] = stop
                    reach_stops[subsequent_point][2] = network.journey_type(first_journey)
                    reach_stops[subsequent_point][3] = first_journey
                    reach_stops[subsequent_point][4] = network
                    switch_b.add(subsequent_point)
                    # Debug: record journey propagation update
                    if debug_stop_ids is not None and subsequent_point in debug_stop_ids:
                        if not hasattr(self, '_debug_events'):
                            self._debug_events = []
                        self._debug_events.append((subsequent_point, 'journey_propagation', subsequent_a_time, first_journey, stop))
            return first_journey
        return None