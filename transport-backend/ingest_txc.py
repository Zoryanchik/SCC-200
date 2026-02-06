import os
import xml.etree.ElementTree as ET
from typing import Dict, List, Tuple, Optional

from db import get_connection
from schema import ensure_schema
from time_utils import parse_iso8601_duration, seconds_since_midnight


def _find_text(elem: ET.Element, tag: str) -> Optional[str]:
    node = elem.find(f".//{{*}}{tag}")
    if node is not None and node.text:
        return node.text.strip()
    return None


def _parse_runtime_seconds(link: ET.Element) -> int:
    runtime = link.find(".//{*}RunTime")
    if runtime is None:
        return 0
    scheduled = runtime.find(".//{*}ScheduledDuration")
    if scheduled is not None and scheduled.text:
        return parse_iso8601_duration(scheduled.text.strip())
    if runtime.text:
        return parse_iso8601_duration(runtime.text.strip())
    return 0


def _load_stop_points(root: ET.Element) -> Dict[str, str]:
    stop_points: Dict[str, str] = {}
    # StopPoint elements with AtcoCode
    for sp in root.findall(".//{*}StopPoint"):
        atco = _find_text(sp, "AtcoCode")
        sp_id = sp.get("id") or atco
        if atco and sp_id:
            stop_points[sp_id] = atco

    # AnnotatedStopPointRef elements used in BODS exports
    for sp in root.findall(".//{*}AnnotatedStopPointRef"):
        sp_ref = _find_text(sp, "StopPointRef")
        if sp_ref:
            stop_points[sp_ref] = sp_ref
    return stop_points


def _load_journey_pattern_sections(root: ET.Element) -> Dict[str, Tuple[List[str], List[int]]]:
    sections: Dict[str, Tuple[List[str], List[int]]] = {}
    for section in root.findall(".//{*}JourneyPatternSection"):
        section_id = section.get("id")
        if not section_id:
            continue
        stops: List[str] = []
        runtimes: List[int] = []
        for link in section.findall(".//{*}JourneyPatternTimingLink"):
            from_ref = _find_text(link, "StopPointRef")
            to_ref = None
            from_node = link.find(".//{*}From")
            to_node = link.find(".//{*}To")
            if from_node is not None:
                from_ref = _find_text(from_node, "StopPointRef") or from_ref
            if to_node is not None:
                to_ref = _find_text(to_node, "StopPointRef")
            if not from_ref or not to_ref:
                continue
            if not stops:
                stops.append(from_ref)
            stops.append(to_ref)
            runtimes.append(_parse_runtime_seconds(link))
        if stops:
            sections[section_id] = (stops, runtimes)
    return sections


def _load_journey_patterns(root: ET.Element, sections: Dict[str, Tuple[List[str], List[int]]]) -> Dict[str, Tuple[List[str], List[int]]]:
    patterns: Dict[str, Tuple[List[str], List[int]]] = {}
    for pattern in root.findall(".//{*}JourneyPattern"):
        pattern_id = pattern.get("id")
        if not pattern_id:
            continue
        stops: List[str] = []
        runtimes: List[int] = []
        # Support both JourneyPatternSectionRef elements and JourneyPatternSectionRefs text
        refs = []
        for section_ref in pattern.findall(".//{*}JourneyPatternSectionRef"):
            if section_ref.text:
                refs.append(section_ref.text.strip())

        if not refs:
            refs_node = pattern.find(".//{*}JourneyPatternSectionRefs")
            if refs_node is not None and refs_node.text:
                refs = [ref for ref in refs_node.text.strip().split() if ref]

        for ref in refs:
            if ref not in sections:
                continue
            section_stops, section_runtimes = sections[ref]
            if not stops:
                stops = list(section_stops)
            else:
                stops.extend(section_stops[1:])
            runtimes.extend(section_runtimes)
        if stops:
            patterns[pattern_id] = (stops, runtimes)
    return patterns


