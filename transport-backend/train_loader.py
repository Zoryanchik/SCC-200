import psycopg
from train_data import TrainData
from datetime import datetime, date, timedelta
from collections import defaultdict
from atco_loader import NORTHWEST_ATCO_PREFIXES
import ssl
import os
import re
import urllib.request
import gzip
import io
import json

class TrainLoader:
    """Load, normalize, and cache train timetable data for routing.

    This loader downloads the daily rail schedule feed, maps TIPLOC
    locations to local ATCO stop codes, normalizes journey timings,
    and stores/retrieves per-day train journeys from Postgres cache.
    """

    def __init__(self, db_path, atco_db_path=None):
        """Initialize loader configuration.

        Args:
            db_path: PostgreSQL DSN for train cache storage.
            atco_db_path: Optional PostgreSQL DSN used to resolve ATCO
                stop metadata. When omitted, WALK_DB_DSN is used.

        Returns:
            None.
        """
        self.db_path = db_path
        self.schedule_url = "https://transport.scc.lancs.ac.uk/rail/schedule"
        self.atco_db_path = atco_db_path or os.environ.get('WALK_DB_DSN')

    def ensure_db(self):
        """Verify that the configured train database is reachable.

        Args:
            None.

        Returns:
            None.
        """
        # Ensure a connection can be opened to the Postgres DSN
        conn = psycopg.connect(self.db_path)
        conn.close()

    def create_schema(self):
        """Create train cache schema objects if they do not already exist.

        Args:
            None.

        Returns:
            None.
        """
        # Create the minimal schema in Postgres
        conn = psycopg.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            '''CREATE TABLE IF NOT EXISTS train_dataset_meta (
                source_url   TEXT PRIMARY KEY,
                download_url TEXT NOT NULL,
                modified     TEXT NOT NULL
            );'''
        )
        cur.execute(
            '''CREATE TABLE IF NOT EXISTS train_journey_cache (
                service_date        TEXT NOT NULL,
                journey_id          TEXT NOT NULL,
                route_id            TEXT NOT NULL,
                route_stops_json    JSONB NOT NULL,
                journey_times_json  JSONB NOT NULL,
                route_metadata_json JSONB,
                journey_metadata_json JSONB,
                cached_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (service_date, journey_id)
            );'''
        )
        cur.execute(
            '''CREATE INDEX IF NOT EXISTS idx_train_journey_cache_service_date
               ON train_journey_cache(service_date);'''
        )
        conn.commit()
        conn.close()

    @staticmethod
    def _normalize_station_text(text):
        """Normalize free-text station labels for dictionary matching.

        Args:
            text: Raw station-like label value.

        Returns:
            Normalized lowercase label with extra whitespace removed and
            the word station stripped, or empty string when text is empty.
        """
        if not text:
            return ''
        normalized = str(text).strip().lower()
        normalized = re.sub(r'\bstation\b', '', normalized)
        normalized = re.sub(r'\s+', ' ', normalized)
        return normalized.strip()

    @staticmethod
    def _rail_place_alias(text):
        """Return a place-name alias for rail-station labels.

        Example: "Lancaster Railway Station" -> "lancaster".
        Returns empty string for non-rail labels so broad town aliases are not
        generated for unrelated stops.

        Args:
            text: Raw stop/station label.

        Returns:
            Relaxed place alias for rail labels, otherwise empty string.
        """
        normalized = TrainLoader._normalize_station_text(text)
        if not normalized:
            return ''

        if not re.search(r'\b(rail|railway|train|station)\b', normalized):
            return ''

        alias = re.sub(r'\b(rail|railway|train|station)\b', ' ', normalized)
        alias = re.sub(r'\s+', ' ', alias).strip()
        if len(alias) < 3:
            return ''
        return alias

    @staticmethod
    def _is_allowed_atco(atco_code):
        """Check whether an ATCO code belongs to allowed regional prefixes.

        Args:
            atco_code: ATCO stop code.

        Returns:
            True when the code is present and starts with an allowed prefix;
            otherwise False.
        """
        return bool(atco_code) and atco_code.startswith(NORTHWEST_ATCO_PREFIXES)

    def _load_atco_lookup(self):
        """Load ATCO lookup dictionaries used for TIPLOC resolution.

        Args:
            None.

        Returns:
            Tuple of:
            - label_to_atcos: mapping of normalized labels to candidate ATCOs
            - atco_to_label: mapping of ATCO code to display label
            - atco_meta: mapping of ATCO code to metadata dict
        """
        if not self.atco_db_path:
            return {}, {}, {}

        try:
            conn = psycopg.connect(self.atco_db_path)
            cur = conn.cursor()
            cur.execute(
                "SELECT atco_code, name, town, stop_type "
                "FROM stop_coords "
                "ORDER BY CASE WHEN stop_type = 'train' THEN 0 ELSE 1 END, atco_code"
            )
            rows = cur.fetchall()
            conn.close()
        except Exception as exc:
            print(f"  [train] ATCO lookup unavailable: {exc}")
            return {}, {}, {}

        label_to_atcos = defaultdict(list)
        atco_to_label = {}
        atco_meta = {}
        for atco_code, name, town, stop_type in rows:
            label = name or town or atco_code
            atco_to_label[atco_code] = label
            atco_meta[atco_code] = {
                'name': name or '',
                'town': town or '',
                'stop_type': (stop_type or '').strip().lower(),
            }
            candidates = {atco_code}
            # Match train TIPLOCs by stop-specific labels only.
            # Including town-level aliases (e.g. "Lancaster") can map a
            # rail TIPLOC to unrelated same-town stops such as bus stands.
            for candidate in (name,):
                normalized = self._normalize_station_text(candidate)
                if normalized:
                    candidates.add(normalized)
                relaxed = self._rail_place_alias(candidate)
                if relaxed:
                    candidates.add(relaxed)
            for candidate in candidates:
                if atco_code not in label_to_atcos[candidate]:
                    label_to_atcos[candidate].append(atco_code)

        return label_to_atcos, atco_to_label, atco_meta

    @staticmethod
    def _train_stop_score(atco_code, atco_meta):
        """Compute preference score for choosing an ATCO as a rail stop.

        Args:
            atco_code: Candidate ATCO code.
            atco_meta: Metadata dictionary keyed by ATCO code.

        Returns:
            Integer score where higher values indicate better train-stop match.
        """
        meta = atco_meta.get(atco_code) or {}
        name = str(meta.get('name') or '').strip().lower()
        stop_type = str(meta.get('stop_type') or '').strip().lower()

        score = 0
        if stop_type == 'train':
            score += 100
        if 'rail' in name or 'train' in name:
            score += 50
        if 'station' in name:
            score += 25

        if 'taxi rank' in name:
            score -= 140
        if 'bus station' in name or 'stand' in name or 'bay ' in f'{name} ':
            score -= 80

        return score

    def _resolve_tiploc_to_atco(self, tiploc_info, tiploc_code, label_to_atcos, atco_meta):
        """Resolve a TIPLOC code to the best ATCO stop candidate.

        Args:
            tiploc_info: Mapping of TIPLOC code to TIPLOC metadata.
            tiploc_code: TIPLOC code to resolve.
            label_to_atcos: Normalized label to candidate ATCO list map.
            atco_meta: Candidate ATCO metadata for scoring.

        Returns:
            Selected ATCO code string, or None when no candidate is found.
        """
        info = tiploc_info.get(tiploc_code) or {}
        candidates = [
            info.get('tps_description'),
            info.get('description'),
            info.get('crs_code'),
            tiploc_code,
        ]
        for candidate in candidates:
            normalized = self._normalize_station_text(candidate)
            if not normalized:
                continue
            matches = label_to_atcos.get(normalized)
            if matches:
                allowed = [code for code in matches if self._is_allowed_atco(code)]
                if allowed:
                    return max(allowed, key=lambda code: self._train_stop_score(code, atco_meta))
                return matches[0]
        return None

    def _route_stays_in_nw(self, route_stops):
        """Validate that all route stops stay within allowed ATCO prefixes.

        Args:
            route_stops: Ordered list of ATCO stop codes for one route.

        Returns:
            True when the route has at least one stop and all stops are
            in the allowed region; otherwise False.
        """
        return bool(route_stops) and all(self._is_allowed_atco(code) for code in route_stops)

    def _load_cached_traindata(self, service_date):
        """Load cached TrainData for a specific service date.

        Args:
            service_date: Date string in YYYY-MM-DD format.

        Returns:
            TrainData object when cache rows are available and usable;
            otherwise None.
        """
        try:
            conn = psycopg.connect(self.db_path)
            cur = conn.cursor()
            cur.execute(
                '''SELECT journey_id, route_id, route_stops_json, journey_times_json,
                          route_metadata_json, journey_metadata_json
                   FROM train_journey_cache
                   WHERE service_date = %s
                   ORDER BY journey_id''',
                (service_date,)
            )
            rows = cur.fetchall()
            conn.close()
        except Exception as exc:
            print(f"  [train] Cache read failed for {service_date}: {exc}")
            return None

        if not rows:
            return None

        route_to_journeys = defaultdict(list)
        route_metadata_seen = {}
        journey_metadata_seen = {}
        train_data = TrainData(num_routes=0, num_journeys=0, num_stops=0)

        for journey_id, route_id, route_stops_json, journey_times_json, route_meta_json, journey_meta_json in rows:
            route_stops = route_stops_json or []
            journey_times = journey_times_json or []
            if not self._route_stays_in_nw(route_stops):
                continue
            arrival_times = []
            departure_times = []

            for stop_row in journey_times:
                stop_code = stop_row.get('stop_code')
                arrival = stop_row.get('arrival')
                departure = stop_row.get('departure')
                if stop_code is None or arrival is None:
                    continue
                arrival_times.append((stop_code, int(arrival)))
                departure_times.append(int(departure) if departure is not None else int(arrival))

            if not route_stops or not arrival_times:
                continue

            arrival_times, departure_times = self._normalize_journey_times(arrival_times, departure_times)

            train_data.add_route_stop(route_id, route_stops)
            train_data.add_journey_times(journey_id, arrival_times, departure_times)
            route_to_journeys[route_id].append(journey_id)
            if route_meta_json is not None:
                route_metadata_seen[route_id] = route_meta_json
            if journey_meta_json is not None:
                journey_metadata_seen[journey_id] = journey_meta_json

        if not route_to_journeys:
            return None

        for route_id, journey_ids in route_to_journeys.items():
            train_data.add_route_journeys(route_id, journey_ids)

        for route_id, meta in route_metadata_seen.items():
            route_idx = train_data.map_routes.get_int(route_id)
            train_data.route_metadata[route_idx] = meta

        for journey_id, meta in journey_metadata_seen.items():
            journey_idx = train_data.map_journeys.get_int(journey_id)
            train_data.journey_metadata[journey_idx] = meta

        print(f"  [train] Cache hit for {service_date}: {len(rows)} journeys")
        return train_data

    def _save_cached_traindata(self, service_date, train_data):
        """Persist TrainData journeys into the date-scoped cache table.

        Args:
            service_date: Date string in YYYY-MM-DD format.
            train_data: TrainData object to serialize and store.

        Returns:
            None.
        """
        rows = []
        for journey_idx, row in enumerate(train_data.journey_times):
            route_idx = train_data.journey_to_route[journey_idx] if journey_idx < len(train_data.journey_to_route) else -1
            if route_idx is None or route_idx < 0:
                continue
            if route_idx >= len(train_data.route_stops):
                continue

            journey_id = train_data.map_journeys.get_code(journey_idx)
            route_id = train_data.map_routes.get_code(route_idx)

            route_stops = []
            for stop_int in train_data.route_stops[route_idx]:
                route_stops.append(train_data.map_stops.get_code(stop_int))

            journey_times = []
            for stop_int, arrival, departure in row:
                journey_times.append({
                    'stop_code': train_data.map_stops.get_code(stop_int),
                    'arrival': int(arrival),
                    'departure': int(departure),
                })

            route_meta = train_data.route_metadata[route_idx] if route_idx < len(train_data.route_metadata) else None
            journey_meta = train_data.journey_metadata[journey_idx] if journey_idx < len(train_data.journey_metadata) else None
            rows.append(
                (
                    service_date,
                    journey_id,
                    route_id,
                    json.dumps(route_stops),
                    json.dumps(journey_times),
                    json.dumps(route_meta) if route_meta is not None else None,
                    json.dumps(journey_meta) if journey_meta is not None else None,
                )
            )

        try:
            conn = psycopg.connect(self.db_path)
            cur = conn.cursor()
            cur.execute("DELETE FROM train_journey_cache WHERE service_date = %s", (service_date,))
            if rows:
                cur.executemany(
                    '''INSERT INTO train_journey_cache (
                           service_date, journey_id, route_id,
                           route_stops_json, journey_times_json,
                           route_metadata_json, journey_metadata_json
                       )
                       VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb)''',
                    rows
                )
            today = date.today().isoformat()
            yesterday = (date.today() - timedelta(days=1)).isoformat()
            cur.execute(
                '''DELETE FROM train_journey_cache
                   WHERE service_date NOT IN (%s, %s)''',
                (today, yesterday)
            )
            conn.commit()
            conn.close()
            print(f"  [train] Cache saved for {service_date}: {len(rows)} journeys")
        except Exception as exc:
            print(f"  [train] Cache write failed for {service_date}: {exc}")

    def load_traindata_for_date(self, date_str):
        """Load TrainData for one date, using cache when possible.

        Args:
            date_str: Date string in YYYY-MM-DD format.

        Returns:
            TrainData instance for the requested date. Non-today dates
            currently return cached data when available, otherwise empty data.
        """
        # Ensure cache tables exist even when this loader is used directly.
        self.create_schema()

        if date_str != datetime.today().strftime('%Y-%m-%d'):
            cached = self._load_cached_traindata(date_str)
            if cached is not None:
                return cached
            # Currently can only get data for current day
            print(f"  [train]: Fetch {date_str}; Return zero-data")
            return TrainData(num_routes=0, num_journeys=0, num_stops=0)

        print(f"  [train]: Fetch {date_str}")
        cached = self._load_cached_traindata(date_str)
        if cached is not None:
            return cached

        loaded = self.download_schedule_today()
        self._save_cached_traindata(date_str, loaded)
        return loaded

    @staticmethod
    def _parse_cif_time(value):
        """Parse CIF-style time text into seconds-from-midnight timeline.

        Args:
            value: Raw CIF time value (string-like), potentially including
                day-shift suffixes such as +1/-1.

        Returns:
            Integer seconds including day shift when parsed, otherwise None.
        """
        if value is None:
            return None

        text = str(value).strip()
        if not text:
            return None

        day_shift = 0
        shift_match = re.search(r'([+-])(\d+)$', text)
        if shift_match:
            day_shift = int(shift_match.group(2)) * (1 if shift_match.group(1) == '+' else -1)
            text = text[:shift_match.start()]

        digits = ''.join(ch for ch in text if ch.isdigit())
        if len(digits) < 4:
            return None

        hours = int(digits[:2])
        minutes = int(digits[2:4])
        if hours >= 24:
            day_shift += hours // 24
            hours %= 24

        return day_shift * 86400 + hours * 3600 + minutes * 60

    @staticmethod
    def _normalize_journey_times(arrival_times, departure_times):
        """Normalize a journey to a non-decreasing stop-time timeline.

        Args:
            arrival_times: List of (stop_code, arrival_seconds) tuples.
            departure_times: List of departure seconds aligned to arrivals.

        Returns:
            Tuple of (normalized_arrivals, normalized_departures) where
            cross-midnight rollovers are adjusted by +86400 as needed.
        """
        if not arrival_times:
            return [], []

        normalized_arrivals = []
        normalized_departures = []
        last_departure = None

        for idx, (stop_code, arrival) in enumerate(arrival_times):
            arr = int(arrival)
            dep = int(departure_times[idx]) if idx < len(departure_times) else arr

            while last_departure is not None and arr < last_departure:
                arr += 86400

            while dep < arr:
                dep += 86400

            normalized_arrivals.append((stop_code, arr))
            normalized_departures.append(dep)
            last_departure = dep

        return normalized_arrivals, normalized_departures

    @staticmethod
    def _stop_name(tiploc_info, code):
        """Return best-available display name for a TIPLOC code.

        Args:
            tiploc_info: Mapping of TIPLOC code to metadata dict.
            code: TIPLOC code.

        Returns:
            Human-readable stop name fallback chain, or empty string.
        """
        info = tiploc_info.get(code) or {}
        return (
            info.get('tps_description')
            or info.get('description')
            or info.get('crs_code')
            or code
            or ''
        )

    @staticmethod
    def _schedule_identity(schedule, index):
        """Build a stable synthetic route/journey id for one schedule entry.

        Args:
            schedule: JsonScheduleV1 payload dictionary.
            index: Monotonic index used to guarantee uniqueness.

        Returns:
            Deterministic train-prefixed identifier string.
        """
        segment = schedule.get('schedule_segment') or {}
        parts = [
            schedule.get('CIF_train_uid'),
            segment.get('CIF_headcode'),
            segment.get('CIF_train_service_code'),
            schedule.get('schedule_start_date'),
            schedule.get('schedule_end_date'),
        ]
        label = '::'.join(str(part).strip() for part in parts if part)
        if not label:
            label = f'schedule-{index}'
        return f'train::{label}::{index}'

    def download_schedule_today(self):
        """Download today's compressed rail schedule and parse it.

        Args:
            None.

        Returns:
            TrainData built from the downloaded schedule content.
        """
        # Get https://transport.scc.lancs.ac.uk/rail/schedule
        # Then unzip, parse it, and cache it
        print(f'  [train] Downloading schedule...')
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        resp = urllib.request.urlopen(self.schedule_url, context=ctx, timeout=180)
        data = resp.read()
        print(f'  [train] (schedule) Downloaded {len(data) / 1024 / 1024:.1f} MB')

        return self.load_schedule_file(data)

    def load_schedule_file(self, file_content):
        """Parse gzipped line-delimited schedule content into TrainData.

        Args:
            file_content: Raw bytes from the compressed schedule endpoint.

        Returns:
            TrainData containing parsed routes, journeys, timings, and metadata.
        """
        train_data = TrainData(num_routes=0, num_journeys=0, num_stops=0)
        tiploc_info = {}
        label_to_atcos, atco_to_label, atco_meta = self._load_atco_lookup()
        route_count = 0
        journey_count = 0

        print('  [train] (schedule) Unzipping..')
        with gzip.GzipFile(fileobj=io.BytesIO(file_content), mode='rb') as gzf:
            for line in gzf:
                if not line.strip():
                    continue

                try:
                    record = json.loads(line.decode('utf-8'))
                except json.JSONDecodeError:
                    continue

                if not record:
                    continue

                record_type = next(iter(record))
                payload = record.get(record_type) or {}

                if record_type == 'TiplocV1':
                    code = (payload.get('tiploc_code') or '').strip()
                    if code:
                        tiploc_info[code] = payload
                    continue

                if record_type != 'JsonScheduleV1':
                    continue

                if str(payload.get('transaction_type') or '').lower() == 'delete':
                    continue

                segment = payload.get('schedule_segment') or {}
                locations = segment.get('schedule_location') or []
                if not locations:
                    continue

                stop_codes = []
                arrival_times = []
                departure_times = []

                for location in locations:
                    tiploc_code = (location.get('tiploc_code') or '').strip()
                    if not tiploc_code:
                        continue

                    atco_code = self._resolve_tiploc_to_atco(tiploc_info, tiploc_code, label_to_atcos, atco_meta)
                    if not atco_code:
                        stop_codes = []
                        break

                    arrival = self._parse_cif_time(
                        location.get('arrival')
                        or location.get('public_arrival')
                        or location.get('pass')
                        or location.get('departure')
                        or location.get('public_departure')
                    )
                    departure = self._parse_cif_time(
                        location.get('departure')
                        or location.get('public_departure')
                        or location.get('pass')
                        or location.get('arrival')
                        or location.get('public_arrival')
                    )

                    if arrival is None and departure is None:
                        continue

                    if arrival is None:
                        arrival = departure
                    if departure is None:
                        departure = arrival

                    stop_codes.append(atco_code)
                    arrival_times.append((atco_code, arrival))
                    departure_times.append(departure)

                if not stop_codes:
                    continue

                if not all(self._is_allowed_atco(code) for code in stop_codes):
                    continue

                arrival_times, departure_times = self._normalize_journey_times(arrival_times, departure_times)

                route_id = self._schedule_identity(payload, route_count)
                route_count += 1

                train_data.add_route_stop(route_id, stop_codes)
                train_data.add_route_journeys(route_id, [route_id])
                train_data.add_journey_times(route_id, arrival_times, departure_times)

                route_idx = train_data.map_routes.get_int(route_id)
                journey_idx = train_data.map_journeys.get_int(route_id)
                origin_code = stop_codes[0]
                destination_code = stop_codes[-1]
                origin_name = atco_to_label.get(origin_code, origin_code)
                destination_name = atco_to_label.get(destination_code, destination_code)
                segment_data = {
                    'route_id': route_id,
                    'line_name': f'{origin_name} to {destination_name}',
                    'train_uid': payload.get('CIF_train_uid'),
                    'headcode': segment.get('CIF_headcode'),
                    'service_code': segment.get('CIF_train_service_code'),
                    'signalling_id': segment.get('signalling_id'),
                    'origin_code': origin_code,
                    'origin_name': origin_name,
                    'destination_code': destination_code,
                    'destination_name': destination_name,
                    'schedule_start_date': payload.get('schedule_start_date'),
                    'schedule_end_date': payload.get('schedule_end_date'),
                    'schedule_days_runs': payload.get('schedule_days_runs'),
                    'stp_indicator': payload.get('CIF_stp_indicator'),
                    'owner': payload.get('atoc_code'),
                }
                train_data.route_metadata[route_idx] = dict(segment_data)
                train_data.journey_metadata[journey_idx] = dict(segment_data)
                journey_count += 1

        print('  [train] (schedule) Unzipped')
        print(f'  [train] (schedule) Loaded {route_count} routes / {journey_count} journeys')

        return train_data

        
