"""ATCO stop data loader.

Owns the ``stop_coords`` table (with name and stop_type columns).
Parses NaPTAN XML for coordinates, common names
(``<Descriptor><CommonName>``) and stop types
(``<StopClassification><StopType>`` — codes starting with ``B`` → bus,
``T`` → train, else ``other``).

The ``walking_transfers`` table is owned by ``WalkingLoader`` — see
``walking_loader.py`` for precompute, cache, and lookup functions.

Provides fast lookup helpers used by MergedData and Walking at runtime.
"""

import os
import re
import shutil
import ssl

import psycopg
import urllib.request


class AtcoLoader:
    """Manage ATCO stop metadata: coords, names, types."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    # ── DB helpers ────────────────────────────────────────────────

    def _connect(self, path=None):
        return psycopg.connect(path or self.db_path)

    # ── Schema ────────────────────────────────────────────────────

    def create_schema(self):
        """Create the ``stop_coords`` table.

        The ``walking_transfers`` table is managed by ``WalkingLoader``.
        """
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
