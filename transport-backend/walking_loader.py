import bisect
import json
import os
import ssl
import psycopg
from psycopg.rows import dict_row
import urllib.request
import re
import shutil
import math
import threading

class WalkingLoader:
    def __init__(self, db_path):
        self.db_path = db_path
        # Precompute progress state
        self.precompute_thread = None
        self.precomputing = False
        self.precompute_total = 0
        self.precompute_processed = 0
        self.precompute_inserted = 0
        self.precompute_use_fallback = False

    def _connect(self, path=None):
        db = path or self.db_path
        # Postgres-only: always connect via psycopg
        pg_conn = psycopg.connect(db)
        return pg_conn

    def create_schema(self):
        """Create walking DB tables: stop_coords and walking_transfers."""
        conn = self._connect(self.db_path)
        schema = '''
            CREATE TABLE IF NOT EXISTS stop_coords (
                atco_code   TEXT PRIMARY KEY,
                lat         REAL NOT NULL,
                lon         REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS walking_transfers (
                from_atco   TEXT NOT NULL,
                to_atco     TEXT NOT NULL,
                walk_seconds INTEGER NOT NULL,
                PRIMARY KEY (from_atco, to_atco)
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

    def download_stop_coords(self):
        """Download NaPTAN XML and populate stop_coords with every AtcoCode.

        Caches the XML locally to avoid repeated large downloads. Uses
        INSERT OR REPLACE so the operation is idempotent.
        """
        # When db_path is a DSN (Postgres) it isn't a filesystem path.
        # Use a repository-local cache directory for downloaded XML so
        # caching doesn't depend on the DB path type.
        repo_cache = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
        os.makedirs(repo_cache, exist_ok=True)
        xml_path = os.path.join(repo_cache, "naptan.xml")

        # Download only if local file missing / too small
        if not os.path.exists(xml_path) or os.path.getsize(xml_path) < 1_000_000:
            print("  Downloading NaPTAN XML (≈100 MB)...")
            url = "https://transport.scc.lancs.ac.uk//nptg/naptan.xml"
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            try:
                with urllib.request.urlopen(url, context=ctx, timeout=180) as resp, open(xml_path, "wb") as out:
                    shutil.copyfileobj(resp, out)
            except Exception as e:
                raise RuntimeError(f"NaPTAN download failed: {e}")
        else:
            print("  Using cached NaPTAN XML...")

        print(f"  Parsing {os.path.getsize(xml_path)//1024}KB...")
        xml_map = {}
        try:
            with open(xml_path, 'r', encoding='utf-8') as fh:
                data = fh.read()

            # Find StopPoint blocks and extract AtcoCode, Latitude, Longitude
            for m in re.finditer(r'<StopPoint\b.*?</StopPoint>', data, flags=re.DOTALL):
                block = m.group(0)
                atco_m = re.search(r'<AtcoCode>\s*([^<\s]+)\s*</AtcoCode>', block)
                lat_m = re.search(r'<Latitude>\s*([^<\s]+)\s*</Latitude>', block)
                lon_m = re.search(r'<Longitude>\s*([^<\s]+)\s*</Longitude>', block)
                if not atco_m or not lat_m or not lon_m:
                    continue
                try:
                    atco = atco_m.group(1).strip()
                    lat = float(lat_m.group(1).strip())
                    lon = float(lon_m.group(1).strip())
                except Exception:
                    continue
                xml_map[atco] = (lat, lon)
        except Exception as e:
            raise RuntimeError(f"Error parsing NaPTAN XML: {e}")

        rows = [(atco, lat, lon) for atco, (lat, lon) in xml_map.items()]

        if not rows:
            print("  ✗ No coordinates found in NaPTAN XML")
            return

        conn = self._connect(self.db_path)
        cur = conn.cursor()
        cur.executemany(
            "INSERT INTO stop_coords (atco_code, lat, lon) VALUES (%s, %s, %s) "
            "ON CONFLICT (atco_code) DO UPDATE SET lat = EXCLUDED.lat, lon = EXCLUDED.lon",
            rows,
        )
        conn.commit()
        conn.close()
        print(f"  ✓ Coordinates loaded for {len(rows)} stops")

    def get_all_stop_coords(self):
        """Return dict {atco_code: (lat, lon)} for all stops with coords."""
        conn = self._connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT atco_code, lat, lon FROM stop_coords")
        result = {row[0]: (row[1], row[2]) for row in cur.fetchall()}
        conn.close()
        return result

    def precompute_walking_transfers(self, osrm_base="http://localhost:5012",
                                      max_walk_seconds=600,
                                      bbox_margin=0.012):
        """Precompute walking transfers between nearby stops using OSRM.

        For each stop, finds other stops within *bbox_margin* degrees,
        queries OSRM /table endpoint for walking durations, and stores pairs
        ≤ *max_walk_seconds* in the walking_transfers table.
        """
        # Mark progress state
        self.precomputing = True
        self.precompute_use_fallback = False

        conn = self._connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT from_atco FROM walking_transfers")
        done_sources = {r[0] for r in cur.fetchall()}
        conn.close()

        coords = self.get_all_stop_coords()
        if not coords:
            print("  ✗ No stop coordinates — run download_stop_coords first")
            return

        atco_list = list(coords.keys())
        lat_list = [coords[a][0] for a in atco_list]
        lon_list = [coords[a][1] for a in atco_list]

        indexed = sorted(range(len(atco_list)), key=lambda i: lat_list[i])
        sorted_lats = [lat_list[i] for i in indexed]

        total_targets = len(atco_list) - len(done_sources)
        self.precompute_total = max(0, total_targets)
        self.precompute_processed = 0
        self.precompute_inserted = 0
        print(f"  Precomputing walking transfers for {len(atco_list)} stops"
              f" ({len(done_sources)} already done)...")
        transfers = []
        processed = 0
        total_found = 0

        for idx_pos, src_idx in enumerate(indexed):
            src_atco = atco_list[src_idx]
            if src_atco in done_sources:
                continue

            src_lat = lat_list[src_idx]
            src_lon = lon_list[src_idx]

            lo = bisect.bisect_left(sorted_lats, src_lat - bbox_margin)
            hi = bisect.bisect_right(sorted_lats, src_lat + bbox_margin)

            neighbors = []
            for j in range(lo, hi):
                nb_idx = indexed[j]
                if nb_idx == src_idx:
                    continue
                if abs(lon_list[nb_idx] - src_lon) <= bbox_margin:
                    neighbors.append(nb_idx)

            if not neighbors:
                continue

            all_indices = [src_idx] + neighbors
            coord_str = ";".join(f"{lon_list[i]},{lat_list[i]}" for i in all_indices)
            osrm_url = f"{osrm_base}/table/v1/foot/{coord_str}?sources=0&annotations=duration"
            try:
                resp = urllib.request.urlopen(osrm_url, timeout=10)
                data = json.loads(resp.read())
                resp.close()
            except Exception:
                continue

            if data.get("code") != "Ok":
                continue

            durations = data["durations"][0]
            for k, dur in enumerate(durations):
                if k == 0:
                    continue
                if dur is None or dur > max_walk_seconds:
                    continue
                nb_idx = all_indices[k]
                dst_atco = atco_list[nb_idx]
                transfers.append((src_atco, dst_atco, int(dur)))

            processed += 1
            # update progress
            self.precompute_processed = processed
            if processed % 500 == 0:
                conn = self._connect(self.db_path)
                cur = conn.cursor()
                cur.executemany(
                    "INSERT INTO walking_transfers "
                    "(from_atco, to_atco, walk_seconds) VALUES (%s, %s, %s) "
                    "ON CONFLICT (from_atco, to_atco) DO UPDATE SET walk_seconds = EXCLUDED.walk_seconds",
                    transfers,
                )
                conn.commit()
                conn.close()
                total_found += len(transfers)
                self.precompute_inserted = total_found
                transfers = []
                print(f"    [{processed}/{len(atco_list) - len(done_sources)}]"
                      f" {total_found} transfers saved")

        if transfers:
            conn = self._connect(self.db_path)
            cur = conn.cursor()
            cur.executemany(
                "INSERT INTO walking_transfers "
                "(from_atco, to_atco, walk_seconds) VALUES (%s, %s, %s) "
                "ON CONFLICT (from_atco, to_atco) DO UPDATE SET walk_seconds = EXCLUDED.walk_seconds",
                transfers,
            )
            conn.commit()
            conn.close()
            total_found += len(transfers)
            self.precompute_inserted = total_found
        print(f"  ✓ {total_found} walking transfers stored")
        # clear running flag
        self.precomputing = False

    def precompute_walking_transfers_fallback(self, max_walk_seconds=600,
                                              walk_speed_mps=1.4,
                                              bbox_margin=0.012):
        """Fallback precompute using straight-line (haversine) distances.

        This computes approximate walk seconds = distance_m / walk_speed_mps
        for nearby stops (selected via the same latitude bbox heuristic used
        for OSRM), and inserts pairs whose computed time ≤ max_walk_seconds.
        """
        def haversine_m(lat1, lon1, lat2, lon2):
            R = 6371000.0
            phi1 = math.radians(lat1)
            phi2 = math.radians(lat2)
            dphi = math.radians(lat2 - lat1)
            dlambda = math.radians(lon2 - lon1)
            a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
            c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
            return R * c

        # Mark progress state
        self.precomputing = True
        self.precompute_use_fallback = True

        conn = self._connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT from_atco FROM walking_transfers")
        done_sources = {r[0] for r in cur.fetchall()}
        conn.close()

        coords = self.get_all_stop_coords()
        if not coords:
            print("  ✗ No stop coordinates — run download_stop_coords first")
            return

        atco_list = list(coords.keys())
        lat_list = [coords[a][0] for a in atco_list]
        lon_list = [coords[a][1] for a in atco_list]

        indexed = sorted(range(len(atco_list)), key=lambda i: lat_list[i])
        sorted_lats = [lat_list[i] for i in indexed]

        total_targets = len(atco_list) - len(done_sources)
        self.precompute_total = max(0, total_targets)
        self.precompute_processed = 0
        self.precompute_inserted = 0
        print(f"  Fallback: computing approx walking transfers for {len(atco_list)} stops"
              f" ({len(done_sources)} already done)...")
        transfers = []
        processed = 0
        total_found = 0

        for idx_pos, src_idx in enumerate(indexed):
            src_atco = atco_list[src_idx]
            if src_atco in done_sources:
                continue

            src_lat = lat_list[src_idx]
            src_lon = lon_list[src_idx]

            lo = bisect.bisect_left(sorted_lats, src_lat - bbox_margin)
            hi = bisect.bisect_right(sorted_lats, src_lat + bbox_margin)

            neighbors = []
            for j in range(lo, hi):
                nb_idx = indexed[j]
                if nb_idx == src_idx:
                    continue
                if abs(lon_list[nb_idx] - src_lon) <= bbox_margin:
                    neighbors.append(nb_idx)

            if not neighbors:
                continue

            for nb_idx in neighbors:
                dst_atco = atco_list[nb_idx]
                lat2 = lat_list[nb_idx]
                lon2 = lon_list[nb_idx]
                dist_m = haversine_m(src_lat, src_lon, lat2, lon2)
                secs = int(dist_m / walk_speed_mps)
                if secs <= 0 or secs > max_walk_seconds:
                    continue
                transfers.append((src_atco, dst_atco, secs))

            processed += 1
            # update progress
            self.precompute_processed = processed
            if processed % 500 == 0:
                conn = self._connect(self.db_path)
                cur = conn.cursor()
                cur.executemany(
                    "INSERT INTO walking_transfers "
                    "(from_atco, to_atco, walk_seconds) VALUES (%s, %s, %s) "
                    "ON CONFLICT (from_atco, to_atco) DO UPDATE SET walk_seconds = EXCLUDED.walk_seconds",
                    transfers,
                )
                conn.commit()
                conn.close()
                total_found += len(transfers)
                self.precompute_inserted = total_found
                transfers = []
                print(f"    [{processed}/{len(atco_list) - len(done_sources)}]"
                      f" {total_found} transfers saved (approx)")

        if transfers:
            conn = self._connect(self.db_path)
            cur = conn.cursor()
            cur.executemany(
                "INSERT INTO walking_transfers "
                "(from_atco, to_atco, walk_seconds) VALUES (%s, %s, %s) "
                "ON CONFLICT (from_atco, to_atco) DO UPDATE SET walk_seconds = EXCLUDED.walk_seconds",
                transfers,
            )
            conn.commit()
            conn.close()
            total_found += len(transfers)
            self.precompute_inserted = total_found
        print(f"  ✓ {total_found} approx walking transfers stored")
        # clear running flag
        self.precomputing = False

    def start_precompute_background(self, osrm_base=None, use_fallback=False, **kwargs):
        """Start precomputing walking transfers in a background thread.

        If use_fallback is True, the haversine approximation is used.
        Additional kwargs are passed to the precompute function.
        """
        if self.precomputing:
            return self.precompute_thread

        def _worker():
            try:
                if use_fallback:
                    self.precompute_walking_transfers_fallback(**kwargs)
                else:
                    self.precompute_walking_transfers(osrm_base=osrm_base, **kwargs)
            except Exception as e:
                # Log to stdout — caller can inspect flags
                print(f"  Precompute background error: {e}")
            finally:
                self.precomputing = False

        th = threading.Thread(target=_worker, name="walking-precompute", daemon=True)
        self.precompute_thread = th
        th.start()
        return th

    def clear_walking_transfers(self):
        """Remove all precomputed walking transfers (e.g. after data update)."""
        conn = self._connect(self.db_path)
        cur = conn.cursor()
        cur.execute("DELETE FROM walking_transfers")
        cur.execute("DELETE FROM stop_coords")
        conn.commit()
        conn.close()

    def get_walking_transfers(self):
        """Return dict {from_atco: {to_atco: walk_seconds}}."""
        conn = self._connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT from_atco, to_atco, walk_seconds FROM walking_transfers")
        result = {}
        for from_a, to_a, secs in cur.fetchall():
            result.setdefault(from_a, {})[to_a] = secs
        conn.close()
        return result
