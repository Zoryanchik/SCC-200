import psycopg
from train_data import TrainData
from datetime import datetime, date, timedelta
from collections import defaultdict
from atco_loader import NORTHWEST_ATCO_PREFIXES
import ssl
import os
import re
import urllib.error
import urllib.request
import gzip
import io
import json
import hashlib

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
            - label_to_atcos: mapping of ATCO-like identifiers to candidate ATCOs
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
            # ATCO-only lookup keys. Do not index by stop names.
            candidates = {
                str(atco_code),
                str(atco_code).upper(),
                str(atco_code).lower(),
            }
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
        """Resolve a TIPLOC code to an ATCO stop using ATCO-coded fields only.

        Args:
            tiploc_info: Mapping of TIPLOC code to TIPLOC metadata.
            tiploc_code: TIPLOC code to resolve.
            label_to_atcos: ATCO-keyed candidate ATCO list map.
            atco_meta: Candidate ATCO metadata for scoring.

        Returns:
            Selected ATCO code string, or None when no candidate is found.
        """
        info = tiploc_info.get(tiploc_code) or {}
        direct_candidates = [
            info.get('atco_code'),
            info.get('atco'),
            info.get('naptan_atco'),
            info.get('naptan_code'),
            info.get('NaPTANAtcoCode'),
            tiploc_code,
        ]
        for candidate in direct_candidates:
            token = str(candidate or '').strip()
            if not token:
                continue
            keys = (token, token.upper(), token.lower())
            for key in keys:
                matches = label_to_atcos.get(key)
                if not matches:
                    continue
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
            True when the route has at least one stop and at least one stop
            is in the allowed region; otherwise False.
        """
        return bool(route_stops) and any(self._is_allowed_atco(code) for code in route_stops)

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
            tomorrow = (date.today() + timedelta(days=1)).isoformat()
            cur.execute(
                '''DELETE FROM train_journey_cache
                   WHERE service_date NOT IN (%s, %s, %s)''',
                (today, yesterday, tomorrow)
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
            TrainData instance for the requested date.

            - For today's date: cache-first, then download/parse and cache.
            - For non-today dates: cache-only; on miss returns empty TrainData.

            This keeps startup and adjacent-day prebuilds fast and avoids
            network fetches for non-today dates.
        """
        # Ensure cache tables exist even when this loader is used directly.
        self.create_schema()

        print(f"  [train]: Fetch {date_str}")
        cached = self._load_cached_traindata(date_str)
        if cached is not None:
            return cached

        today_str = datetime.today().strftime('%Y-%m-%d')
        if date_str != today_str:
            print(f"  [train]: Cache miss for {date_str}; no non-today download")
            return TrainData(num_routes=0, num_journeys=0, num_stops=0)

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
    def _journey_identity(schedule, index):
        """Build a stable synthetic journey id for one schedule entry.

        Args:
            schedule: JsonScheduleV1 payload dictionary.
            index: Monotonic index used to guarantee uniqueness.

        Returns:
            Deterministic train-prefixed journey identifier string.
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
        return f'train_j::{label}::{index}'

    @staticmethod
    def _route_identity(schedule, stop_codes):
        """Build a route-pattern id shared by journeys with same stop sequence.

        Args:
            schedule: JsonScheduleV1 payload dictionary.
            stop_codes: Ordered resolved ATCO code sequence for this schedule.

        Returns:
            Deterministic train-prefixed route identifier string.
        """
        segment = schedule.get('schedule_segment') or {}
        origin = (stop_codes[0] if stop_codes else '')
        destination = (stop_codes[-1] if stop_codes else '')
        signature = '|'.join(str(code).strip() for code in (stop_codes or []) if code)
        sig_hash = hashlib.sha1(signature.encode('utf-8')).hexdigest()[:12] if signature else 'nosig'
        parts = [
            schedule.get('atoc_code'),
            origin,
            destination,
            segment.get('signalling_id'),
            str(len(stop_codes or [])),
            sig_hash,
        ]
        label = '::'.join(str(part).strip() for part in parts if str(part).strip())
        if not label:
            label = f'route-{sig_hash}'
        return f'train_r::{label}'

    def download_schedule_today(self, target_date=None):
        """Download today's compressed rail schedule and parse it.

        Args:
            target_date: Optional YYYY-MM-DD. When provided, applies
                operational-day filtering for that date.

        Returns:
            TrainData built from the downloaded schedule content.
        """
        # Get https://transport.scc.lancs.ac.uk/rail/schedule
        # Then unzip, parse it, and cache it
        data = self.download_schedule_raw()
        if data is None:
            return TrainData(num_routes=0, num_journeys=0, num_stops=0)
        return self.load_schedule_file(data, target_date=target_date)

    def download_schedule_raw(self):
        """Download raw gzipped schedule bytes from the schedule URL.

        Returns:
            Raw bytes on success, or None on failure.
        """
        print(f'  [train] Downloading schedule...')
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        try:
            resp = urllib.request.urlopen(self.schedule_url, context=ctx, timeout=180)
            data = resp.read()
        except urllib.error.HTTPError as exc:
            code = getattr(exc, 'code', None)
            print(f"  [train] ⚠ Schedule URL returned HTTP {code} — skipping: {self.schedule_url}")
            return None
        except urllib.error.URLError as exc:
            print(f"  [train] ⚠ Schedule URL unreachable ({exc}) — skipping: {self.schedule_url}")
            return None
        print(f'  [train] (schedule) Downloaded {len(data) / 1024 / 1024:.1f} MB')
        return data

    def load_schedule_file(self, file_content, target_date=None):
        """Parse gzipped line-delimited schedule content into TrainData.

        Args:
            file_content: Raw bytes from the compressed schedule endpoint.

        Returns:
            TrainData containing parsed routes, journeys, timings, and metadata.
        """
        train_data = TrainData(num_routes=0, num_journeys=0, num_stops=0)
        tiploc_info = {}
        label_to_atcos, atco_to_label, atco_meta = self._load_atco_lookup()
        route_to_journeys = defaultdict(list)
        route_meta_by_id = {}
        route_seen = set()
        journey_count = 0
        # Optional target-date filtering (YYYY-MM-DD). When provided, only
        # include schedules that are valid for that date (start/end/runs).
        target_dt = None
        target_wd = None
        if target_date:
            try:
                target_dt = datetime.fromisoformat(target_date).date()
                target_wd = target_dt.weekday()  # Mon=0
            except Exception:
                target_dt = None
                target_wd = None

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
                # If a target date was requested, validate schedule applicability
                if target_dt is not None:
                    s_str = payload.get('schedule_start_date')
                    e_str = payload.get('schedule_end_date')
                    runs = (payload.get('schedule_days_runs') or '').strip()
                    try:
                        s_dt = datetime.fromisoformat(s_str).date() if s_str else None
                    except Exception:
                        s_dt = None
                    try:
                        e_dt = datetime.fromisoformat(e_str).date() if e_str else None
                    except Exception:
                        e_dt = None

                    # If end date missing, treat as one year from start (or from target)
                    if e_dt is None:
                        anchor = s_dt if s_dt is not None else target_dt
                        e_dt = anchor + timedelta(days=365)

                    if s_dt and target_dt < s_dt:
                        continue
                    if e_dt and target_dt > e_dt:
                        continue
                    if len(runs) == 7 and target_wd is not None and runs[target_wd] != '1':
                        continue
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
                        # Keep parsing the schedule even when a TIPLOC cannot be
                        # mapped (common for junction-only timing points). This
                        # preserves full passenger-stop routes instead of
                        # collapsing to endpoints due to one unmapped location.
                        continue

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

                    # Some timing points can resolve to the same ATCO as the
                    # previous location; collapse consecutive duplicates while
                    # keeping the latest timestamp pair.
                    if stop_codes and stop_codes[-1] == atco_code:
                        arrival_times[-1] = (atco_code, arrival)
                        departure_times[-1] = departure
                        continue

                    stop_codes.append(atco_code)
                    arrival_times.append((atco_code, arrival))
                    departure_times.append(departure)

                if len(stop_codes) < 2:
                    continue

                if not self._route_stays_in_nw(stop_codes):
                    continue

                arrival_times, departure_times = self._normalize_journey_times(arrival_times, departure_times)

                journey_id = self._journey_identity(payload, journey_count)
                route_id = self._route_identity(payload, stop_codes)

                if route_id not in route_seen:
                    train_data.add_route_stop(route_id, stop_codes)
                    route_seen.add(route_id)

                train_data.add_journey_times(journey_id, arrival_times, departure_times)
                route_to_journeys[route_id].append(journey_id)

                route_idx = train_data.map_routes.get_int(route_id)
                journey_idx = train_data.map_journeys.get_int(journey_id)
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
                route_meta_by_id.setdefault(route_id, dict(segment_data))
                train_data.journey_metadata[journey_idx] = dict(segment_data)
                journey_count += 1

        for route_id, journey_ids in route_to_journeys.items():
            train_data.add_route_journeys(route_id, journey_ids)
            route_idx = train_data.map_routes.get_int(route_id)
            if route_idx < len(train_data.route_metadata):
                train_data.route_metadata[route_idx] = route_meta_by_id.get(route_id)

        print('  [train] (schedule) Unzipped')
        print(f'  [train] (schedule) Loaded {len(route_to_journeys)} routes / {journey_count} journeys')

        return train_data

        
