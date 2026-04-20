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
import uuid
from typing import List, Any, Set, Optional, Tuple
from walking import Walking
from modes import WALKING, BUS, TRAIN, int_to_name, all_transit_modes, name_to_int

# Boarding buffers (seconds) applied after arrival at a stop before
# allowing boarding of a vehicle.  These replace the previous global
# MIN_TRANSFER_SECONDS / INITIAL_BOARDING_TOLERANCE constants and are
# chosen per-mode to reflect realistic boarding requirements.
BUS_BOARD_BUFFER = 180    # 3 minute for buses
TRAIN_BOARD_BUFFER = 540 # 9 minutes for trains


class RaptorRouter:
    """Round-based RAPTOR on a single MergedData network."""

    def __init__(self, network):
        self.network = network

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

        # Clear any previous debug events and run recursive rounds
        if hasattr(self, '_debug_events'):
            del self._debug_events
        self.recursive_raptor(
            -1, n_transfer_limit, reach_stops, walking,
            switch_a, allowed_modes, initial_stops,
            debug_stop_ids=debug_stop_ids,
        )

        # ── Find best final stop ─────────────────────────────────
        final_list = walking.reachable_stops(destination)
        if not final_list:
            return {}

        best_final_stop = None
        final_walk_seconds = None
        best_total_arrival = inf
        for stop, walk_time in final_list:
            if reach_stops[stop][1] < inf:
                if reach_stops[stop][2] == WALKING:
                    pre = reach_stops[stop][0]
                    if pre is None:
                        continue
                    # Guard: only treat this as an initial walking arrival when
                    # the pre-stop itself was a pure walking seed from the origin
                    # (mode == WALKING and prev_stop is None). This avoids
                    # selecting a final stop where the 'pre' was reached via a
                    # journey and then converted into a walking arrival in the
                    # same round, which can produce implausibly long final walks.
                    try:
                        if pre not in initial_stops:
                            continue
                    except Exception:
                        continue
                    wal = walking.walking_time_between(
                        walking.get_loc_coords(pre), destination)
                    # Reject implausibly large final walks (safeguard).
                    # If the walk exceeds 1 hour, skip this candidate.
                    if wal is None or wal > 3600:
                        continue
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

        # --- Log the chosen journey(s) for precise matching later ---
        try:
            # Build ordered stop list from origin -> destination
            route_entries = {k: v for k, v in fastest_route.items() if k != '_meta'}
            # Find the terminal destination (stop not referenced as prev_stop)
            all_prevs = {info['prev_stop'] for info in route_entries.values() if info['prev_stop'] is not None}
            destinations = [s for s in route_entries if s not in all_prevs]
            if not destinations:
                destinations = list(route_entries.keys())
            # Trace back to build ordered stops
            ordered = []
            stop = destinations[0]
            visited = set()
            while stop is not None and stop not in visited:
                visited.add(stop)
                ordered.append((stop, route_entries[stop]))
                stop = route_entries[stop]['prev_stop']
            ordered = list(reversed(ordered))  # now origin -> destination

            # Extract contiguous transit legs (where 'journey' is not None)
            legs = []
            cur_leg = None
            for stop_int, info in ordered:
                j_id = info.get('journey')
                if j_id is None:
                    # walking or undefined — close any current leg
                    if cur_leg is not None:
                        legs.append(cur_leg)
                        cur_leg = None
                    continue
                if cur_leg is None or cur_leg['journey_id'] != j_id:
                    if cur_leg is not None:
                        legs.append(cur_leg)
                    cur_leg = {'journey_id': j_id, 'stops': [stop_int]}
                else:
                    cur_leg['stops'].append(stop_int)
            if cur_leg is not None:
                legs.append(cur_leg)

            # Build a compact logged_journey structure
            lj = {
                'id': uuid.uuid4().hex,
                'created_at': None,
                'start_point': start_point,
                'destination_point': destination,
                'total_arrival': arrival_time,
                'legs': [],
            }
            for leg in legs:
                j_id = leg['journey_id']
                try:
                    jsi = net.journey_stop_index[j_id]
                    jt = net.journey_times[j_id]
                except Exception:
                    jsi = {}
                    jt = []
                # determine start/end positions within jt if possible
                leg_stops = leg['stops']
                start_pos = jsi.get(leg_stops[0]) if jsi else None
                end_pos = jsi.get(leg_stops[-1]) if jsi else None
                stops_slice = []
                if start_pos is not None and end_pos is not None and start_pos <= end_pos:
                    for sid, atime, dtime in jt[start_pos:end_pos + 1]:
                        stops_slice.append({'stop': sid, 'arrival': atime, 'departure': dtime})
                else:
                    # fallback: just record stop ints
                    stops_slice = [{'stop': s} for s in leg_stops]
                # Normalize journey metadata so any returned route_id
                # corresponds to the canonical route id known to the
                # merged network (via journey_to_route -> route_metadata).
                jm = None
                if j_id is not None and j_id < len(net.journey_metadata):
                    try:
                        jm = dict(net.journey_metadata[j_id]) if net.journey_metadata[j_id] else None
                    except Exception:
                        jm = net.journey_metadata[j_id]
                try:
                    if jm is not None and j_id is not None and hasattr(net, 'journey_to_route'):
                        r_int = net.journey_to_route[j_id] if j_id < len(net.journey_to_route) else None
                        if r_int is not None and r_int < len(net.route_metadata):
                            rm = net.route_metadata[r_int]
                            if isinstance(rm, dict) and rm.get('route_id'):
                                jm['route_id'] = rm.get('route_id')
                except Exception:
                    pass

                leg_meta = {
                    'journey_id': j_id,
                    'mode': net.journey_type(j_id),
                    'journey_metadata': jm,
                    'stops': stops_slice,
                }
                lj['legs'].append(leg_meta)

            # store on the merged network for later lookup
            try:
                if hasattr(net, 'logged_journeys'):
                    lj['created_at'] = None
                    net.logged_journeys[lj['id']] = lj
                    # Annotate the returned route with the logged journey id so
                    # callers (api.build_journey_plan_response) can surface
                    # it to clients. This keeps the logging side-effect local
                    # but makes the id available for subsequent geometry lookups.
                    try:
                        # Annotate the returned route with a dense route_int
                        # that can be used for direct in-memory fragment lookup.
                        route_int_for_tracks = None
                        for leg in lj.get('legs', []) or []:
                            try:
                                j_id_leg = leg.get('journey_id') if isinstance(leg, dict) else None
                                if j_id_leg is None:
                                    continue
                                if hasattr(net, 'journey_to_route') and j_id_leg < len(net.journey_to_route):
                                    r_int = net.journey_to_route[j_id_leg]
                                    if r_int is not None and int(r_int) >= 0:
                                        route_int_for_tracks = int(r_int)
                                        break
                            except Exception:
                                pass
                        if route_int_for_tracks is not None:
                            fastest_route['_route_int'] = route_int_for_tracks
                    except Exception:
                        pass
                    # Persist route geometries (if present) onto the logged
                    # journey so later geometry lookups can reconstruct
                    # a road-following polyline using OSRM when stored
                    # fragment tracks are missing.
                    try:
                        if isinstance(fastest_route, dict) and 'routeGeometries' in fastest_route:
                            lj['routeGeometries'] = fastest_route.get('routeGeometries')
                    except Exception:
                        pass
                    # Best-effort persist to DB if available
                    try:
                        import os
                        from bus_loader import BusLoader
                        db_dsn = os.environ.get('BUS_DB_DSN') or os.environ.get('BUS_DB_PATH')
                        if not db_dsn:
                            # fallback to a sensible default matching main.py
                            db_dsn = "postgresql://pguser:pgpass@127.0.0.1:5011/transport"
                        bl = BusLoader(db_dsn)
                        bl.insert_logged_journey(lj)
                    except Exception:
                        # swallow DB persistence failures — logging already in-memory
                        pass
            except Exception:
                pass
        except Exception:
            # best-effort logging — do not break routing on any error
            pass

        return fastest_route

    # ── recursive RAPTOR rounds ──────────────────────────────────

    def recursive_raptor(self,
                         n_transfer: int,
                         transfer_limit: int,
                         reach_stops: List[List[Any]],
                         walking: Walking,
                         switch_a: set,
                         allowed_modes: Set[int],
                         initial_walk_stops: Optional[dict] = None,
                         debug_stop_ids: Optional[Set[int]] = None):
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
        if not switch_a or n_transfer == transfer_limit:
            return
        n_transfer += 1
        switch_b: set = set()
        net = self.network

        # ── Snapshot arrival times from the previous round ────────
        # first_journey will use *prev_arrival* for boarding-eligibility
        # checks while writing improvements into *reach_stops*.
        prev_arrival = [row[1] for row in reach_stops]
        # Snapshot previous-round mode per stop so walking-transfer expansion
        # can avoid re-expanding from stops that were already walking-reached.
        prev_mode = [row[2] for row in reach_stops]

        # Scan every route passing through each improved stop.
        # Process stops in ascending arrival-time order (nearest first)
        # to ensure propagation follows earliest-known states rather than
        # arbitrary set iteration order.
        ordered_switch_a = sorted(switch_a, key=lambda s: prev_arrival[s])
        for stop in ordered_switch_a:
            routes = net.stop_to_routes[stop]
            # If this stop was seeded from the origin (initial_walk_stops)
            # but has been improved by a journey in the current round,
            # do not allow boarding here during round 0. This prevents
            # a seeded stop that was later reached earlier by vehicle
            # propagation from being used as a zero-transfer boarding
            # point within the same round.
            if (initial_walk_stops is not None
                    and stop in initial_walk_stops
                    and n_transfer == 0
                    and (reach_stops[stop][2] != WALKING or reach_stops[stop][0] is not None)):
                # skip processing this stop for boarding in round 0
                continue
            for route in routes:
                # On the very first round (n_transfer == 0), stops that
                # were reached by walking from the origin get a relaxed
                # boarding threshold — no 90 s transfer buffer.
                # Only treat a stop as an "initial walk" if it was seeded
                # from the origin and has not been improved by any journey
                # in the current round. This prevents a seeded stop that
                # was later reached earlier by a vehicle from being
                # considered a zero-transfer boarding point.
                is_initial_walk = (
                    initial_walk_stops is not None
                    and stop in initial_walk_stops
                    and n_transfer == 0
                    and reach_stops[stop][2] == WALKING
                    and reach_stops[stop][0] is None
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
            # Snapshot rule: do not chain walking expansion from a stop that
            # was already WALKING in the previous round, or has already
            # become WALKING earlier in this round.
            if prev_mode[stop] == WALKING or reach_stops[stop][2] == WALKING:
                continue
            # For any improved stop, propagate walking transfers from the stop's
            # arrival time. Use the stop itself as the origin of the subsequent
            # walking transfer (not its predecessor). This avoids creating
            # back-and-forth walk links that reference earlier stops and can
            # produce large, spurious walking legs.
            walk_stops = walking.inter_walk(stop)
            for walk_stop, secs in walk_stops.items():
                try:
                    # Prefer exact walking time between stop and walk_stop when available
                    wsecs = walking.walking_time_between(
                        walking.get_loc_coords(stop), walking.get_loc_coords(walk_stop))
                    if wsecs is None:
                        wsecs = secs
                except Exception:
                    wsecs = secs
                walk_arrival = reach_stops[stop][1] + wsecs
                if reach_stops[walk_stop][1] > walk_arrival:
                    reach_stops[walk_stop][1] = walk_arrival
                    reach_stops[walk_stop][0] = stop
                    reach_stops[walk_stop][2] = WALKING
                    # Clear any stale journey/day pointers when a walking
                    # arrival overwrites a previous journey-based arrival.
                    reach_stops[walk_stop][3] = None
                    reach_stops[walk_stop][4] = None
                    switch_b.add(walk_stop)
                    # Debug: record walking transfer update
                    if debug_stop_ids is not None and walk_stop in debug_stop_ids:
                        if not hasattr(self, '_debug_events'):
                            self._debug_events = []
                        self._debug_events.append((walk_stop, 'walk_transfer', walk_arrival, stop))

        self.recursive_raptor(
            n_transfer, transfer_limit, reach_stops, walking,
            switch_b, allowed_modes,
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
        stop_deps = network.get_route_stop_departures(route).get(stop)
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