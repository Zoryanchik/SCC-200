import re
import ssl
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import List, Tuple, Iterable, Optional


def _parse_iso_duration(s: str) -> Optional[int]:
    """Parse ISO 8601 duration string to seconds (positive = late, negative = early).

    Examples: 'PT2M30S' → 150, '-PT1M' → -60, 'PT0S' → 0.
    Returns None if the string cannot be parsed.
    """
    if not s:
        return None
    sign = -1 if s.startswith('-') else 1
    s = s.lstrip('-')
    m = re.match(r'P(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?', s)
    if not m or not any(m.groups()):
        return None
    h = int(m.group(1) or 0)
    mins = int(m.group(2) or 0)
    secs = float(m.group(3) or 0)
    return sign * int(h * 3600 + mins * 60 + secs)


def _parse_iso_dt(s: str) -> Optional[datetime]:
    """Parse ISO 8601 datetime string to a timezone-aware datetime."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace('Z', '+00:00'))
    except Exception:
        return None



class BusLive:
    """Fetch live vehicle positions from one or more SCCI feeds.

    get_bus_live(lat, lon, urls=None, lat_tol=0.01, lon_tol=0.01)

    Returns a list of tuples: (line_ref, destination_name, latitude, longitude, operator_name)
    Only vehicleactivity elements whose latitude/longitude fall inside the
    specified box around (lat, lon) are included.
    """

    DEFAULT_URLS = [
        "https://transport.scc.lancs.ac.uk/bus/live/SCCU",
        "https://transport.scc.lancs.ac.uk/bus/live/ARCT",
        "https://transport.scc.lancs.ac.uk/bus/live/BLAC",
        "https://transport.scc.lancs.ac.uk/bus/live/KLCO",
        "https://transport.scc.lancs.ac.uk/bus/live/SCMY",
        "https://transport.scc.lancs.ac.uk/bus/live/NUTT",
    ]

    # Mapping of operator codes to full names
    OPERATOR_NAMES = {
        "ARCT": "Archway Travel",
        "BLAC": "Blackpool Transport",
        "KLCO": "Kirkby Lonsdale Coach Hire",
        "SCCU": "Stagecoach Cumbria & North Lancashire",
        "SCMY": "Stagecoach Merseyside & South Lancashire",
        "NUTT": "Transpora North West",
    }

    def __init__(self, urls: Iterable[str] = None, timeout: Optional[float] = None):
        self.urls = list(urls) if urls else list(self.DEFAULT_URLS)
        # timeout=None means no explicit timeout (block until response)
        self.timeout = timeout

    def _fetch_xml(self, url: str) -> ET.Element:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        if self.timeout is None:
            with urllib.request.urlopen(url, context=ctx) as resp:
                data = resp.read()
        else:
            with urllib.request.urlopen(url, timeout=self.timeout, context=ctx) as resp:
                data = resp.read()
        # parse and return root element
        return ET.fromstring(data)

    def _get_text(self, el: ET.Element, tag: str) -> str:
        c = el.find(tag)
        if c is None:
            # try lowercase tag name (some feeds vary)
            for child in el:
                if child.tag.lower().endswith(tag.lower()):
                    return (child.text or '').strip()
            return ''
        return (c.text or '').strip()

    def get_bus_live(self, lat: float, lon: float, urls: Iterable[str] = None,
                     lat_tol: float = 0.01, lon_tol: float = 0.01) -> List[dict]:
        """Return nearby live vehicles.

        Each element is a dict with at least:
            line_ref, destination_name, lat, lon, operator_name, delay_seconds

        and additional SIRI fields when available:
            vehicle_ref, bearing, direction, origin_ref, origin_name,
            destination_ref, journey_ref, aimed_departure_time

        ``delay_seconds`` is an int (positive = late, negative = early) or
        ``None`` when no timing data is available in the feed.

        The returned dicts also support tuple-style unpacking of the first
        six fields for backward compatibility::

            for rec in results:
                line_ref = rec["line_ref"]
                # or destructure the legacy tuple:
                line_ref, dest, lat_v, lon_v, op, delay = rec.as_tuple()

        lat, lon are the centre point; lat_tol/lon_tol define the half-widths of
        the allowed rectangle.
        """
        lat_min = lat - lat_tol
        lat_max = lat + lat_tol
        lon_min = lon - lon_tol
        lon_max = lon + lon_tol

        urls_to_use = list(urls) if urls else self.urls
        results: List[dict] = []

        for url in urls_to_use:
            try:
                root = self._fetch_xml(url)
            except Exception:
                # ignore failures for individual feeds
                continue

            # find all vehicleactivity elements (case-insensitive match).
            # SIRI feeds place useful fields under MonitoredVehicleJourney and
            # VehicleLocation elements (namespaced). Handle nested structure.
            for va in root.findall('.//'):
                tag = va.tag
                if not isinstance(tag, str):
                    continue
                if tag.lower().endswith('vehicleactivity'):
                    # find the MonitoredVehicleJourney element inside this VehicleActivity
                    mvj = None
                    for child in va.iter():
                        if isinstance(child.tag, str) and child.tag.lower().endswith('monitoredvehiclejourney'):
                            mvj = child
                            break
                    if mvj is None:
                        continue

                    # line and destination may be in different tags (LineRef, PublishedLineName, DestinationName)
                    line_ref = (self._get_text(mvj, 'lineref') or
                                self._get_text(mvj, 'LineRef') or
                                self._get_text(mvj, 'publishedlinename') or
                                self._get_text(mvj, 'PublishedLineName'))

                    dest = (self._get_text(mvj, 'destinationname') or
                            self._get_text(mvj, 'DestinationName') or
                            self._get_text(mvj, 'destinationref') or
                            self._get_text(mvj, 'DestinationRef'))

                    # parse operator reference and map to full name
                    operator_ref = (self._get_text(mvj, 'operatorref') or
                                    self._get_text(mvj, 'OperatorRef'))
                    operator_name = self.OPERATOR_NAMES.get(operator_ref, operator_ref or 'Unknown')

                    # --- extra SIRI fields for richer live data -----
                    direction = (self._get_text(mvj, 'directionref') or
                                 self._get_text(mvj, 'DirectionRef'))

                    vehicle_ref = (self._get_text(mvj, 'vehicleref') or
                                   self._get_text(mvj, 'VehicleRef'))

                    origin_ref = (self._get_text(mvj, 'originref') or
                                  self._get_text(mvj, 'OriginRef'))
                    origin_name = (self._get_text(mvj, 'originname') or
                                   self._get_text(mvj, 'OriginName'))
                    destination_ref = (self._get_text(mvj, 'destinationref') or
                                       self._get_text(mvj, 'DestinationRef'))

                    origin_aimed_dep = (self._get_text(mvj, 'originaimeddeparturetime') or
                                        self._get_text(mvj, 'OriginAimedDepartureTime'))

                    # FramedVehicleJourneyRef contains DataFrameRef + DatedVehicleJourneyRef
                    journey_ref = ''
                    for child in mvj.iter():
                        if isinstance(child.tag, str) and child.tag.lower().endswith('framedvehiclejourneyref'):
                            journey_ref = (self._get_text(child, 'datedvehiclejourneyref') or
                                           self._get_text(child, 'DatedVehicleJourneyRef'))
                            break

                    # vehicle location may be nested under VehicleLocation element
                    lat_s = lon_s = bearing_s = ''
                    vl = None
                    for child in mvj.iter():
                        if isinstance(child.tag, str) and child.tag.lower().endswith('vehiclelocation'):
                            vl = child
                            break
                    if vl is not None:
                        lon_s = self._get_text(vl, 'longitude') or self._get_text(vl, 'Longitude')
                        lat_s = self._get_text(vl, 'latitude') or self._get_text(vl, 'Latitude')
                    else:
                        # fallback: look for latitude/longitude anywhere under mvj
                        lat_s = (self._get_text(mvj, 'latitude') or self._get_text(mvj, 'Latitude'))
                        lon_s = (self._get_text(mvj, 'longitude') or self._get_text(mvj, 'Longitude'))

                    # Bearing may be under VehicleLocation or directly under MVJ
                    bearing_s = (self._get_text(mvj, 'bearing') or
                                 self._get_text(mvj, 'Bearing'))
                    if not bearing_s and vl is not None:
                        bearing_s = (self._get_text(vl, 'bearing') or
                                     self._get_text(vl, 'Bearing'))

                    try:
                        lat_v = float(lat_s)
                        lon_v = float(lon_s)
                    except Exception:
                        continue

                    bearing: Optional[float] = None
                    if bearing_s:
                        try:
                            bearing = float(bearing_s)
                        except Exception:
                            pass

                    # --- delay extraction -----------------------------------
                    # Strategy 1: explicit <Delay> ISO 8601 duration element
                    delay_seconds: Optional[int] = None
                    delay_raw = (self._get_text(mvj, 'delay') or
                                 self._get_text(mvj, 'Delay'))
                    if delay_raw:
                        delay_seconds = _parse_iso_duration(delay_raw)

                    # Strategy 2: difference between ExpectedDeparture and AimedDeparture
                    if delay_seconds is None:
                        aimed_raw = (self._get_text(mvj, 'aimeddeparturetime') or
                                     self._get_text(mvj, 'AimedDepartureTime'))
                        expected_raw = (self._get_text(mvj, 'expecteddeparturetime') or
                                        self._get_text(mvj, 'ExpectedDepartureTime'))
                        if not aimed_raw or not expected_raw:
                            # try AimedArrivalTime / ExpectedArrivalTime as fallback
                            aimed_raw = (self._get_text(mvj, 'aimedarrivaltime') or
                                         self._get_text(mvj, 'AimedArrivalTime'))
                            expected_raw = (self._get_text(mvj, 'expectedarrivaltime') or
                                            self._get_text(mvj, 'ExpectedArrivalTime'))
                        aimed_dt = _parse_iso_dt(aimed_raw)
                        expected_dt = _parse_iso_dt(expected_raw)
                        if aimed_dt and expected_dt:
                            diff = (expected_dt - aimed_dt).total_seconds()
                            delay_seconds = int(diff)
                    # --------------------------------------------------------

                    if lat_min <= lat_v <= lat_max and lon_min <= lon_v <= lon_max:
                        results.append({
                            "line_ref": line_ref,
                            "destination": dest,
                            "lat": lat_v,
                            "lon": lon_v,
                            # Human-readable operator name for display
                            "operator": operator_name,
                            # Operator reference code from the SIRI feed (e.g. 'SCCU')
                            "operator_ref": operator_ref or None,
                            "delay_seconds": delay_seconds,
                            # --- rich SIRI fields for route display ---
                            "vehicle_ref": vehicle_ref or None,
                            "bearing": bearing,
                            "direction": direction or None,
                            "origin_ref": origin_ref or None,
                            "origin_name": origin_name or None,
                            "destination_ref": destination_ref or None,
                            "journey_ref": journey_ref or None,
                            "aimed_departure_time": origin_aimed_dep or None,
                        })

        return results


def get_bus_live(lat, lon, urls=None, lat_tol=0.01, lon_tol=0.01, timeout=10):
    """Convenience wrapper for BusLive.get_bus_live."""
    bl = BusLive(urls=urls, timeout=timeout)
    return bl.get_bus_live(lat, lon, urls=urls, lat_tol=lat_tol, lon_tol=lon_tol)


def main():
    """Simple CLI to query nearby live buses.

    Usage: python bus_live.py [--lat LAT] [--lon LON] [--timeout SEC] [--limit N]
    """
    import argparse

    parser = argparse.ArgumentParser(description="Query live bus feeds for nearby vehicles")
    parser.add_argument("--lat", type=float, default=None, help="Center latitude (will prompt if not provided)")
    parser.add_argument("--lon", type=float, default=None, help="Center longitude (will prompt if not provided)")
    parser.add_argument("--timeout", type=float, default=None, help="HTTP timeout per feed (s); omit for no timeout")
    parser.add_argument("--limit", type=int, default=None, help="Max results to show (omit for unlimited)")
    parser.add_argument("--urls", nargs="*", help="Optional list of feed URLs to query (overrides defaults)")

    args = parser.parse_args()

    # If lat/lon not provided on the command line, prompt the user.
    if args.lat is None:
        try:
            args.lat = float(input("Enter center latitude (e.g. 54.046): ").strip())
        except Exception:
            print("Invalid latitude input - exiting.")
            return
    if args.lon is None:
        try:
            args.lon = float(input("Enter center longitude (e.g. -2.798): ").strip())
        except Exception:
            print("Invalid longitude input - exiting.")
            return

    bl = BusLive(urls=args.urls if args.urls else None, timeout=args.timeout)
    to_print_timeout = f"{args.timeout}s" if args.timeout is not None else "no timeout"
    print(f"Querying {len(bl.urls)} feeds with timeout={to_print_timeout} for point ({args.lat}, {args.lon})")
    try:
        results = bl.get_bus_live(args.lat, args.lon)
    except Exception as e:
        print("Error fetching live data:", e)
        return

    print(f"Found {len(results)} vehicles within tolerance")
    display_results = results if args.limit is None else results[: args.limit]
    for i, rec in enumerate(display_results):
        line = rec["line_ref"]
        dest = rec["destination"]
        lat = rec["lat"]
        lon = rec["lon"]
        operator = rec["operator"]
        delay_s = rec["delay_seconds"]
        bearing = rec.get("bearing")
        direction = rec.get("direction")
        vehicle = rec.get("vehicle_ref")
        delay_str = f", delay={delay_s}s" if delay_s is not None else ""
        extra = ""
        if bearing is not None:
            extra += f", bearing={bearing}"
        if direction:
            extra += f", dir={direction}"
        if vehicle:
            extra += f", vehicle={vehicle}"
        print(f"{i+1:2d}. line={line!r}, dest={dest!r}, lat={lat:.6f}, lon={lon:.6f}, operator={operator!r}{delay_str}{extra}")


if __name__ == "__main__":
    main()




