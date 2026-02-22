import ssl
import urllib.request
import xml.etree.ElementTree as ET
from typing import List, Tuple, Iterable, Optional


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
                     lat_tol: float = 0.01, lon_tol: float = 0.01) -> List[Tuple[str, str, float, float, str]]:
        """Return nearby live vehicles as (line_ref, destination_name, lat, lon, operator_name).

        lat, lon are the centre point; lat_tol/lon_tol define the half-widths of
        the allowed rectangle.
        """
        lat_min = lat - lat_tol
        lat_max = lat + lat_tol
        lon_min = lon - lon_tol
        lon_max = lon + lon_tol

        urls_to_use = list(urls) if urls else self.urls
        results: List[Tuple[str, str, float, float]] = []

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

                    # vehicle location may be nested under VehicleLocation element
                    lat_s = lon_s = ''
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

                    try:
                        lat_v = float(lat_s)
                        lon_v = float(lon_s)
                    except Exception:
                        continue

                    if lat_min <= lat_v <= lat_max and lon_min <= lon_v <= lon_max:
                        results.append((line_ref, dest, lat_v, lon_v, operator_name))

        return results


def get_bus_live(lat, lon, urls=None, lat_tol=0.0003, lon_tol=0.0003, timeout=10):
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
    for i, (line, dest, lat, lon, operator) in enumerate(display_results):
        print(f"{i+1:2d}. line={line!r}, dest={dest!r}, lat={lat:.6f}, lon={lon:.6f}, operator={operator!r}")


if __name__ == "__main__":
    main()




