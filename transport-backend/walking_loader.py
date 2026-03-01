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
            CREATE TABLE IF NOT EXISTS walking_transfers (
                from_atco   TEXT NOT NULL,
                to_atco     TEXT NOT NULL,
                walk_seconds INTEGER NOT NULL,
                PRIMARY KEY (from_atco, to_atco)
            );
        """)
        conn.commit()
        conn.close()

    def precompute_walking_transfers(self, coords, osrm_base="http://localhost:5012",
                                      max_walk_seconds=600,
                                      bbox_margin=0.012,
                                      max_workers=50):
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

        def query_one_stop(src_idx):
            """Query OSRM for one source stop, return list of (src_atco, dst_atco, dur) or []."""
            src_atco = atco_list[src_idx]
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
                return []

            all_indices = [src_idx] + neighbors
            coord_str = ";".join(f"{lon_list[i]},{lat_list[i]}" for i in all_indices)
            osrm_url = f"{osrm_base}/table/v1/foot/{coord_str}?sources=0&annotations=duration"

            try:
                resp = urllib.request.urlopen(osrm_url, timeout=10)
                data = json.loads(resp.read())
                resp.close()
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
                results.append((src_atco, dst_atco, int(dur)))
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
                    print(f"    [{processed}/{total_pending}] {total_found} transfers saved")

        # Final flush
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
