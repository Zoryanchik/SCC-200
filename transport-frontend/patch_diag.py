import re

with open('/Users/lty/Desktop/scc200/puppy/SCC-200/transport-backend/api.py', 'r') as f:
    text = f.read()

old_diag = """    # 2) destination check (prefer feed ATCO)
    dest_matches = []
    for j_id, jm in line_candidates:
        try:
            jt = merged.journey_times[j_id]
            dest_stop_int = jt[-1][0]
            journey_dest_atco = merged.get_atco_code(dest_stop_int)
        except Exception:
            journey_dest_atco = None
        if feed_destination_atco:
            if journey_dest_atco and str(journey_dest_atco).strip() == str(feed_destination_atco).strip():
                dest_matches.append((j_id, jm))
        else:
            # build stop name map lazily
            pass

    if feed_destination_atco and not dest_matches:
        reasons.append("destination_atco_mismatch")
        return reasons

    if not feed_destination_atco and dest_q:
        # check if any journey's final ATCO in line_candidates matches
        stop_name_map = {}
        for si, sname in enumerate(merged.stop_metadata or []):
            if sname:
                stop_name_map.setdefault(str(sname).strip().lower(), []).append(si)
        candidate_dest_atcos = set()
        for k, v in stop_name_map.items():
            if dest_q in k or k in dest_q:
                for si in v:
                    candidate_dest_atcos.add(merged.get_atco_code(si))
        if candidate_dest_atcos:
            any_match = False
            for j_id, jm in line_candidates:
                try:
                    jt = merged.journey_times[j_id]
                    journey_dest_atco = merged.get_atco_code(jt[-1][0])
                    if journey_dest_atco in candidate_dest_atcos:
                        any_match = True
                except Exception:
                    pass
            if not any_match:
                reasons.append("destination_atco_mismatch")
                return reasons

    # 3) operator check
    if operator_ref:
        matched_op = False
        for j_id, jm in line_candidates:
            op_noc = str(jm.get("operator_national_code") or "").strip()
            # Prefer operator_national_code (added by loader) for strict matching
            if op_noc and op_noc == str(operator_ref).strip():
                matched_op = True
                break
            sc = str(jm.get("service_code") or "").strip()
            if sc and sc == str(operator_ref).strip():
                matched_op = True
                break
        if not matched_op:
            reasons.append("operator_mismatch")
            return reasons

    # 4) origin ATCO/time checks
    origin_ok = False
    for j_id, jm in line_candidates:
        try:
            jt = merged.journey_times[j_id]
            start_dep = jt[0][2]
            first_stop_int = jt[0][0]
            journey_first_atco = merged.get_atco_code(first_stop_int)
        except Exception:
            continue
        if feed_origin_atco:
            if journey_first_atco and str(journey_first_atco).strip() == str(feed_origin_atco).strip():
                origin_ok = True
                break
        else:
            try:
                nearby = walking.reachable_stops((lat_v, lon_v))
                if not nearby:
                    continue
                nearest_stop_int, walk_secs = nearby[0]
                nearest_atco = merged.get_atco_code(nearest_stop_int)
                if nearest_atco and journey_first_atco and nearest_atco == journey_first_atco:
                    origin_ok = True
                    break
            except Exception:
                pass

    if not origin_ok:
        reasons.append("origin_atco_mismatch_or_unreachable")
        return reasons"""

new_diag = """    # 2 & 4 combined) endpoint check (relax rule: either dest OR origin must match)
    endpoint_ok = False
    
    stop_name_map = {}
    if not feed_destination_atco and dest_q:
        for si, sname in enumerate(merged.stop_metadata or []):
            if sname:
                stop_name_map.setdefault(str(sname).strip().lower(), []).append(si)

    for j_id, jm in line_candidates:
        dest_match_ok = False
        origin_match_ok = False
        
        try:
            jt = merged.journey_times[j_id]
            dest_stop_int = jt[-1][0]
            journey_dest_atco = merged.get_atco_code(dest_stop_int)
            first_stop_int = jt[0][0]
            journey_first_atco = merged.get_atco_code(first_stop_int)
        except Exception:
            continue

        if feed_destination_atco:
            if journey_dest_atco and str(journey_dest_atco).strip() == str(feed_destination_atco).strip():
                dest_match_ok = True
        else:
            candidate_dest_atcos = set()
            if dest_q:
                for k, v in stop_name_map.items():
                    if dest_q in k or k in dest_q:
                        for si in v:
                            candidate_dest_atcos.add(merged.get_atco_code(si))
            if candidate_dest_atcos and journey_dest_atco and journey_dest_atco in candidate_dest_atcos:
                dest_match_ok = True

        if feed_origin_atco:
            if journey_first_atco and str(journey_first_atco).strip() == str(feed_origin_atco).strip():
                origin_match_ok = True
        else:
            try:
                nearby = walking.reachable_stops((lat_v, lon_v))
                if nearby:
                    nearest_stop_int, walk_secs = nearby[0]
                    nearest_atco = merged.get_atco_code(nearest_stop_int)
                    if nearest_atco and journey_first_atco and nearest_atco == journey_first_atco:
                        origin_match_ok = True
            except Exception:
                pass

        if dest_match_ok or origin_match_ok:
            endpoint_ok = True
            break
            
    if not endpoint_ok:
        reasons.append("endpoint_mismatch_or_unreachable")
        return reasons

    # 3) operator check
    if operator_ref:
        matched_op = False
        for j_id, jm in line_candidates:
            op_noc = str(jm.get("operator_national_code") or "").strip()
            # Prefer operator_national_code (added by loader) for strict matching
            if op_noc and op_noc == str(operator_ref).strip():
                matched_op = True
                break
            sc = str(jm.get("service_code") or "").strip()
            if sc and sc == str(operator_ref).strip():
                matched_op = True
                break
        if not matched_op:
            reasons.append("operator_mismatch")
            return reasons"""

text = text.replace(old_diag, new_diag)

with open('/Users/lty/Desktop/scc200/puppy/SCC-200/transport-backend/api.py', 'w') as f:
    f.write(text)

