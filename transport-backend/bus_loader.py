import bisect
import io
import os
import pickle
import re
import psycopg
from psycopg.rows import dict_row
import ssl
import tempfile
import json
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from bus_data import BusData
import traceback

class BusLoader:
    def __init__( self, db_path, user=None, password=None, walking_db_path=None ):
        self.db_path = db_path
        self.user = user
        self.password = password
        # Optional separate walking DB (contains stop_coords and walking_transfers)
        self.walking_db_path = walking_db_path

    # Connection helper: return a DB connection object. Backend is
    # Postgres-only: treat the configured DB path as a Postgres DSN
    # (psycopg) and return a native psycopg connection.
    def _connect(self, path=None):
        db = path or self.db_path

        # Always use Postgres (psycopg) in Postgres-only mode.
        pg_conn = psycopg.connect(db)
        # psycopg connection is returned directly for Postgres usage.
        return pg_conn

    def get_download_urls(self):
        """Return a list of dataset download URLs.

        Fetches operator index pages, picks the entry with the latest
        creation time from each, and collects its download URL.
        """
        datasets = self._fetch_dataset_info()
        return [d['download_url'] for d in datasets]

    def _fetch_dataset_info(self):
        """Return a list of dicts with source_url, download_url, modified
        for every operator dataset."""
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        sources = [
            'https://transport.scc.lancs.ac.uk/bus/times/ARCT',
            'https://transport.scc.lancs.ac.uk/bus/times/BLAC',
            'https://transport.scc.lancs.ac.uk/bus/times/KLCO',
            'https://transport.scc.lancs.ac.uk/bus/times/NUTT',
        ]

        # sources where we filter by description instead of latest created
        desc_sources = [
            ('https://transport.scc.lancs.ac.uk/bus/times/SCCU', 'Stagecoach Cumbria & North Lancashire'),
            ('https://transport.scc.lancs.ac.uk/bus/times/SCMY', 'Stagecoach Merseyside & South Lancashire'),
        ]

        datasets = []
        for src in sources:
            resp = urllib.request.urlopen(src, context=ctx)
            data = json.loads(resp.read())
            results = data.get('results', [])
            if not results:
                continue
            latest = max(results, key=lambda r: r.get('created', ''))
            url = latest.get('url', '')
            if url:
                datasets.append({
                    'source_url': src,
                    'download_url': url,
                    'modified': latest.get('modified', ''),
                })

        for src, desc in desc_sources:
            resp = urllib.request.urlopen(src, context=ctx)
            data = json.loads(resp.read())
            results = data.get('results', [])
            matched = [r for r in results if r.get('description') == desc]
            if not matched:
                continue
            latest = max(matched, key=lambda r: r.get('created', ''))
            url = latest.get('url', '')
            if url:
                datasets.append({
                    'source_url': src,
                    'download_url': url,
                    'modified': latest.get('modified', ''),
                })

        return datasets

    def check_for_updates(self):
        """Check remote APIs for updated datasets and reload any that changed.

        Compares the 'modified' timestamp of each remote dataset against what
        is stored in the local dataset_meta table.  Downloads and reloads only
        the datasets whose timestamp has changed.

        Returns True if any data was reloaded, False otherwise.
        """
        print("  Checking for timetable updates...")
        datasets = self._fetch_dataset_info()

        conn = self._connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT source_url, modified FROM bus_dataset_meta")
        stored = {row[0]: row[1] for row in cur.fetchall()}
        conn.close()

        changed = []
        for ds in datasets:
            prev = stored.get(ds['source_url'])
            if prev != ds['modified']:
                changed.append(ds)

        if not changed:
            print("  ✓ All datasets up-to-date")
            return False

        print(f"  {len(changed)} dataset(s) changed — reloading...")
        for ds in changed:
            self.download_and_load(ds['download_url'])
            # save the new timestamp
            conn = self._connect(self.db_path)
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO bus_dataset_meta (source_url, download_url, modified) VALUES (%s, %s, %s) "
                "ON CONFLICT (source_url) DO UPDATE SET download_url = EXCLUDED.download_url, modified = EXCLUDED.modified",
                (ds['source_url'], ds['download_url'], ds['modified']),
            )
            conn.commit()
            conn.close()

        print(f"  ✓ Reloaded {len(changed)} dataset(s)")
        return True

    def ensure_db( self ):
        # Create the database file if it doesn't exist
        conn = self._connect(self.db_path)
        conn.close()

    #Create tables
    def create_schema( self ):
        conn = self._connect(self.db_path)
        schema = '''
            CREATE TABLE IF NOT EXISTS bus_route_stops (
                route_id   TEXT,
                atco_code  TEXT,
                stop_order INTEGER NOT NULL,
                PRIMARY KEY (route_id, atco_code)
            );
            CREATE TABLE IF NOT EXISTS bus_journey_routes (
                journey_id TEXT PRIMARY KEY,
                route_id   TEXT NOT NULL,
                line_name  TEXT,
                destination_display TEXT
            );
            CREATE TABLE IF NOT EXISTS bus_journey_times (
                journey_id     TEXT,
                atco_code      TEXT,
                arrival_time   INTEGER NOT NULL,
                PRIMARY KEY (journey_id, atco_code)
            );
            CREATE TABLE IF NOT EXISTS bus_stop_names (
                atco_code   TEXT PRIMARY KEY,
                common_name TEXT NOT NULL,
                indicator   TEXT,
                locality    TEXT
            );
            -- stop coordinates and walking transfers are now stored in a separate walking DB
            CREATE TABLE IF NOT EXISTS bus_dataset_meta (
                source_url   TEXT PRIMARY KEY,
                download_url TEXT NOT NULL,
                modified     TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS bus_service_operating_period (
                service_code TEXT NOT NULL,
                start_date   TEXT NOT NULL,
                end_date     TEXT,
                PRIMARY KEY (service_code)
            );
            CREATE TABLE IF NOT EXISTS bus_serviced_org_working_days (
                service_code     TEXT NOT NULL,
                start_date   TEXT NOT NULL,
                end_date     TEXT NOT NULL,
                PRIMARY KEY (service_code, start_date, end_date)
            );
            CREATE TABLE IF NOT EXISTS bus_journey_operating_profile (
                journey_id   TEXT NOT NULL,
                service_code TEXT NOT NULL,
                days_of_week INTEGER NOT NULL DEFAULT 0,
                start_date   TEXT,
                end_date     TEXT,
                org_ref      TEXT,
                org_working  INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (journey_id)
            );
        '''
        import re as _re
        cur = conn.cursor()
        for stmt in schema.split(';'):
            stmt = stmt.strip()
            if not stmt:
                continue
            stmt2 = _re.sub(r"CHAR\s*\(\s*\d+\s*\)", "TEXT", stmt, flags=_re.I)
            cur.execute(stmt2)
        conn.commit()
        conn.close()
        
    @staticmethod
    def _parse_duration(iso):
        """Parse ISO 8601 duration like 'PT1M0S' or 'PT2H30M' into seconds."""
        m = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', iso or '')
        if not m:
            return 0
        h, mi, s = (int(v) if v else 0 for v in m.groups())
        return h * 3600 + mi * 60 + s

    @staticmethod
    def _hms_to_seconds(hms):
        """Convert 'HH:MM:SS' to seconds since midnight."""
        parts = hms.split(':')
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])

    def load_file(self, file_path):
        """Parse a TransXChange XML file and populate the database."""
        tree = ET.parse(file_path)
        root = tree.getroot()
        ns = '{http://www.transxchange.org.uk/}'

        # Attempt to determine the operator / organisation code for this file.
        # TransXChange files may include this under several elements; try a few
        # common locations and fall back to empty string if not found.
        service_code = ''
        # Preferred: Services/Service/ServiceCode
        svc_el = root.find(f'{ns}Services/{ns}Service')
        if svc_el is not None:
            service_code = svc_el.findtext(f'{ns}ServiceCode', '').strip()
        # Fallback: ServiceOrganisations/ServiceOrganisation/OrganisationCode
        if not service_code:
            so_el = root.find(f'{ns}ServiceOrganisations/{ns}ServiceOrganisation')
            if so_el is not None:
                service_code = so_el.findtext(f'{ns}OrganisationCode', '').strip()
        # Fallback: ServicedOrganisations/ServicedOrganisation/OrganisationCode
        if not service_code:
            sdel = root.find(f'{ns}ServicedOrganisations/{ns}ServicedOrganisation')
            if sdel is not None:
                service_code = sdel.findtext(f'{ns}OrganisationCode', '').strip()
        # Another possible location: ServiceOperator/OperatorCode
        if not service_code:
            op_el = root.find(f'{ns}ServiceOperator/{ns}OperatorCode')
            if op_el is not None:
                service_code = (op_el.text or '').strip()

        # ── 0. StopPoints — AtcoCode → CommonName mapping ────────
        stop_names_rows = []
        for sp in root.findall(f'{ns}StopPoints/{ns}AnnotatedStopPointRef'):
            atco = sp.findtext(f'{ns}StopPointRef')
            cname = sp.findtext(f'{ns}CommonName')
            indicator = sp.findtext(f'{ns}Indicator', '')
            locality = sp.findtext(f'{ns}LocalityName', '')
            if atco and cname:
                stop_names_rows.append((atco, cname, indicator, locality))

        # ── 1. JourneyPatternSections ──────────────────────────────
        # jps_data[section_id] = [ (jptl_id, from_stop, to_stop, run_seconds, to_day_shift), … ]
        jps_data = {}
        for jps in root.findall(f'{ns}JourneyPatternSections/{ns}JourneyPatternSection'):
            sid = jps.attrib['id']
            links = []
            for jptl in jps.findall(f'{ns}JourneyPatternTimingLink'):
                jptl_id  = jptl.attrib['id']
                from_stop = jptl.findtext(f'{ns}From/{ns}StopPointRef')
                to_stop   = jptl.findtext(f'{ns}To/{ns}StopPointRef')
                run_sec   = self._parse_duration(jptl.findtext(f'{ns}RunTime'))
                # DepartureDayShift on the To element: number of days
                # after the journey start that this stop is reached
                to_day_shift = int(jptl.findtext(f'{ns}To/{ns}DepartureDayShift') or '0')
                links.append((jptl_id, from_stop, to_stop, run_sec, to_day_shift))
            jps_data[sid] = links

        # Build ordered stop list from a section's timing links
        def section_stops(sid):
            links = jps_data[sid]
            if not links:
                return []
            stops = [links[0][1]]  # first From
            for _, _, to_stop, _, _ in links:
                stops.append(to_stop)
            return stops

        # ── 2. JourneyPatterns (under Services) ──────────────────
        # jp_map[jp_id] = { route_ref, section_ids }
        jp_map = {}
        svc = root.find(f'{ns}Services/{ns}Service')

        # ── 2a. Service OperatingPeriod ───────────────────────────
        service_code = svc.findtext(f'{ns}ServiceCode', '')
        op_period = svc.find(f'{ns}OperatingPeriod')
        svc_start = op_period.findtext(f'{ns}StartDate', '') if op_period is not None else ''
        svc_end   = op_period.findtext(f'{ns}EndDate', '')   if op_period is not None else ''
        service_op_rows = [(service_code, svc_start, svc_end)]

        # ── 2b. Services OperatingPeriod ─────────────────────────────
        serviced_org_rows = []
        service_working_map = {}  # service_code -> list of (start, end)
        op_period = svc.find(f'{ns}OperatingPeriod')
        if op_period is not None:
            sd = op_period.findtext(f'{ns}StartDate', '')
            ed = op_period.findtext(f'{ns}EndDate', '')
            if sd and ed:
                serviced_org_rows.append((service_code, sd, ed))
                service_working_map.setdefault(service_code, []).append((sd, ed))

        # Parse Lines: line_id -> LineName
        line_names = {}
        for line in svc.findall(f'{ns}Lines/{ns}Line'):
            line_names[line.attrib['id']] = line.findtext(f'{ns}LineName', '')

        std = svc.find(f'{ns}StandardService')
        for jp in std.findall(f'{ns}JourneyPattern'):
            jp_id = jp.attrib['id']
            route_ref = jp.findtext(f'{ns}RouteRef')
            sec_refs  = [s.text for s in jp.findall(f'{ns}JourneyPatternSectionRefs')]
            dest_display = jp.findtext(f'{ns}DestinationDisplay', '')
            jp_map[jp_id] = {'route_ref': route_ref, 'section_ids': sec_refs, 'destination_display': dest_display}

        # ── 3. Routes → route_stops ──────────────────────────────
        # Build stop list per route from its JourneyPatterns' sections
        # Multiple JPs can share a RouteRef; pick the longest stop list
        route_stop_lists = {}  # route_ref -> [atco_codes]
        for jp_id, info in jp_map.items():
            rref = info['route_ref']
            stops = []
            for sid in info['section_ids']:
                stops.extend(section_stops(sid))
            # deduplicate while preserving order (chain of From→To can repeat)
            seen = set()
            ordered = []
            for s in stops:
                if s not in seen:
                    seen.add(s)
                    ordered.append(s)
            if rref not in route_stop_lists or len(ordered) > len(route_stop_lists[rref]):
                route_stop_lists[rref] = ordered

        route_stops_rows = []
        for route_id, stops in route_stop_lists.items():
            # Prefix route_id with service code if available so that
            # route identifiers are namespaced per service. This ensures
            # uniqueness across multiple operator datasets loaded into the
            # same DB.
            rkey = f"{service_code}:{route_id}" if service_code else route_id
            for idx, atco in enumerate(stops):
                route_stops_rows.append((rkey, atco, idx))

        # ── 4. VehicleJourneys → journey_routes + journey_times ──
        journey_routes_rows = []
        journey_times_rows  = []
        journey_op_rows     = []

        # Days-of-week element names → bitmask (bit 0 = Monday, bit 6 = Sunday)
        DOW_BITS = {
            'Monday': 1, 'Tuesday': 2, 'Wednesday': 4, 'Thursday': 8,
            'Friday': 16, 'Saturday': 32, 'Sunday': 64,
            'MondayToFriday': 31, 'MondayToSaturday': 63,
            'MondayToSunday': 127, 'Weekend': 96,
            'NotSaturday': 95, 'NotSunday': 63,
        }

        for vj in root.findall(f'{ns}VehicleJourneys/{ns}VehicleJourney'):
            vj_code  = vj.findtext(f'{ns}VehicleJourneyCode')
            jp_ref   = vj.findtext(f'{ns}JourneyPatternRef')
            dep_hms  = vj.findtext(f'{ns}DepartureTime')
            dep_sec  = self._hms_to_seconds(dep_hms)
            line_ref = vj.findtext(f'{ns}LineRef', '')
            line_name = line_names.get(line_ref, '')
            if line_name:
                line_name = f"{service_code}:{line_name}"

            info = jp_map[jp_ref]
            route_ref = info['route_ref']
            destination_display = info.get('destination_display', '')
            # Namespace journey and route IDs with the service code
            # so they're unique across multiple operators.
            jkey = f"{service_code}:{vj_code}" if service_code and vj_code else (vj_code or '')
            rkey = f"{service_code}:{route_ref}" if service_code and route_ref else (route_ref or '')
            journey_routes_rows.append((jkey, rkey, line_name, destination_display))

            # ── Parse OperatingProfile ────────────────────────────
            op = vj.find(f'{ns}OperatingProfile')
            dow_mask = 127  # default: all days
            op_start = svc_start
            op_end   = svc_end
            org_ref  = ''
            org_working = 1  # 1 = runs on org working days, 0 = runs on org holidays

            if op is not None:
                # RegularDayType / DaysOfWeek
                dow_el = op.find(f'{ns}RegularDayType/{ns}DaysOfWeek')
                if dow_el is not None:
                    dow_mask = 0
                    for child in dow_el:
                        tag = child.tag.replace(ns, '')
                        dow_mask |= DOW_BITS.get(tag, 0)

                # SpecialDaysOperation — override start/end date range
                sdo = op.find(f'{ns}SpecialDaysOperation')
                if sdo is not None:
                    inc = sdo.find(f'{ns}DaysOfOperation/{ns}DateRange')
                    if inc is not None:
                        sd = inc.findtext(f'{ns}StartDate', '')
                        ed = inc.findtext(f'{ns}EndDate', '')
                        if sd:
                            op_start = sd
                        if ed:
                            op_end = ed

            # Store journey operating profile keyed by the namespaced journey id
            journey_op_rows.append((jkey, service_code, dow_mask, op_start, op_end, org_ref, org_working))

            # Build per-JPTL RunTime overrides from VehicleJourneyTimingLinks
            overrides = {}
            for vjtl in vj.findall(f'{ns}VehicleJourneyTimingLink'):
                ref = vjtl.findtext(f'{ns}JourneyPatternTimingLinkRef')
                rt  = vjtl.findtext(f'{ns}RunTime')
                if ref and rt:
                    overrides[ref] = self._parse_duration(rt)

            # Walk through the JourneyPatternSection timing links
            cum = dep_sec  # cumulative time in seconds
            first = True
            for sid in info['section_ids']:
                for jptl_id, from_stop, to_stop, base_run, to_day_shift in jps_data[sid]:
                    if first:
                        # arrival at the first stop is the departure time
                        journey_times_rows.append((jkey, from_stop, cum))
                        first = False
                    run = overrides.get(jptl_id, base_run)
                    cum += run
                    # Apply DepartureDayShift: ensure arrival is on the
                    # correct day (e.g. overnight journeys crossing midnight)
                    if to_day_shift:
                        min_time = dep_sec + to_day_shift * 86400
                        if cum < min_time:
                            cum = min_time
                    journey_times_rows.append((jkey, to_stop, cum))

        # ── 5. Persist ───────────────────────────────────────────
        self.populate(route_stops_rows, journey_routes_rows, journey_times_rows, stop_names_rows,
                      service_op_rows, serviced_org_rows, journey_op_rows)

    def load_folder(self, folder_path):
        """Parse every .xml file in a folder and populate the database."""
        files = sorted(f for f in os.listdir(folder_path) if f.lower().endswith('.xml'))
        total = len(files)
        for i, fname in enumerate(files, 1):
            fpath = os.path.join(folder_path, fname)
            try:
                self.load_file(fpath)
                print(f'[{i}/{total}] OK  {fname}')
            except Exception as e:
                print(f'[{i}/{total}] ERR {fname}: {e}')
                traceback.print_exc()
        print(f'Done. {total} files processed.')

    def download_and_load(self, url):
        """Download a zip of TXC XML files from a URL, extract, and load into the DB.

        Args:
            url: e.g. 'https://transport.scc.lancs.ac.uk/timetable/dataset/18047/download/'
        """
        print(f'Downloading {url} ...')
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        resp = urllib.request.urlopen(url, context=ctx)
        data = resp.read()
        print(f'Downloaded {len(data) / 1024 / 1024:.1f} MB')

        with tempfile.TemporaryDirectory() as tmp_dir:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                zf.extractall(tmp_dir)
            self.load_folder(tmp_dir)

    def populate( self, route_stops, journey_routes, journey_times, stop_names=None,
                  service_ops=None, serviced_orgs=None, journey_ops=None ):
        """Insert data into the database.
        Args:
            route_stops:    list of (route_id, atco_code, stop_order)
            journey_routes: list of (journey_id, route_id, line_name, destination_display)
            journey_times:  list of (journey_id, atco_code, arrival_time)
            stop_names:     list of (atco_code, common_name, indicator, locality)
            service_ops:    list of (service_code, start_date, end_date)
            serviced_orgs:  list of (service_code, start_date, end_date)
            journey_ops:    list of (journey_id, service_code, days_of_week, start_date, end_date, org_ref, org_working)
        """
        conn = self._connect(self.db_path)
        cursor = conn.cursor()
        cursor.executemany(
            "INSERT INTO bus_route_stops (route_id, atco_code, stop_order) VALUES (%s, %s, %s) "
            "ON CONFLICT (route_id, atco_code) DO UPDATE SET stop_order = EXCLUDED.stop_order",
            route_stops
        )
        cursor.executemany(
            "INSERT INTO bus_journey_routes (journey_id, route_id, line_name, destination_display) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (journey_id) DO UPDATE SET route_id = EXCLUDED.route_id, line_name = EXCLUDED.line_name, destination_display = EXCLUDED.destination_display",
            journey_routes
        )
        cursor.executemany(
            "INSERT INTO bus_journey_times (journey_id, atco_code, arrival_time) VALUES (%s, %s, %s) "
            "ON CONFLICT (journey_id, atco_code) DO UPDATE SET arrival_time = EXCLUDED.arrival_time",
            journey_times
        )
        if stop_names:
            cursor.executemany(
                "INSERT INTO bus_stop_names (atco_code, common_name, indicator, locality) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (atco_code) DO UPDATE SET common_name = EXCLUDED.common_name, indicator = EXCLUDED.indicator, locality = EXCLUDED.locality",
                stop_names
            )
        if service_ops:
            cursor.executemany(
                "INSERT INTO bus_service_operating_period (service_code, start_date, end_date) VALUES (%s, %s, %s) "
                "ON CONFLICT (service_code) DO UPDATE SET start_date = EXCLUDED.start_date, end_date = EXCLUDED.end_date",
                service_ops
            )
        if serviced_orgs:
            cursor.executemany(
                # This table's primary key is (service_code, start_date, end_date);
                # when the exact triple exists, there's nothing to update, so use DO NOTHING.
                "INSERT INTO bus_serviced_org_working_days (service_code, start_date, end_date) VALUES (%s, %s, %s) "
                "ON CONFLICT (service_code, start_date, end_date) DO NOTHING",
                serviced_orgs
            )
        if journey_ops:
            cursor.executemany(
                "INSERT INTO bus_journey_operating_profile (journey_id, service_code, days_of_week, start_date, end_date, org_ref, org_working) VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (journey_id) DO UPDATE SET service_code = EXCLUDED.service_code, days_of_week = EXCLUDED.days_of_week, start_date = EXCLUDED.start_date, end_date = EXCLUDED.end_date, org_ref = EXCLUDED.org_ref, org_working = EXCLUDED.org_working",
                journey_ops
            )
        conn.commit()
        conn.close()

    def load_busdata( self ):
        """Query the SQLite DB and build a BusData object."""
        conn = self._connect(self.db_path)
        cursor = conn.cursor()

        # --- count distinct entities for initial sizing ---
        cursor.execute("SELECT COUNT(DISTINCT route_id) FROM bus_route_stops")
        num_routes = cursor.fetchone()[0] or 0
        cursor.execute("SELECT COUNT(DISTINCT journey_id) FROM bus_journey_routes")
        num_journeys = cursor.fetchone()[0] or 0
        cursor.execute("SELECT COUNT(DISTINCT atco_code) FROM bus_route_stops")
        num_stops = cursor.fetchone()[0] or 0

        bd = BusData(num_routes=num_routes, num_journeys=num_journeys, num_stops=num_stops)

        # --- 1. load route_stops: route_id -> ordered list of atco_codes ---
        cursor.execute("SELECT route_id, atco_code FROM bus_route_stops ORDER BY route_id, stop_order")
        current_route = None
        stops_buf = []
        for route_id, atco_code in cursor.fetchall():
            if route_id != current_route:
                if current_route is not None:
                    bd.add_route_stop(current_route, stops_buf)
                current_route = route_id
                stops_buf = []
            stops_buf.append(atco_code)
        if current_route is not None:
            bd.add_route_stop(current_route, stops_buf)

        # --- 2. load journey_routes: route_id -> list of journey_ids ---
        cursor.execute("SELECT route_id, journey_id FROM bus_journey_routes ORDER BY route_id")
        current_route = None
        journeys_buf = []
        for route_id, journey_id in cursor.fetchall():
            if route_id != current_route:
                if current_route is not None:
                    bd.add_route_journeys(current_route, journeys_buf)
                current_route = route_id
                journeys_buf = []
            journeys_buf.append(journey_id)
        if current_route is not None:
            bd.add_route_journeys(current_route, journeys_buf)

        # --- 3. load journey_times: journey_id -> list of (atco_code, arrival) ---
        cursor.execute("SELECT journey_id, atco_code, arrival_time FROM bus_journey_times ORDER BY journey_id, arrival_time")
        current_journey = None
        times_buf = []
        for journey_id, atco_code, arrival_time in cursor.fetchall():
            if journey_id != current_journey:
                if current_journey is not None:
                    bd.add_journey_times(current_journey, times_buf)
                current_journey = journey_id
                times_buf = []
            times_buf.append((atco_code, arrival_time))
        if current_journey is not None:
            bd.add_journey_times(current_journey, times_buf)

        # --- 4. populate route_metadata and journey_metadata ---------------
        # journey_routes has (journey_id, route_id, line_name, destination_display)
        cursor.execute("SELECT journey_id, route_id, line_name, destination_display FROM bus_journey_routes")
        route_line_names = {}   # route_id -> line_name (first seen)
        for journey_id, route_id, line_name, destination_display in cursor.fetchall():
            # journey metadata
            j_int = bd.map_journeys.code_to_int.get(journey_id)
            if j_int is not None:
                bd.journey_metadata[j_int] = {
                    "journey_id": journey_id,
                    "route_id":   route_id,
                    "line_name":  line_name or "",
                    "destination_display": destination_display or "",
                }
            # collect line_name per route (keep first non-empty)
            if route_id not in route_line_names or not route_line_names[route_id]:
                route_line_names[route_id] = line_name or ""

        # route metadata
        for route_id, line_name in route_line_names.items():
            r_int = bd.map_routes.code_to_int.get(route_id)
            if r_int is not None:
                bd.route_metadata[r_int] = {
                    "route_id":  route_id,
                    "line_name": line_name,
                }

        conn.close()
        return bd

    def load_busdata_for_date( self, date_str ):
        """Build a BusData containing only journeys that operate on *date_str*.

        Args:
            date_str: 'YYYY-MM-DD'
        """
        from datetime import date as _date
        query_date = _date.fromisoformat(date_str)
        dow_bit = 1 << query_date.weekday()       # Mon=0 → bit 1, Sun=6 → bit 64

        conn = self._connect(self.db_path)
        cur = conn.cursor()

        # 1. Load serviced org working-day ranges
        service_ranges = {}   # service_code -> [(start, end), ...]
        for svc, sd, ed in cur.execute("SELECT service_code, start_date, end_date FROM bus_serviced_org_working_days"):
            service_ranges.setdefault(svc, []).append((_date.fromisoformat(sd), _date.fromisoformat(ed)))

        # 1b. Load service operating periods (coarse outer boundary)
        svc_periods = {}  # service_code -> (start_date | None, end_date | None)
        for svc, sd, ed in cur.execute("SELECT service_code, start_date, end_date FROM bus_service_operating_period"):
            try:
                sp_s = _date.fromisoformat(sd) if sd else None
                sp_e = _date.fromisoformat(ed) if ed else None
            except ValueError:
                sp_s, sp_e = None, None
            svc_periods[svc] = (sp_s, sp_e)

        # 1c. Hard ceiling for open-ended services: use the latest
        #     explicitly-defined end date anywhere in the DB.
        row = cur.execute(
            "SELECT MAX(end_date) FROM bus_journey_operating_profile WHERE end_date != ''"
        ).fetchone()
        max_end_str = row[0] if row and row[0] else None
        hard_ceiling = _date.fromisoformat(max_end_str) if max_end_str else None

        # 2. Determine which journeys operate on this date
        valid_journeys = set()
        cur.execute(
            "SELECT journey_id, service_code, days_of_week, start_date, end_date, org_ref, org_working "
            "FROM bus_journey_operating_profile"
        )
        for j_id, svc_code, dow_mask, op_start, op_end, org_ref, org_working in cur.fetchall():
            # a) Date range check — journey-level, with service-period fallback
            try:
                s = _date.fromisoformat(op_start) if op_start else None
                e = _date.fromisoformat(op_end) if op_end else None
            except ValueError:
                continue

            # Fall back to service operating period for missing bounds
            svc_s, svc_e = svc_periods.get(svc_code, (None, None))
            if s is None:
                s = svc_s
            if e is None:
                e = svc_e

            # If still no end date, apply the hard ceiling
            if e is None:
                e = hard_ceiling

            if s and query_date < s:
                continue
            if e and query_date > e:
                continue

            # b) Day-of-week check
            if not (dow_mask & dow_bit):
                continue

            # c) Serviced organisation check
            if org_ref and org_ref in service_ranges:
                in_working = any(s <= query_date <= e for s, e in service_ranges[org_ref])
                if org_working == 1 and not in_working:
                    continue     # should run on working days, but today isn't one
                if org_working == 0 and in_working:
                    continue     # should run on non-working days (holidays), but today is a working day

            valid_journeys.add(j_id)

        conn.close()

        if not valid_journeys:
            # Return an empty BusData
            return BusData(num_routes=0, num_journeys=0, num_stops=0)

        # 3. Build BusData filtering to valid_journeys only
        conn = self._connect(self.db_path)
        cur = conn.cursor()

        # Figure out which routes are still needed
        placeholders = ','.join(['%s'] * len(valid_journeys))
        valid_list = list(valid_journeys)
        cur.execute(
            f"SELECT DISTINCT route_id FROM bus_journey_routes WHERE journey_id IN ({placeholders})",
            valid_list,
        )
        valid_routes = {r[0] for r in cur.fetchall()}

        # Count for sizing
        num_routes = len(valid_routes)
        num_journeys = len(valid_journeys)
        cur.execute("SELECT COUNT(DISTINCT atco_code) FROM bus_route_stops")
        num_stops = cur.fetchone()[0] or 0

        bd = BusData(num_routes=num_routes, num_journeys=num_journeys, num_stops=num_stops)

        # 3a. route_stops — only routes that have valid journeys
        route_placeholders = ','.join(['%s'] * len(valid_routes))
        valid_routes_list = list(valid_routes)
        cur.execute(
            f"SELECT route_id, atco_code FROM bus_route_stops WHERE route_id IN ({route_placeholders}) ORDER BY route_id, stop_order",
            valid_routes_list,
        )
        current_route = None
        stops_buf = []
        for route_id, atco_code in cur.fetchall():
            if route_id != current_route:
                if current_route is not None:
                    bd.add_route_stop(current_route, stops_buf)
                current_route = route_id
                stops_buf = []
            stops_buf.append(atco_code)
        if current_route is not None:
            bd.add_route_stop(current_route, stops_buf)

        # 3b. journey_routes — only valid journeys
        cur.execute(
            f"SELECT route_id, journey_id FROM bus_journey_routes WHERE journey_id IN ({placeholders}) ORDER BY route_id",
            valid_list,
        )
        current_route = None
        journeys_buf = []
        for route_id, journey_id in cur.fetchall():
            if route_id != current_route:
                if current_route is not None:
                    bd.add_route_journeys(current_route, journeys_buf)
                current_route = route_id
                journeys_buf = []
            journeys_buf.append(journey_id)
        if current_route is not None:
            bd.add_route_journeys(current_route, journeys_buf)

        # 3c. journey_times — only valid journeys
        cur.execute(
            f"SELECT journey_id, atco_code, arrival_time FROM bus_journey_times WHERE journey_id IN ({placeholders}) ORDER BY journey_id, arrival_time",
            valid_list,
        )
        current_journey = None
        times_buf = []
        for journey_id, atco_code, arrival_time in cur.fetchall():
            if journey_id != current_journey:
                if current_journey is not None:
                    bd.add_journey_times(current_journey, times_buf)
                current_journey = journey_id
                times_buf = []
            times_buf.append((atco_code, arrival_time))
        if current_journey is not None:
            bd.add_journey_times(current_journey, times_buf)

        # 3d. metadata
        cur.execute(
            f"SELECT journey_id, route_id, line_name, destination_display FROM bus_journey_routes WHERE journey_id IN ({placeholders})",
            valid_list,
        )
        route_line_names = {}
        for journey_id, route_id, line_name, destination_display in cur.fetchall():
            j_int = bd.map_journeys.code_to_int.get(journey_id)
            if j_int is not None:
                bd.journey_metadata[j_int] = {
                    "journey_id": journey_id,
                    "route_id":   route_id,
                    "line_name":  line_name or "",
                    "destination_display": destination_display or "",
                }
            if route_id not in route_line_names or not route_line_names[route_id]:
                route_line_names[route_id] = line_name or ""

        for route_id, line_name in route_line_names.items():
            r_int = bd.map_routes.code_to_int.get(route_id)
            if r_int is not None:
                bd.route_metadata[r_int] = {
                    "route_id":  route_id,
                    "line_name": line_name,
                }

        conn.close()
        return bd

    # ── Stop name lookups (from stop_names table) ────────────────

    def get_stop_name(self, atco_code):
        """Return the common name for a single ATCO code, or None."""
        conn = self._connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT common_name FROM bus_stop_names WHERE atco_code = %s", (atco_code,))
        row = cur.fetchone()
        conn.close()
        return row[0] if row else None

    def get_stop_names_bulk(self, atco_codes):
        """Return a dict {atco_code: common_name} for all given codes.

        Uses a single query for efficiency.
        """
        if not atco_codes:
            return {}
        conn = self._connect(self.db_path)
        cur = conn.cursor()
        placeholders = ','.join(['%s'] * len(atco_codes))
        cur.execute(
            f"SELECT atco_code, common_name FROM bus_stop_names WHERE atco_code IN ({placeholders})",
            list(atco_codes)
        )
        result = {row[0]: row[1] for row in cur.fetchall()}
        conn.close()
        return result

    def get_stop_count(self):
        """Return the number of unique stop names in the database."""
        conn = self._connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM bus_stop_names")
        count = cur.fetchone()[0]
        conn.close()
        return count

    def search_stops(self, query, limit=10):
        """Search for stops by name with fuzzy matching.

        First tries an exact substring match (ILIKE).  When that
        returns fewer than *limit* results, a trigram similarity
        search supplements the list — so typos like "lancstr" still
        find "Lancaster Bus Station".

        Falls back to Python-side fuzzy scoring when the Postgres
        ``pg_trgm`` extension is unavailable.

        Args:
            query: search string (substring or approximate name)
            limit: maximum number of results to return

        Returns:
            list[dict]: matching stops with id, name, atco_code, lat, lon
        """
        if not query:
            return []

        conn = self._connect(self.db_path)
        cur = conn.cursor()

        # --- 1) exact ILIKE substring match (fast, no extension needed) ---
        cur.execute(
            "SELECT atco_code, common_name FROM bus_stop_names "
            "WHERE LOWER(common_name) LIKE LOWER(%s) LIMIT %s",
            (f"%{query}%", limit),
        )
        exact_rows = cur.fetchall()

        # --- 2) fuzzy supplement when exact results are sparse ----------
        fuzzy_rows = []
        if len(exact_rows) < limit:
            remaining = limit - len(exact_rows)
            exact_atcos = {r[0] for r in exact_rows}
            try:
                # Try pg_trgm similarity (fast, server-side)
                cur.execute(
                    "SELECT atco_code, common_name, "
                    "similarity(LOWER(common_name), LOWER(%s)) AS sim "
                    "FROM bus_stop_names "
                    "WHERE similarity(LOWER(common_name), LOWER(%s)) > 0.15 "
                    "ORDER BY sim DESC LIMIT %s",
                    (query, query, remaining + len(exact_rows)),
                )
                for atco, name, _sim in cur.fetchall():
                    if atco not in exact_atcos:
                        fuzzy_rows.append((atco, name))
                        if len(fuzzy_rows) >= remaining:
                            break
            except Exception:
                # pg_trgm not available — fall back to Python difflib
                conn.rollback()
                cur.execute(
                    "SELECT atco_code, common_name FROM bus_stop_names"
                )
                all_rows = cur.fetchall()
                from difflib import SequenceMatcher
                q_lower = query.lower()
                scored = []
                for atco, name in all_rows:
                    if atco in exact_atcos:
                        continue
                    ratio = SequenceMatcher(None, q_lower, name.lower()).ratio()
                    if ratio > 0.45:
                        scored.append((ratio, atco, name))
                scored.sort(key=lambda t: t[0], reverse=True)
                fuzzy_rows = [(atco, name) for _, atco, name in scored[:remaining]]

        conn.close()

        rows = list(exact_rows) + fuzzy_rows

        if not rows:
            return []

        atcos = [r[0] for r in rows]

        # Fetch coordinates from the walking DB if available
        coords_map = {}
        wdb = self.walking_db_path
        if wdb:
            try:
                wconn = self._connect(wdb)
                wcur = wconn.cursor()
                placeholders = ','.join(['%s'] * len(atcos))
                wcur.execute(f"SELECT atco_code, lat, lon FROM stop_coords WHERE atco_code IN ({placeholders})", atcos)
                for atco, lat, lon in wcur.fetchall():
                    coords_map[atco] = (lat, lon)
                wconn.close()
            except Exception:
                coords_map = {}

        results = []
        for i, (atco, name) in enumerate(rows):
            latlon = coords_map.get(atco, (None, None))
            results.append({
                "id": i,
                "name": name,
                "atco_code": atco,
                "lat": latlon[0],
                "lon": latlon[1],
            })
        return results

    # ── Pickle cache ─────────────────────────────────────────────

    @property
    def _cache_path(self):
        """Path of the pickle cache file, derived from the DB path."""
        base, _ = os.path.splitext(self.db_path)
        return base + ".cache"

    def _db_mtime(self):
        """Return the DB file's last-modified timestamp (epoch seconds)."""
        try:
            return os.path.getmtime(self.db_path)
        except OSError:
            return 0

    def save_cache(self, bus_data):
        """Pickle *bus_data* alongside the current DB mtime."""
        payload = {
            "db_mtime": self._db_mtime(),
            "bus_data": bus_data,
        }
        with open(self._cache_path, "wb") as f:
            pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)

    def load_cache(self):
        """Return the cached BusData if valid, else None.

        The cache is considered stale when:
          • the cache file does not exist, or
          • the DB file has been modified since the cache was written.
        """
        if not os.path.exists(self._cache_path):
            return None
        try:
            with open(self._cache_path, "rb") as f:
                payload = pickle.load(f)
            if payload.get("db_mtime") != self._db_mtime():
                return None                     # DB changed → stale
            return payload["bus_data"]
        except Exception:
            return None                         # corrupt / incompatible

    # ── NaPTAN stop coordinates ──────────────────────────────────

    # Walking-related functions have been moved to `walking_loader.py`.
    # Use WalkingLoader(db_path) for NaPTAN download and walking precomputation.