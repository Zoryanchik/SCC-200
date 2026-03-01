"""ATCO stop data loader.

Owns the ``stop_coords`` table (with name and stop_type columns) and
the ``walking_transfers`` table.  Parses NaPTAN XML for coordinates,
common names (``<Descriptor><CommonName>``) and stop types
(``<StopClassification><StopType>`` — codes starting with ``B`` → bus,
``T`` → train, else ``other``).

Provides fast lookup helpers used by MergedData and Walking at runtime.
"""

import bisect
import json
import math
import os
import re
import shutil
import ssl
import threading

import psycopg
import urllib.request


class AtcoLoader:
    """Manage ATCO stop metadata: coords, names, types, walking transfers."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        # Background precompute state (walking transfers)
        self.precompute_thread = None
        self.precomputing = False
        self.precompute_total = 0
        self.precompute_processed = 0
        self.precompute_inserted = 0
        self.precompute_use_fallback = False

    # ── DB helpers ────────────────────────────────────────────────

    def _connect(self, path=None):
        return psycopg.connect(path or self.db_path)

    # ── Schema ────────────────────────────────────────────────────

    def create_schema(self):
        """Create ``stop_coords`` and ``walking_transfers`` tables."""
        conn = self._connect()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS stop_coords (
                atco_code  TEXT PRIMARY KEY,
                lat        REAL NOT NULL,
                lon        REAL NOT NULL,
                name       TEXT,
                stop_type  TEXT
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS walking_transfers (
                from_atco    TEXT NOT NULL,
                to_atco      TEXT NOT NULL,
                walk_seconds INTEGER NOT NULL,
                PRIMARY KEY (from_atco, to_atco)
            );
        """)
        # Migrate older schemas that lack the new columns
        for col, coltype in [("name", "TEXT"), ("stop_type", "TEXT")]:
            try:
                cur.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'stop_coords' AND column_name = %s",
                    (col,),
                )
                if not cur.fetchone():
                    cur.execute(f"ALTER TABLE stop_coords ADD COLUMN {col} {coltype}")
            except Exception:
                pass
        conn.commit()
        conn.close()

    # ── NaPTAN download & parse ───────────────────────────────────

    def download_stop_coords(self):
        """Download NaPTAN XML and populate ``stop_coords``.

        Extracts per stop:
        * ``AtcoCode``, ``Latitude``, ``Longitude``
        * ``<Descriptor><CommonName>`` → ``name``
        * ``<StopClassification><StopType>`` → ``stop_type``
          (B* → bus, T* → train, else other)
        """
        repo_cache = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
        os.makedirs(repo_cache, exist_ok=True)
        xml_path = os.path.join(repo_cache, "naptan.xml")

        if not os.path.exists(xml_path) or os.path.getsize(xml_path) < 1_000_000:
            print("  Downloading NaPTAN XML (≈100 MB)...")
            url = "https://transport.scc.lancs.ac.uk//nptg/naptan.xml"
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            try:
                with urllib.request.urlopen(url, context=ctx, timeout=180) as resp, \
                     open(xml_path, "wb") as out:
                    shutil.copyfileobj(resp, out)
            except Exception as e:
                raise RuntimeError(f"NaPTAN download failed: {e}")
        else:
            print("  Using cached NaPTAN XML...")

        print(f"  Parsing {os.path.getsize(xml_path) // 1024} KB...")

        rows = []
        try:
            with open(xml_path, "r", encoding="utf-8") as fh:
                data = fh.read()

            for m in re.finditer(r"<StopPoint\b.*?</StopPoint>", data, flags=re.DOTALL):
                block = m.group(0)
                atco_m = re.search(r"<AtcoCode>\s*([^<\s]+)\s*</AtcoCode>", block)
                lat_m = re.search(r"<Latitude>\s*([^<\s]+)\s*</Latitude>", block)
                lon_m = re.search(r"<Longitude>\s*([^<\s]+)\s*</Longitude>", block)
                if not atco_m or not lat_m or not lon_m:
                    continue
                try:
                    atco = atco_m.group(1).strip()
                    lat = float(lat_m.group(1).strip())
                    lon = float(lon_m.group(1).strip())
                except Exception:
                    continue

                # Common name
                name_m = re.search(
                    r"<Descriptor>.*?<CommonName>\s*([^<]+?)\s*</CommonName>",
                    block, flags=re.DOTALL,
                )
                name = name_m.group(1).strip() if name_m else None

                # Stop type code  (e.g. BCT, BCS, TXR, PLT …)
                type_m = re.search(
                    r"<StopClassification>.*?<StopType>\s*([^<\s]+)\s*</StopType>",
                    block, flags=re.DOTALL,
                )
                raw_type = type_m.group(1).strip().upper() if type_m else ""
                if raw_type.startswith("B"):
                    stop_type = "bus"
                elif raw_type.startswith("T"):
                    stop_type = "train"
                else:
                    stop_type = "other"

                rows.append((atco, lat, lon, name, stop_type))

        except Exception as e:
            raise RuntimeError(f"Error parsing NaPTAN XML: {e}")

        if not rows:
            print("  ✗ No coordinates found in NaPTAN XML")
            return

        conn = self._connect()
        cur = conn.cursor()
        cur.executemany(
            "INSERT INTO stop_coords (atco_code, lat, lon, name, stop_type) "
            "VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (atco_code) DO UPDATE SET "
            "lat = EXCLUDED.lat, lon = EXCLUDED.lon, "
            "name = EXCLUDED.name, stop_type = EXCLUDED.stop_type",
            rows,
        )
        conn.commit()
        conn.close()
        print(f"  ✓ Coordinates loaded for {len(rows)} stops")

    # ── Lookups ───────────────────────────────────────────────────

    def get_all_stop_coords(self) -> dict:
        """Return ``{atco_code: (lat, lon)}``."""
        conn = self._connect()
        cur = conn.cursor()
        cur.execute("SELECT atco_code, lat, lon FROM stop_coords")
        result = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
        conn.close()
        return result

    def get_stop_names_bulk(self, atco_codes) -> dict:
        """Return ``{atco_code: name}`` for the given codes."""
        if not atco_codes:
            return {}
        conn = self._connect()
        cur = conn.cursor()
        codes = list(atco_codes)
        ph = ",".join(["%s"] * len(codes))
        cur.execute(
            f"SELECT atco_code, name FROM stop_coords WHERE atco_code IN ({ph})",
            codes,
        )
        result = {r[0]: r[1] for r in cur.fetchall() if r[1]}
        conn.close()
        return result

    def get_stop_type(self, atco_code: str) -> str:
        """Return ``'bus'``, ``'train'``, or ``'other'`` for a single code."""
        conn = self._connect()
        cur = conn.cursor()
        cur.execute("SELECT stop_type FROM stop_coords WHERE atco_code = %s", (atco_code,))
        row = cur.fetchone()
        conn.close()
        return (row[0] or "other") if row else "other"

    def get_stop_types_bulk(self, atco_codes) -> dict:
        """Return ``{atco_code: stop_type}``."""
        if not atco_codes:
            return {}
        conn = self._connect()
        cur = conn.cursor()
        codes = list(atco_codes)
        ph = ",".join(["%s"] * len(codes))
        cur.execute(
            f"SELECT atco_code, stop_type FROM stop_coords WHERE atco_code IN ({ph})",
            codes,
        )
        result = {r[0]: (r[1] or "other") for r in cur.fetchall()}
        conn.close()
        return result

    # ── Walking transfers ─────────────────────────────────────────

    def get_walking_transfers(self) -> dict:
        """Return ``{from_atco: {to_atco: walk_seconds}}``."""
        conn = self._connect()
        cur = conn.cursor()
        cur.execute("SELECT from_atco, to_atco, walk_seconds FROM walking_transfers")
        result: dict = {}
        for from_a, to_a, secs in cur.fetchall():
            result.setdefault(from_a, {})[to_a] = secs
        conn.close()
        return result

    def clear_walking_transfers(self):
        """Remove all precomputed transfers and stop data."""
        conn = self._connect()
        cur = conn.cursor()
        cur.execute("DELETE FROM walking_transfers")
        cur.execute("DELETE FROM stop_coords")
        conn.commit()
        conn.close()

    # ── Precompute walking transfers (OSRM) ───────────────────────

    def precompute_walking_transfers(self, osrm_base="http://localhost:5012",
                                     max_walk_seconds=600, bbox_margin=0.012):
        """Precompute walking transfers between nearby stops using OSRM."""
        self.precomputing = True
        self.precompute_use_fallback = False

        conn = self._connect()
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

        for _idx_pos, src_idx in enumerate(indexed):
            src_atco = atco_list[src_idx]
            if src_atco in done_sources:
                continue
            src_lat, src_lon = lat_list[src_idx], lon_list[src_idx]
            lo = bisect.bisect_left(sorted_lats, src_lat - bbox_margin)
            hi = bisect.bisect_right(sorted_lats, src_lat + bbox_margin)
            neighbors = [indexed[j] for j in range(lo, hi)
                         if indexed[j] != src_idx and abs(lon_list[indexed[j]] - src_lon) <= bbox_margin]
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
                if k == 0 or dur is None or dur > max_walk_seconds:
                    continue
                transfers.append((src_atco, atco_list[all_indices[k]], int(dur)))
            processed += 1
            self.precompute_processed = processed
            if processed % 500 == 0:
                self._flush_transfers(transfers)
                total_found += len(transfers)
                self.precompute_inserted = total_found
                transfers = []
                print(f"    [{processed}/{total_targets}] {total_found} transfers saved")

        if transfers:
            self._flush_transfers(transfers)
            total_found += len(transfers)
            self.precompute_inserted = total_found
        print(f"  ✓ {total_found} walking transfers stored")
        self.precomputing = False

    def precompute_walking_transfers_fallback(self, max_walk_seconds=600,
                                              walk_speed_mps=1.4,
                                              bbox_margin=0.012):
        """Fallback precompute using haversine distances."""
        def haversine_m(lat1, lon1, lat2, lon2):
            R = 6371000.0
            p1, p2 = math.radians(lat1), math.radians(lat2)
            dp = math.radians(lat2 - lat1)
            dl = math.radians(lon2 - lon1)
            a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
            return 2 * R * math.asin(math.sqrt(a))

        self.precomputing = True
        self.precompute_use_fallback = True
        conn = self._connect()
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
        for _idx_pos, src_idx in enumerate(indexed):
            src_atco = atco_list[src_idx]
            if src_atco in done_sources:
                continue
            src_lat, src_lon = lat_list[src_idx], lon_list[src_idx]
            lo = bisect.bisect_left(sorted_lats, src_lat - bbox_margin)
            hi = bisect.bisect_right(sorted_lats, src_lat + bbox_margin)
            for j in range(lo, hi):
                nb_idx = indexed[j]
                if nb_idx == src_idx:
                    continue
                if abs(lon_list[nb_idx] - src_lon) > bbox_margin:
                    continue
                secs = int(haversine_m(src_lat, src_lon, lat_list[nb_idx], lon_list[nb_idx]) / walk_speed_mps)
                if 0 < secs <= max_walk_seconds:
                    transfers.append((src_atco, atco_list[nb_idx], secs))
            processed += 1
            self.precompute_processed = processed
            if processed % 500 == 0:
                self._flush_transfers(transfers)
                total_found += len(transfers)
                self.precompute_inserted = total_found
                transfers = []
                print(f"    [{processed}/{total_targets}] {total_found} transfers saved (approx)")
        if transfers:
            self._flush_transfers(transfers)
            total_found += len(transfers)
            self.precompute_inserted = total_found
        print(f"  ✓ {total_found} approx walking transfers stored")
        self.precomputing = False

    def _flush_transfers(self, transfers):
        """Batch-insert walking transfer rows."""
        if not transfers:
            return
        conn = self._connect()
        cur = conn.cursor()
        cur.executemany(
            "INSERT INTO walking_transfers (from_atco, to_atco, walk_seconds) "
            "VALUES (%s, %s, %s) "
            "ON CONFLICT (from_atco, to_atco) DO UPDATE SET walk_seconds = EXCLUDED.walk_seconds",
            transfers,
        )
        conn.commit()
        conn.close()

    def start_precompute_background(self, osrm_base=None, use_fallback=False, **kwargs):
        """Start walking-transfer precompute in a background thread."""
        if self.precomputing:
            return self.precompute_thread

        def _worker():
            try:
                if use_fallback:
                    self.precompute_walking_transfers_fallback(**kwargs)
                else:
                    self.precompute_walking_transfers(osrm_base=osrm_base, **kwargs)
            except Exception as e:
                print(f"  Precompute background error: {e}")
            finally:
                self.precomputing = False

        th = threading.Thread(target=_worker, name="walking-precompute", daemon=True)
        self.precompute_thread = th
        th.start()
        return th
