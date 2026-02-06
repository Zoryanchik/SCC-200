#!/usr/bin/env python3
"""TransXChange dataset manager

Features:
- ensure database exists (no tables created)
- download all XML files linked from a dataset index URL into a local folder
- provide parsing helpers to turn XML files into in-memory BusData objects

Usage examples:
  python txc_manager.py download https://transport.scc.lancs.ac.uk/timetable/dataset/18047/download/ --out data/18047
  python txc_manager.py parse data/18047 --date 2026-01-20
"""

from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
import urllib.request
import os
import argparse
import shutil
import xml.etree.ElementTree as ET
import zipfile
from typing import List


def ensure_database(db_name: str, user: str = '', password: str = '', host: str = 'localhost') -> bool:
    """Create database `db_name` using the postgres database if it doesn't exist."""
    try:
        import psycopg2
        conn = psycopg2.connect(host=host, user=user, password=password, database='postgres')
        conn.autocommit = True
        cur = conn.cursor()
        try:
            cur.execute(f"CREATE DATABASE {db_name}")
            print(f"Created database '{db_name}'")
        except psycopg2.errors.DuplicateDatabase:
            print(f"Database '{db_name}' already exists")
        cur.close()
        conn.close()
        return True
    except Exception as e:
        print(f"Could not create/connect to postgres: {e}")
        return False


class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == 'a':
            href = None
            for k, v in attrs:
                if k.lower() == 'href':
                    href = v
                    break
            if href:
                self.links.append(href)


def list_index_xml_links(index_url: str) -> List[str]:
    """Fetch an index page and return absolute URLs for anchors ending in .xml."""
    resp = urllib.request.urlopen(index_url)
    text = resp.read().decode('utf-8', errors='ignore')
    p = LinkParser()
    p.feed(text)
    abs_links = []
    for href in p.links:
        if href.lower().endswith('.xml'):
            abs_links.append(urljoin(index_url, href))
    return abs_links


def download_file(url: str, out_path: str) -> None:
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    if os.path.exists(out_path):
        print(f"Skip (exists): {out_path}")
        return
    print(f"Downloading {url} -> {out_path}")
    with urllib.request.urlopen(url) as r, open(out_path, 'wb') as w:
        shutil.copyfileobj(r, w)


def _download_zip(url: str, out_dir: str) -> List[str]:
    os.makedirs(out_dir, exist_ok=True)
    zip_path = os.path.join(out_dir, "dataset.zip")
    print(f"Downloading {url} -> {zip_path}")
    with urllib.request.urlopen(url) as r, open(zip_path, 'wb') as w:
        shutil.copyfileobj(r, w)

    extracted = []
    with zipfile.ZipFile(zip_path, 'r') as zf:
        for name in zf.namelist():
            if name.lower().endswith('.xml'):
                target = os.path.join(out_dir, os.path.basename(name))
                with zf.open(name) as src, open(target, 'wb') as dst:
                    shutil.copyfileobj(src, dst)
                extracted.append(target)
    return extracted


def download_dataset(index_url: str, out_dir: str) -> List[str]:
    """Download XML files from an index page or a ZIP endpoint."""
    try:
        with urllib.request.urlopen(index_url) as resp:
            content_type = resp.headers.get('Content-Type', '')
    except Exception:
        content_type = ''

    if 'zip' in content_type.lower():
        return _download_zip(index_url, out_dir)

    links = list_index_xml_links(index_url)
    files = []
    for link in links:
        name = os.path.basename(urlparse(link).path)
        out_path = os.path.join(out_dir, name)
        download_file(link, out_path)
        files.append(out_path)
    return files


# Lightweight TXC parser (adapted from previous loader implementation)

def local_name(tag: str) -> str:
    if '}' in tag:
        return tag.split('}', 1)[1]
    return tag


