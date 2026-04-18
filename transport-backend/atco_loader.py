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
import urllib.error
import urllib.request


NORTHWEST_ATCO_PREFIXES = ("250", "259", "258", "090", "180", "280", "060", "061", "062", "065", "320", "329")


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
            CREATE UNLOGGED TABLE IF NOT EXISTS stop_coords (
                atco_code  TEXT PRIMARY KEY,
                lat        REAL NOT NULL,
                lon        REAL NOT NULL,
                name       TEXT,
                stop_type  TEXT,
                town       TEXT
            );
        """)
        # Migrate older schemas that lack the new columns
        for col, coltype in [("name", "TEXT"), ("stop_type", "TEXT"), ("town", "TEXT")]:
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
        xml_path = os.path.join(repo_cache, "naptan-full.xml")

        if not os.path.exists(xml_path) or os.path.getsize(xml_path) < 1_000_000:
            print("  Downloading NaPTAN full XML (≈570 MB)...")
            url = "https://transport.scc.lancs.ac.uk/nptg/naptan-full.xml"
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            try:
                with urllib.request.urlopen(url, context=ctx, timeout=300) as resp, \
                     open(xml_path, "wb") as out:
                    shutil.copyfileobj(resp, out)
            except urllib.error.HTTPError as e:
                if getattr(e, "code", None) == 404:
                    print(f"  ⚠ NaPTAN URL returned 404 — skipping download: {url}")
                    return
                raise RuntimeError(f"NaPTAN download failed: {e}")
            except Exception as e:
                raise RuntimeError(f"NaPTAN download failed: {e}")
        else:
            print("  Using cached NaPTAN full XML...")

        print(f"  Parsing {os.path.getsize(xml_path) // 1024 // 1024} MB...")

        rows = []
        
        # Filter regions to strictly Northwest UK + Cumbria + Yorkshire to cover operators like ARCT, BLAC, KLCO, SCCU, SCMY, NUTT
        allow_list = NORTHWEST_ATCO_PREFIXES
        
        try:
            import xml.etree.ElementTree as ET
            ns = '{http://www.naptan.org.uk/}'
            context = ET.iterparse(xml_path, events=('end',))
            for event, elem in context:
                if elem.tag == f"{ns}StopPoint":
                    atco_e = elem.find(f"{ns}AtcoCode")
                    if atco_e is not None and atco_e.text:
                        atco = atco_e.text.strip()
                        if atco.startswith(allow_list):
                            lat_e = elem.find(f".//{ns}Latitude")
                            lon_e = elem.find(f".//{ns}Longitude")
                            if lat_e is not None and lon_e is not None and lat_e.text and lon_e.text:
                                try:
                                    lat = float(lat_e.text.strip())
                                    lon = float(lon_e.text.strip())
                                    
                                    cn_e = elem.find(f".//{ns}CommonName")
                                    name = cn_e.text.strip() if cn_e is not None and cn_e.text else None
                                    
                                    town_e = elem.find(f".//{ns}Town")
                                    if town_e is None:
                                        town_e = elem.find(f".//{ns}TownName")
                                    if town_e is None:
                                        town_e = elem.find(f".//{ns}LocalityName")
                                    town = town_e.text.strip() if town_e is not None and town_e.text else None
                                    
                                    st_e = elem.find(f".//{ns}StopType")
                                    raw_type = st_e.text.strip().upper() if st_e is not None and st_e.text else ""
                                    if raw_type.startswith("B"):
                                        st_type = "bus"
                                    elif raw_type.startswith("T"):
                                        st_type = "train"
                                    else:
                                        st_type = "other"
                                        
                                    rows.append((atco, lat, lon, name, st_type, town))
                                except ValueError:
                                    pass
                    # Extremely important for memory use when parsing 570MB files
                    elem.clear()
        except Exception as e:
            raise RuntimeError(f"Error parsing NaPTAN full XML: {e}")

        if not rows:
            print("  ✗ No coordinates found in NaPTAN XML for allowed regions")
            return

        conn = self._connect()
        cur = conn.cursor()
        
        # Fast bulk insert using COPY and temp table
        cur.execute("CREATE TEMP TABLE _tmp_stop_coords (LIKE stop_coords) ON COMMIT DROP")
        
        with cur.copy("COPY _tmp_stop_coords (atco_code, lat, lon, name, stop_type, town) FROM STDIN") as copy:
            for r in rows:
                copy.write_row(r)
                
        cur.execute("""
            INSERT INTO stop_coords (atco_code, lat, lon, name, stop_type, town)
            SELECT atco_code, lat, lon, name, stop_type, town FROM _tmp_stop_coords
            ON CONFLICT (atco_code) DO UPDATE SET 
            lat = EXCLUDED.lat, lon = EXCLUDED.lon, 
            name = EXCLUDED.name, stop_type = EXCLUDED.stop_type, town = EXCLUDED.town
        """)
        
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

    def get_stop_towns_bulk(self, atco_codes) -> dict:
        """Return ``{atco_code: town}`` for the given ATCO codes.

        This mirrors ``get_stop_names_bulk`` but returns the stored
        ``town`` value from the ``stop_coords`` table.  Missing or
        empty towns are omitted from the result.
        """
        if not atco_codes:
            return {}
        conn = self._connect()
        cur = conn.cursor()
        codes = list(atco_codes)
        ph = ",".join(["%s"] * len(codes))
        cur.execute(
            f"SELECT atco_code, town FROM stop_coords WHERE atco_code IN ({ph})",
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