def _load_lines(root: ET.Element) -> Dict[str, Dict[str, Optional[str]]]:
    lines: Dict[str, Dict[str, Optional[str]]] = {}
    for line in root.findall(".//{*}Line"):
        line_id = line.get("id") or _find_text(line, "LineRef") or _find_text(line, "LineName")
        if not line_id:
            continue
        lines[line_id] = {
            "route_code": line_id,
            "line_name": _find_text(line, "LineName"),
            "operator_code": _find_text(line, "OperatorRef"),
        }
    return lines


def ingest_txc_file(xml_path: str) -> int:
    ensure_schema()
    tree = ET.parse(xml_path)
    root = tree.getroot()

    stop_points = _load_stop_points(root)
    sections = _load_journey_pattern_sections(root)
    patterns = _load_journey_patterns(root, sections)
    lines = _load_lines(root)

    conn = get_connection()
    inserted_journeys = 0
    try:
        with conn:
            with conn.cursor() as cur:
                for vj in root.findall(".//{*}VehicleJourney"):
                    journey_code = _find_text(vj, "VehicleJourneyCode") or vj.get("id")
                    pattern_ref = _find_text(vj, "JourneyPatternRef")
                    line_ref = _find_text(vj, "LineRef") or _find_text(vj, "ServiceRef")
                    dep_time_str = _find_text(vj, "DepartureTime")

                    if not journey_code or not pattern_ref or pattern_ref not in patterns:
                        continue

                    stops, runtimes = patterns[pattern_ref]
                    if not stops:
                        continue

                    route_meta = lines.get(line_ref, {"route_code": line_ref, "line_name": None, "operator_code": None})
                    route_code = route_meta.get("route_code") or line_ref or pattern_ref

                    cur.execute(
                        """
                        INSERT INTO routes (route_code, mode, operator_code, line_name)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (route_code, mode) DO UPDATE SET
                            operator_code = EXCLUDED.operator_code,
                            line_name = EXCLUDED.line_name
                        RETURNING id
                        """,
                        (route_code, "bus", route_meta.get("operator_code"), route_meta.get("line_name")),
                    )
                    route_id = cur.fetchone()[0]

                    dep_time = seconds_since_midnight(dep_time_str) if dep_time_str else None
                    cur.execute(
                        """
                        INSERT INTO journeys (route_id, journey_code, departure_time)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (journey_code) DO UPDATE SET
                            route_id = EXCLUDED.route_id,
                            departure_time = EXCLUDED.departure_time
                        RETURNING id
                        """,
                        (route_id, journey_code, dep_time),
                    )
                    journey_id = cur.fetchone()[0]

                    # Insert route stops and journey times
                    time_cursor = dep_time or 0
                    for idx, stop_ref in enumerate(stops):
                        stop_code = stop_points.get(stop_ref, stop_ref)
                        cur.execute(
                            """
                            INSERT INTO route_stops (route_id, stop_code, stop_sequence)
                            VALUES (%s, %s, %s)
                            ON CONFLICT (route_id, stop_sequence) DO NOTHING
                            """,
                            (route_id, stop_code, idx),
                        )
                        cur.execute(
                            """
                            INSERT INTO journey_times (journey_id, stop_code, stop_sequence, arrival_time)
                            VALUES (%s, %s, %s, %s)
                            ON CONFLICT (journey_id, stop_sequence) DO UPDATE SET
                                stop_code = EXCLUDED.stop_code,
                                arrival_time = EXCLUDED.arrival_time
                            """,
                            (journey_id, stop_code, idx, time_cursor),
                        )
                        if idx < len(runtimes):
                            time_cursor += runtimes[idx]

                    inserted_journeys += 1
    finally:
        conn.close()

    return inserted_journeys


def ingest_txc_dir(dir_path: str) -> int:
    total = 0
    for name in os.listdir(dir_path):
        if not name.lower().endswith(".xml"):
            continue
        total += ingest_txc_file(os.path.join(dir_path, name))
    return total
