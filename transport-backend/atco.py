"""
ATCO (Association of Transport Coordinating Officers) NaPTAN Stop Data Loader

Loads UK National Public Transport Access Nodes (NaPTAN) data from XML,
providing standardized stop information including names, types, and coordinates.

Data source: https://transport.scc.lancs.ac.uk/nptg/naptan.xml
"""

try:
    import psycopg2
    import psycopg2.extras
    PG_AVAILABLE = True
except Exception:
    PG_AVAILABLE = False

import xml.etree.ElementTree as ET
import os
import hashlib
from pathlib import Path
import sqlite3
from typing import Optional, List, Dict


class Atco:
    """Load and manage ATCO NaPTAN stop data.

    This class supports either a PostgreSQL backend (when `psycopg2` is
    available) or an on-disk SQLite fallback (useful for demo runs where
    system packages / sudo are not available).
    """

    def __init__(self, db_name: str = 'atco', user: str = 'lty', password: str = '', host: str = 'localhost'):
        self.db_name = db_name
        self.user = user
        self.password = password
        self.host = host
        self.naptan_url = "https://transport.scc.lancs.ac.uk/nptg/naptan.xml"
        self.naptan_file = "naptan.xml"
        self.hash_file = ".naptan_hash"
        # SQLite fallback file
        self.sqlite_file = f"{self.db_name}.sqlite"
        
    def _get_connection(self):
        """Get a DB connection. Uses PostgreSQL if available, otherwise SQLite."""
        if PG_AVAILABLE:
            conn = psycopg2.connect(
                host=self.host,
                user=self.user,
                password=self.password,
                database=self.db_name
            )
            return conn
        else:
            conn = sqlite3.connect(self.sqlite_file)
            conn.row_factory = sqlite3.Row
            return conn
    
    def initialize(self):
        """Create database and schema if needed"""
        if PG_AVAILABLE:
            # Attempt to create database if it doesn't exist (best-effort)
            conn = None
            try:
                conn = psycopg2.connect(
                    host=self.host,
                    user=self.user,
                    password=self.password,
                    database='postgres'
                )
                conn.autocommit = True
                cur = conn.cursor()
                try:
                    cur.execute(f"CREATE DATABASE {self.db_name}")
                    print(f"✓ Created database '{self.db_name}'")
                except Exception:
                    print(f"✓ Database '{self.db_name}' already exists or could not be created")
                finally:
                    cur.close()
            except Exception:
                # Could not connect to postgres to create database (ignore)
                pass
            finally:
                if conn:
                    conn.close()

            # Create schema in target DB
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS atco_stops (
                    atco_code TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    stop_type TEXT,
                    latitude REAL,
                    longitude REAL,
                    locality TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
            cur.close()
            conn.close()
            print("✓ Schema initialized (Postgres)")
        else:
            # SQLite fallback: create file and table
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS atco_stops (
                    atco_code TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    stop_type TEXT,
                    latitude REAL,
                    longitude REAL,
                    locality TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
            # Insert sample data if empty
            cur.execute("SELECT COUNT(*) as c FROM atco_stops")
            count = cur.fetchone()[0]
            if count == 0:
                sample = [
                    ("ATCO1", "Central Bus Stop", "bus", 53.8008, -1.5491, "Lancaster"),
                    ("ATCO2", "North Station", "train", 53.7950, -1.5500, "Lancaster"),
                    ("ATCO3", "University Gate", "bus", 53.8045, -1.5536, "Lancaster")
                ]
                cur.executemany("INSERT OR REPLACE INTO atco_stops (atco_code,name,stop_type,latitude,longitude,locality) VALUES (?,?,?,?,?,?)", sample)
                conn.commit()
            cur.close()
            conn.close()
            print("✓ Schema initialized (SQLite fallback) — sample data ready")
    
    def _get_file_hash(self, filepath):
        """Calculate SHA256 hash of file"""
        sha256_hash = hashlib.sha256()
        with open(filepath, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    
    def _should_reload(self):
        """Check if NaPTAN file has changed"""
        if not os.path.exists(self.naptan_file):
            return True
        
        if not os.path.exists(self.hash_file):
            return True
        
        current_hash = self._get_file_hash(self.naptan_file)
        with open(self.hash_file, 'r') as f:
            stored_hash = f.read().strip()
        
        return current_hash != stored_hash
    
    def _save_hash(self):
        """Save current file hash"""
        if os.path.exists(self.naptan_file):
            current_hash = self._get_file_hash(self.naptan_file)
            with open(self.hash_file, 'w') as f:
                f.write(current_hash)
    
    def download_naptan(self):
        """Download NaPTAN XML file"""
        import urllib.request
        
        print(f"Downloading NaPTAN data from {self.naptan_url}...")
        try:
            urllib.request.urlretrieve(self.naptan_url, self.naptan_file)
            self._save_hash()
            print(f"✓ Downloaded {self.naptan_file}")
            return True
        except Exception as e:
            print(f"✗ Download failed: {e}")
            return False
    
    def load_naptan(self):
        # For demo runs we don't automatically parse and import the full
        # NaPTAN XML (it is large). Keep the function available for
        # environments that can use it; otherwise the SQLite fallback will
        # use sample data created in `initialize()` above.
        self.initialize()
        if PG_AVAILABLE:
            # Attempt to download and load (best-effort)
            if self._should_reload():
                print("NaPTAN data outdated or missing, downloading...")
                if not self.download_naptan():
                    return False
            else:
                print("✓ Using cached NaPTAN data")

            if not os.path.exists(self.naptan_file):
                print(f"✗ File not found: {self.naptan_file}")
                return False

            print(f"Parsing {self.naptan_file}...")
            try:
                tree = ET.parse(self.naptan_file)
                root = tree.getroot()
            except Exception as e:
                print(f"✗ XML parse error: {e}")
                return False

            conn = self._get_connection()
            cur = conn.cursor()

            stops = []
            for stop_elem in root.findall('.//{http://www.naptan.org.uk/}StopPoint'):
                try:
                    atco_code = stop_elem.findtext('{http://www.naptan.org.uk/}AtcoCode')
                    name = stop_elem.findtext('{http://www.naptan.org.uk/}CommonName')
                    stop_type = stop_elem.findtext('{http://www.naptan.org.uk/}StopClassification/{http://www.naptan.org.uk/}StopType')
                    location = stop_elem.find('{http://www.naptan.org.uk/}Location')
                    latitude = None
                    longitude = None
                    if location is not None:
                        lat_str = location.findtext('{http://www.naptan.org.uk/}Latitude')
                        lon_str = location.findtext('{http://www.naptan.org.uk/}Longitude')
                        if lat_str and lon_str:
                            latitude = float(lat_str)
                            longitude = float(lon_str)
                    locality = stop_elem.findtext('{http://www.naptan.org.uk/}LocalityRef')
                    if atco_code:
                        stops.append((atco_code, name, stop_type, latitude, longitude, locality))
                except Exception:
                    continue

            print(f"Loaded {len(stops)} stops from XML")
            inserted = 0
            for stop in stops:
                try:
                    cur.execute("""
                        INSERT INTO atco_stops (atco_code, name, stop_type, latitude, longitude, locality)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (atco_code) DO UPDATE SET
                            name = EXCLUDED.name,
                            stop_type = EXCLUDED.stop_type,
                            latitude = EXCLUDED.latitude,
                            longitude = EXCLUDED.longitude,
                            locality = EXCLUDED.locality
                    """, stop)
                    inserted += 1
                except Exception:
                    continue

            conn.commit()
            cur.close()
            conn.close()
            print(f"✓ Inserted {inserted} stops into database")
            return True
        else:
            print("SQLite fallback in use — sample data already available")
            return True
    
    def get_stop(self, atco_code):
        """Get stop details by ATCO code"""
        conn = self._get_connection()
        cur = conn.cursor()
        if PG_AVAILABLE:
            cur.execute("""
                SELECT * FROM atco_stops WHERE atco_code = %s
            """, (atco_code,))
            result = cur.fetchone()
            # convert RealDictRow to dict if needed
            try:
                return dict(result) if result is not None else None
            finally:
                cur.close()
                conn.close()
        else:
            cur.execute("SELECT * FROM atco_stops WHERE atco_code = ?", (atco_code,))
            row = cur.fetchone()
            cur.close()
            conn.close()
            return dict(row) if row else None
    
    def search_stops(self, query, limit=10):
        """Search for stops by name"""
        conn = self._get_connection()
        cur = conn.cursor()
        if PG_AVAILABLE:
            cur.execute("""
                SELECT * FROM atco_stops
                WHERE LOWER(name) LIKE LOWER(%s)
                ORDER BY name
                LIMIT %s
            """, (f"%{query}%", limit))
            rows = cur.fetchall()
            cur.close()
            conn.close()
            return [dict(r) for r in rows]
        else:
            cur.execute("SELECT * FROM atco_stops WHERE LOWER(name) LIKE LOWER(?) ORDER BY name LIMIT ?", (f"%{query}%", limit))
            rows = cur.fetchall()
            cur.close()
            conn.close()
            return [dict(r) for r in rows]
    
    def get_all_stops(self, limit=None):
        """Get all stops"""
        conn = self._get_connection()
        cur = conn.cursor()
        if limit:
            if PG_AVAILABLE:
                cur.execute("SELECT * FROM atco_stops LIMIT %s", (limit,))
            else:
                cur.execute("SELECT * FROM atco_stops LIMIT ?", (limit,))
        else:
            cur.execute("SELECT * FROM atco_stops")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [dict(r) for r in rows]
    
    def get_stop_count(self):
        """Get total number of stops"""
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM atco_stops")
        c = cur.fetchone()[0]
        cur.close()
        conn.close()
        return c

    # Backwards-compatible helper methods expected by `main.py`
    def get_stops_count(self):
        return self.get_stop_count()

    def get_bus_stops(self):
        """Return list of bus stops"""
        conn = self._get_connection()
        cur = conn.cursor()
        if PG_AVAILABLE:
            cur.execute("SELECT * FROM atco_stops WHERE LOWER(stop_type) LIKE 'bus%'")
            rows = cur.fetchall()
        else:
            cur.execute("SELECT * FROM atco_stops WHERE LOWER(stop_type) LIKE 'bus%'")
            rows = cur.fetchall()
        cur.close()
        conn.close()
        return [dict(r) for r in rows]

    def get_train_stops(self):
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM atco_stops WHERE LOWER(stop_type) LIKE 'train%'")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [dict(r) for r in rows]

    def get_gazetteer_id(self, atco_code: str) -> Optional[str]:
        """Return a gazetteer/locality id for a stop (best-effort)."""
        stop = self.get_stop(atco_code)
        if not stop:
            return None
        return stop.get('locality')


if __name__ == "__main__":
    atco = Atco()
    atco.load_naptan()
    
    count = atco.get_stop_count()
    print(f"\n✓ Total stops in database: {count}")
    
    # Show some examples
    print("\nExample stops:")
    stops = atco.get_all_stops(limit=5)
    for stop in stops:
        print(f"  • {stop['name']} ({stop['atco_code']})")