def parse_transxchange(xml_path: str, service_date: str = None):
    """Parse TXC and return a list of journeys (journey_id, route_id, stops[])."""
    tree = ET.parse(xml_path)
    root = tree.getroot()

    # optional service date filtering (OperatingPeriod on Service elements)
    service_periods = {}
    target_date = None
    if service_date:
        from datetime import datetime
        target_date = datetime.strptime(service_date, "%Y-%m-%d").date()
        for svc in root.iter():
            if local_name(svc.tag).lower() == 'service':
                svc_code = None
                start = None
                end = None
                for c in svc:
                    ctag = local_name(c.tag).lower()
                    if ctag == 'servicecode':
                        svc_code = (c.text or '').strip()
                    elif ctag == 'operatingperiod':
                        for op in c:
                            oname = local_name(op.tag).lower()
                            if oname == 'startdate':
                                start = (op.text or '').strip()
                            elif oname == 'enddate':
                                end = (op.text or '').strip()
                if svc_code and start and end:
                    service_periods[svc_code] = (start, end)

    # StopPoint -> ATCO (if present in the file)
    stop_point_atco = {}
    for sp in root.iter():
        sname = local_name(sp.tag).lower()
        if sname in ('stoppoint', 'scheduledstoppoint', 'annotatedstoppointref'):
            sid = sp.attrib.get('id') or sp.findtext('StopPointRef') or sp.findtext('StopPointId')
            atco = None
            for c in sp:
                if local_name(c.tag).lower() == 'atcocode':
                    atco = (c.text or '').strip()
                    break
            if sid and atco:
                stop_point_atco[sid] = atco

    # JourneyPatternSection -> ordered StopPointRef
    section_map = {}
    for sec in root.iter():
        sname = local_name(sec.tag).lower()
        if 'journeypatternsection' in sname:
            sid = sec.attrib.get('id')
            refs = []
            seen = set()
            for node in sec.iter():
                nname = local_name(node.tag).lower()
                if nname in ('stoppointref', 'scheduledstoppointref'):
                    ref = (node.text or node.attrib.get('ref') or '').strip()
                    if ref and ref not in seen:
                        seen.add(ref)
                        refs.append(ref)
            if sid:
                section_map[sid] = refs

    # JourneyPattern -> ordered stops via JourneyPatternSectionRefs
    journey_patterns = {}
    for jp in root.iter():
        if local_name(jp.tag).lower() == 'journeypattern':
            pid = jp.attrib.get('id')
            refs_text = ''
            for child in jp:
                if local_name(child.tag).lower().endswith('journeypatternsectionrefs'):
                    refs_text = (child.text or '').strip()
                    break
            stops = []
            if refs_text and pid:
                for ref in refs_text.split():
                    sec_refs = section_map.get(ref, [])
                    for r in sec_refs:
                        atco = stop_point_atco.get(r)
                        stops.append((atco or r, None))
            if pid:
                journey_patterns[pid] = stops

    journeys = []
    for elem in root.iter():
        tag = local_name(elem.tag).lower()
        if tag in ('vehiclejourney', 'scheduledvehiclejourney', 'journey'):
            # optional date filter using ServiceRef -> OperatingPeriod
            if service_date:
                svc_ref = elem.findtext('ServiceRef') or elem.findtext('serviceref')
                if svc_ref:
                    period = service_periods.get(svc_ref)
                    if period:
                        from datetime import datetime
                        sd = datetime.strptime(period[0], "%Y-%m-%d").date()
                        ed = datetime.strptime(period[1], "%Y-%m-%d").date()
                        if not (sd <= target_date <= ed):
                            continue
                else:
                    continue

            jid = elem.attrib.get('id') or elem.findtext('VehicleJourneyCode') or f"journey_{len(journeys)}"
            route_ref = elem.findtext('LineRef') or elem.findtext('RouteRef') or elem.findtext('ServiceRef')

            stop_list = []
            # try JourneyPatternRef
            jpref = elem.findtext('JourneyPatternRef') or elem.findtext('journeypatternref')
            if jpref and jpref in journey_patterns:
                stop_list = list(journey_patterns[jpref])

            if not stop_list:
                for stop_elem in elem.iter():
                    sname = local_name(stop_elem.tag).lower()
                    if sname in ('scheduledstoppointref', 'stoppointref', 'stopref', 'stoppoint'):
                        code = (stop_elem.text or stop_elem.attrib.get('ref') or '').strip()
                        time = None
                        for ssub in stop_elem.iter():
                            stn = local_name(ssub.tag).lower()
                            if stn in ('departuretime', 'arrivaltime', 'scheduleddeparturetime'):
                                time = ssub.text
                                break
                        if code:
                            atco = stop_point_atco.get(code)
                            stop_list.append((atco or code, time))

            journeys.append({'journey_id': jid, 'route_id': route_ref or 'route_unknown', 'stops': stop_list})

    return journeys


def build_busdata_from_journeys(journeys):
    """Return a BusData object built from parsed journeys (in-memory)."""
    try:
        from bus_data import BusData
    except Exception as e:
        raise RuntimeError('BusData class not available: ' + str(e))

    routes = {j['route_id'] for j in journeys}
    journey_ids = {j['journey_id'] for j in journeys}
    stops = set()
    for j in journeys:
        for s, _ in j['stops']:
            if s:
                stops.add(s)

    num_routes = max(1, len(routes))
    num_journeys = max(1, len(journey_ids))
    num_stops = max(1, len(stops))

    bd = BusData(num_routes=num_routes, num_journeys=num_journeys, num_stops=num_stops)

    for j in journeys:
        route_id = j['route_id']
        atco_codes = [s for (s, _) in j['stops'] if s]
        bd.add_route(route_id=route_id, atco_codes=atco_codes, journey_id=j['journey_id'])

    for j in journeys:
        arrival_times = []
        for s, t in j['stops']:
            arrival_times.append((s, t or ''))
        bd.add_journey(journey_id=j['journey_id'], route_id=j['route_id'], arrival_times=arrival_times)

    return bd


def list_local_xml(dirpath: str) -> List[str]:
    if not os.path.isdir(dirpath):
        return []
    return [os.path.join(dirpath, f) for f in os.listdir(dirpath) if f.lower().endswith('.xml')]


def main():
    p = argparse.ArgumentParser(description='TransXChange dataset manager')
    sub = p.add_subparsers(dest='cmd')

    d = sub.add_parser('download')
    d.add_argument('index_url')
    d.add_argument('--out', required=True, help='local output directory')
    d.add_argument('--db', help='create database name (optional)')
    d.add_argument('--user', default='', help='DB user')
    d.add_argument('--password', default='', help='DB password')
    d.add_argument('--host', default='localhost', help='DB host')

    p2 = sub.add_parser('parse')
    p2.add_argument('dir')
    p2.add_argument('--date', help='YYYY-MM-DD to filter services')

    args = p.parse_args()
    if args.cmd == 'download':
        if args.db:
            ensure_database(args.db, user=args.user, password=args.password, host=args.host)
        files = download_dataset(args.index_url, args.out)
        print(f"Downloaded {len(files)} files to {args.out}")
    elif args.cmd == 'parse':
        files = list_local_xml(args.dir)
        print(f"Found {len(files)} XML files in {args.dir}")
        for f in files:
            journeys = parse_transxchange(f, service_date=args.date)
            print(
                f"{os.path.basename(f)} -> routes approx {len(set(j['route_id'] for j in journeys))}, "
                f"journeys {len(journeys)}, stops {len({s for j in journeys for (s, _) in j['stops'] if s})}"
            )
    else:
        p.print_help()


if __name__ == '__main__':
    main()
