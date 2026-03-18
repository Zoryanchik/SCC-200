import bisect
import csv
import json
import os
import psycopg
import urllib.request
import math
from concurrent.futures import ThreadPoolExecutor, as_completed


WALKING_CACHE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "cache", "walking_transfers.csv"
)


class WalkingLoader:
    def __init__(self, db_path):
        self.db_path = db_path
        # Precompute progress state (used by /status endpoint)
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
        """Create walking DB table: walking_transfers.

        Note: the ``stop_coords`` table is owned by ``AtcoLoader`` —
        use ``AtcoLoader.create_schema()`` and
        ``AtcoLoader.download_stop_coords()`` for stop coordinate data.
        """
        conn = self._connect(self.db_path)
        cur = conn.cursor()
        cur.execute("""
            CREATE UNLOGGED TABLE IF NOT EXISTS walking_transfers (
                from_atco   TEXT NOT NULL,
                to_atco     TEXT NOT NULL,
                walk_seconds INTEGER NOT NULL,
                PRIMARY KEY (from_atco, to_atco)
            );
        """)
        conn.commit()
        conn.close()

    def _bulk_insert_transfers(self, records):
        """High performance bulk insert for walking transfers using COPY and temp table."""
        if not records:
            return
            
        # Deduplicate records in memory first (by from_atco, to_atco) keeping minimum walk_seconds
        deduped = {}
        for (src, dst, sec) in records:
            k = (src, dst)
            if k not in deduped or sec < deduped[k]:
                deduped[k] = sec
        unique_records = [(k[0], k[1], v) for k, v in deduped.items()]
        
        conn = self._connect(self.db_path)
        cur = conn.cursor()
        
        cur.execute("CREATE TEMP TABLE _tmp_walking_transfers (LIKE walking_transfers) ON COMMIT DROP")
        
        # Use COPY for fast writes without constraint checks
        with cur.copy("COPY _tmp_walking_transfers (from_atco, to_atco, walk_seconds) FROM STDIN") as copy:
            for r in unique_records:
                copy.write_row(r)
                
        # Merge temp table back to actual table
        cur.execute("""
            INSERT INTO walking_transfers (from_atco, to_atco, walk_seconds)
            SELECT from_atco, to_atco, walk_seconds FROM _tmp_walking_transfers
            ON CONFLICT (from_atco, to_atco) DO UPDATE SET walk_seconds = EXCLUDED.walk_seconds
        """)
        
        conn.commit()
        conn.close()

    def precompute_walking_transfers(self, coords, osrm_base="http://localhost:5012",
                                      max_walk_seconds=600,
                                      bbox_margin=0.012,
                                      max_workers=15):
        """Precompute walking transfers between nearby stops using OSRM.

        Parameters
        ----------
        coords : dict
            ``{atco_code: (lat, lon)}`` — obtained from
            ``AtcoLoader.get_all_stop_coords()``.
        max_workers : int
            Number of parallel threads for OSRM requests.

        For each stop, finds other stops within *bbox_margin* degrees,
        queries OSRM /table endpoint for walking durations, and stores pairs
        ≤ *max_walk_seconds* in the walking_transfers table.
        """
        # Mark progress state
        self.precomputing = True
        self.precompute_use_fallback = False

        # Hard cap concurrency to avoid saturating CPU / OSRM / network.
        max_workers = max(1, min(int(max_workers or 1), 15))

        conn = self._connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT from_atco FROM walking_transfers")
        done_sources = {r[0] for r in cur.fetchall()}
        conn.close()

        if not coords:
            print("  ✗ No stop coordinates — run AtcoLoader.download_stop_coords() first")
            return

        atco_list = list(coords.keys())
        lat_list = [coords[a][0] for a in atco_list]
        lon_list = [coords[a][1] for a in atco_list]

        indexed = sorted(range(len(atco_list)), key=lambda i: lat_list[i])
        sorted_lats = [lat_list[i] for i in indexed]

        # Build list of pending source indices
        pending = [i for i in indexed if atco_list[i] not in done_sources]
        total_pending = len(pending)
        self.precompute_total = total_pending
        self.precompute_processed = 0
        self.precompute_inserted = 0

        print(f"  Precomputing walking transfers for {len(atco_list)} stops"
              f" ({len(done_sources)} already done, {total_pending} pending) "
              f"using {max_workers} threads...")

        import requests
        session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_connections=max_workers, pool_maxsize=max_workers)
        session.mount('http://', adapter)
        
        def query_one_stop(src_idx):
            """Query OSRM for one source stop, return list of (src_atco, dst_atco, dur) or []."""
            src_atco = atco_list[src_idx]
            src_lat = lat_list[src_idx]
            src_lon = lon_list[src_idx]

            lo = bisect.bisect_left(sorted_lats, src_lat - bbox_margin)
            hi = bisect.bisect_right(sorted_lats, src_lat + bbox_margin)

            neighbors = []
            import math
            def fast_dist_m(lat1, lon1, lat2, lon2):
                r_lat1 = math.radians(lat1)
                x = math.radians(lon2 - lon1) * math.cos(r_lat1)
                y = math.radians(lat2 - lat1)
                return 6371000.0 * math.sqrt(x*x + y*y)
                
            max_straight_m = max_walk_seconds * 2.0  # conservative leeway

            for j in range(lo, hi):
                nb_idx = indexed[j]
                # Only process each pair once by enforcing a strict ordering
                if nb_idx <= src_idx:
                    continue
                if abs(lon_list[nb_idx] - src_lon) <= bbox_margin:
                    dist = fast_dist_m(src_lat, src_lon, lat_list[nb_idx], lon_list[nb_idx])
                    if dist <= max_straight_m:
                        neighbors.append(nb_idx)

            if not neighbors:
                return []

            all_indices = [src_idx] + neighbors
            coord_str = ";".join(f"{lon_list[i]},{lat_list[i]}" for i in all_indices)
            osrm_url = f"{osrm_base}/table/v1/foot/{coord_str}?sources=0&annotations=duration"

            try:
                resp = session.get(osrm_url, timeout=10)
                if resp.status_code != 200:
                    return []
                data = resp.json()
            except Exception:
                return []

            if data.get("code") != "Ok":
                return []

            results = []
            durations = data["durations"][0]
            for k, dur in enumerate(durations):
                if k == 0:
                    continue
                if dur is None or dur > max_walk_seconds:
                    continue
                nb_idx = all_indices[k]
                dst_atco = atco_list[nb_idx]
                d = int(dur)
                results.append((src_atco, dst_atco, d))
                results.append((dst_atco, src_atco, d))
            return results

        transfers = []
        processed = 0
        total_found = 0

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(query_one_stop, src_idx): src_idx for src_idx in pending}

            for future in as_completed(futures):
                result = future.result()
                transfers.extend(result)
                processed += 1
                self.precompute_processed = processed

                # Flush to DB periodically
                if processed % 500 == 0:
                    if transfers:
                        self._bulk_insert_transfers(transfers)
                        total_found += len(transfers)
                        self.precompute_inserted = total_found
                        transfers = []
                    print(f"    [{processed}/{total_pending}] {total_found} transfers saved")

        # Final flush
        if transfers:
            self._bulk_insert_transfers(transfers)
            total_found += len(transfers)
            self.precompute_inserted = total_found

        print(f"  ✓ {total_found} walking transfers stored")
        self.precomputing = False

    def precompute_walking_transfers_fallback(self, coords, max_walk_seconds=600,
                                              walk_speed_mps=1.4,
                                              bbox_margin=0.012):
        """Fallback precompute using straight-line (haversine) distances.

        Parameters
        ----------
        coords : dict
            ``{atco_code: (lat, lon)}`` — obtained from
            ``AtcoLoader.get_all_stop_coords()``.

        This computes approximate walk seconds = distance_m / walk_speed_mps
        for nearby stops (selected via the same latitude bbox heuristic used
        for OSRM), and inserts pairs whose computed time ≤ max_walk_seconds.
        """
        def fast_dist_m(lat1, lon1, lat2, lon2):
            # Equirectangular approximation for very short distances (<10km)
            # ~10x faster than full spherical Haversine in pure Python
            r_lat1 = math.radians(lat1)
            x = math.radians(lon2 - lon1) * math.cos(r_lat1)
            y = math.radians(lat2 - lat1)
            return 6371000.0 * math.sqrt(x*x + y*y)

        # Mark progress state
        self.precomputing = True
        self.precompute_use_fallback = True

        conn = self._connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT from_atco FROM walking_transfers")
        done_sources = {r[0] for r in cur.fetchall()}
        conn.close()

        if not coords:
            print("  ✗ No stop coordinates — run AtcoLoader.download_stop_coords() first")
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
                if nb_idx <= src_idx:
                    continue
                if abs(lon_list[nb_idx] - src_lon) <= bbox_margin:
                    neighbors.append(nb_idx)

            if not neighbors:
                continue

            for nb_idx in neighbors:
                dst_atco = atco_list[nb_idx]
                lat2 = lat_list[nb_idx]
                lon2 = lon_list[nb_idx]
                dist_m = fast_dist_m(src_lat, src_lon, lat2, lon2)
                secs = int(dist_m / walk_speed_mps)
                if secs <= 0 or secs > max_walk_seconds:
                    continue
                transfers.append((src_atco, dst_atco, secs))
                transfers.append((dst_atco, src_atco, secs))

            processed += 1
            # update progress
            self.precompute_processed = processed
            if processed % 1000 == 0:
                if transfers:
                    self._bulk_insert_transfers(transfers)
                    total_found += len(transfers)
                    self.precompute_inserted = total_found
                    transfers = []
                print(f"    [{processed}/{len(atco_list) - len(done_sources)}]"
                      f" {total_found} transfers saved (approx)")

        if transfers:
            self._bulk_insert_transfers(transfers)
            total_found += len(transfers)
            self.precompute_inserted = total_found
        print(f"  ✓ {total_found} approx walking transfers stored")
        # clear running flag
        self.precomputing = False

    # ── Disk cache for walking transfers ──────────────────────────

    def save_cache(self, cache_path=None):
        """Dump the walking_transfers table to a CSV file for fast reload."""
        path = cache_path or WALKING_CACHE_FILE
        conn = self._connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT from_atco, to_atco, walk_seconds FROM walking_transfers")
        rows = cur.fetchall()
        conn.close()

        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerows(rows)
        print(f"  [walking] Cache saved → {path}  ({len(rows)} rows)")

    def load_from_cache(self, cache_path=None):
        """Bulk-load walking transfers from a cached CSV file into the DB.

        Returns True if the cache was found and loaded, False otherwise.
        """
        path = cache_path or WALKING_CACHE_FILE
        if not os.path.exists(path):
            return False

        # Read CSV rows
        rows = []
        with open(path, "r", newline="") as f:
            reader = csv.reader(f)
            for r in reader:
                if len(r) >= 3:
                    rows.append((r[0], r[1], int(r[2])))

        if not rows:
            return False

        print(f"  [walking] Loading {len(rows)} cached transfers from {path} ...")
        conn = self._connect(self.db_path)
        cur = conn.cursor()

        # Use COPY for fastest bulk load
        with cur.copy("COPY walking_transfers (from_atco, to_atco, walk_seconds) FROM STDIN") as copy:
            for r in rows:
                copy.write_row(r)

        conn.commit()
        conn.close()
        print(f"  [walking] ✓ Loaded {len(rows)} transfers from cache")
        return True

    def clear_walking_transfers(self):
        """Remove all precomputed walking transfers (e.g. after data update)."""
        conn = self._connect(self.db_path)
        cur = conn.cursor()
        cur.execute("DELETE FROM walking_transfers")
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
