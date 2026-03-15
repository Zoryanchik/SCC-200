        # Destination matching: prefer explicit feed-provided ATCO when present.
        try:
            jt = merged.journey_times[j_id]
            if not jt:
                continue
            dest_stop_int = jt[-1][0]
            journey_dest_atco = merged.get_atco_code(dest_stop_int)
        except Exception:
            continue

        if feed_destination_atco:
            # Feed supplied an ATCO code — require exact equality
            if not journey_dest_atco or str(journey_dest_atco).strip() != str(feed_destination_atco).strip():
                continue
        else:
            # Fall back to resolving free-text destination to ATCO(s)
            candidate_dest_atcos = set()
            if dest_q:
                for sname_key, sidx_list in stop_name_map.items():
                    if dest_q in sname_key or sname_key in dest_q:
                        for si in sidx_list:
                            atco = merged.get_atco_code(si)
                            if atco:
                                candidate_dest_atcos.add(atco)
            # strict mode: require feed destination to resolve to an ATCO and match
            if not candidate_dest_atcos:
                continue
            if not journey_dest_atco or journey_dest_atco not in candidate_dest_atcos:
                continue

        # Strict operator/service match: require journey's recorded service_code
        # to match the feed-provided operator_ref. If the journey metadata
        # lacks a service_code, attempt to derive from line_name prefix if present.
        if operator_ref:
            # Prefer the authoritative National Operator Code (if present)
            # which maps to <Operators><Operator><NationalOperatorCode>
            op_noc = jmeta.get("operator_national_code") or None
            svc = None
            if op_noc:
                svc = str(op_noc).strip()
            else:
                # Fall back to service_code (older datasets)
                sc = jmeta.get("service_code") or ""
                if sc:
                    svc = str(sc).strip()
                else:
                    # line_name may be prefixed with service_code:line when loaded
                    ln = (jmeta.get("line_name") or "")
                    if ":" in ln:
                        svc = ln.split(":")[0]
            if not svc or svc.strip() != str(operator_ref).strip():
                # operator mismatch — in strict mode, reject candidate
                continue
        try:
            jt = merged.journey_times[j_id]
            if not jt:
                continue
            start_dep = jt[0][2]   # departure time of first stop
            # Safe end_arr: prefer arrival, fallback to departure
            end_arr = jt[-1][1] if jt[-1][1] is not None else jt[-1][2]
        except Exception:
            continue

        # Day guard: allow journeys from yesterday and today.
        # The merged timetable includes journeys with day-offsets
        # (previous day encoded as negative seconds). Restrict to
        # journeys whose scheduled start_dep falls within the range
        # [-86400, 86400) so we consider yesterday and today only.
        try:
            if start_dep is None:
                continue
            if start_dep < -86400 or start_dep >= 86400:
                continue
        except Exception:
            continue

        r_int = merged.journey_to_route[j_id] if j_id < len(merged.journey_to_route) else -1
        if r_int < 0:
            continue

    # Quick spatial pre-filter: ensure the candidate route/track has
    # at least one vertex within MATCH_MAX_TRACK_DIST_M of the
    # vehicle position. This prevents considering journeys in a
    # different town that happen to share the same short line
    # number / free-text destination.
        try:
            nearby_min = 1e9
            # Prefer cached route_tracks when present
            if r_int < len(merged.route_tracks) and merged.route_tracks[r_int]:
                pts = merged.route_tracks[r_int]
                for px, py in pts:
                    try:
                        d = _hav(lat_v, lon_v, px, py)
                        if d < nearby_min:
                            nearby_min = d
                    except Exception:
                        continue
            else:
                # Fall back to route_stops coordinates
                route_stops = merged.route_stops[r_int] if r_int < len(merged.route_stops) else []
                for sid in route_stops:
                    try:
                        coords = walking.get_loc_coords(sid)
                        if coords and len(coords) == 2:
                            d = _hav(lat_v, lon_v, coords[0], coords[1])
                            if d < nearby_min:
                                nearby_min = d
                    except Exception:
                        continue
            if nearby_min > MATCH_MAX_TRACK_DIST_M:
                # route too far from vehicle — skip candidate
                continue
        except Exception:
            # If anything goes wrong with the prefilter, don't block
            # matching — fall back to the original permissive behaviour.
            pass

        # Ensure the vehicle is near a stop that actually belongs to the
        # candidate journey. This helps avoid matching to a journey whose
        # geometry happens to pass nearby but does not serve the stop the
        # vehicle is at.
        try:
            nearby = walking.reachable_stops((lat_v, lon_v))
            if nearby:
                nearest_stop_int, walk_secs = nearby[0]
                jsi = merged.journey_stop_index[j_id] if j_id < len(merged.journey_stop_index) else {}
                if nearest_stop_int not in jsi:
                    # vehicle's nearest stop is not served by this journey
                    continue
        except Exception:
            # If walking lookup fails, don't block — fall back to other checks
            pass

        # ── Origin checks (strict) ──
        # Origin checks: prefer explicit feed-provided origin ATCO when present.
        try:
            first_stop_int = jt[0][0]
            journey_first_atco = merged.get_atco_code(first_stop_int)
        except Exception:
            continue

        if feed_origin_atco:
            # Feed supplied an origin ATCO — require exact equality
            if not journey_first_atco or str(journey_first_atco).strip() != str(feed_origin_atco).strip():
                continue
        else:
            # Determine nearest reachable stop(s) to vehicle location and compare
            try:
                nearby = walking.reachable_stops((lat_v, lon_v))
                if not nearby:
                    # strict mode: if we cannot determine the vehicle's nearby stop, reject
                    continue
                nearest_stop_int, walk_secs = nearby[0]
                nearest_atco = merged.get_atco_code(nearest_stop_int)
                if not nearest_atco or not journey_first_atco or nearest_atco != journey_first_atco:
                    # vehicle is not at/near the scheduled origin stop
                    continue
            except Exception:
                # If walking lookup failed, reject (strict requirement)
                continue

        # ── OriginAimedDepartureTime gate (optional) ──
        # Use the feed-provided OriginAimedDepartureTime when present. Feeds
        # may include a timezone offset; if provided we incorporate that
        # offset when aligning the feed time-of-day to the scheduled start
        # (also try ±1 day shifts). This is stronger than the previous
        # naive comparison and avoids ±1h heuristics.
