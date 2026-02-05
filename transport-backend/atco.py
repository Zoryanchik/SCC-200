"""
ATCO (Association of Transport Coordinating Officers) NaPTAN Stop Data Loader

Loads UK National Public Transport Access Nodes (NaPTAN) data from XML,
providing standardized stop information including names, types, and coordinates.

Data source: https://transport.scc.lancs.ac.uk/nptg/naptan.xml
"""

import psycopg2
import psycopg2.extras
import xml.etree.ElementTree as ET
import os
import hashlib
from pathlib import Path


class Atco:
    """Load and manage ATCO NaPTAN stop data"""
    
    def __init__(self, db_name='atco', user='', password='', host='localhost'):
        """Initialize ATCO database connection"""
        self.db_name = db_name
        self.user = user
        self.password = password
        self.host = host
        self.naptan_url = "https://transport.scc.lancs.ac.uk/nptg/naptan.xml"
        self.naptan_file = "naptan.xml"
        self.hash_file = ".naptan_hash"
        
    def _get_connection(self):
        """Get database connection"""
        conn = psycopg2.connect(
            host=self.host,
            user=self.user,
            password=self.password,
            database=self.db_name
        )
        return conn
    
    def initialize(self):
        """Create database and schema if needed"""
        # Connect to default postgres database
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
        except psycopg2.errors.DuplicateDatabase:
            print(f"✓ Database '{self.db_name}' already exists")
        finally:
            cur.close()
            conn.close()
        
        # Create schema
        conn = self._get_connection()
        cur = conn.cursor()
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS atco_stops (
                atco_code VARCHAR(12) PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                stop_type VARCHAR(10),
                latitude DECIMAL(10, 8),
                longitude DECIMAL(11, 8),
                locality VARCHAR(255),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        conn.commit()
        cur.close()
        conn.close()
        print("✓ Schema initialized")
    
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
        """Load NaPTAN XML into database"""
        self.initialize()
        
        # Check if we need to download
        if self._should_reload():
            print("NaPTAN data outdated or missing, downloading...")
            if not self.download_naptan():
                return False
        else:
            print("✓ Using cached NaPTAN data")
        
        # Parse and load XML
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
        
        # Extract stops
        conn = self._get_connection()
        cur = conn.cursor()
        
        stops = []
        for stop_elem in root.findall('.//{http://www.naptan.org.uk/}StopPoint'):
            try:
                atco_code = stop_elem.findtext('{http://www.naptan.org.uk/}AtcoCode')
                name = stop_elem.findtext('{http://www.naptan.org.uk/}CommonName')
                stop_type = stop_elem.findtext('{http://www.naptan.org.uk/}StopClassification/{http://www.naptan.org.uk/}StopType')
                
                # Get coordinates
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
            except Exception as e:
                continue
        
        print(f"Loaded {len(stops)} stops from XML")
        
        # Insert into database
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
            except Exception as e:
                continue
        
        conn.commit()
        cur.close()
        conn.close()
        
        print(f"✓ Inserted {inserted} stops into database")
        return True
    
    def get_stop(self, atco_code):
        """Get stop details by ATCO code"""
        conn = self._get_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        cur.execute("""
            SELECT * FROM atco_stops
            WHERE atco_code = %s
        """, (atco_code,))
        
        result = cur.fetchone()
        cur.close()
        conn.close()
        
        return result

    def get_stop_name(self, atco_code):
        """Return only the stop name for an ATCO code, or None if not found."""
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute("SELECT name FROM atco_stops WHERE atco_code = %s", (atco_code,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row:
            return row[0]
        return None
    
    def search_stops(self, query, limit=10):
        """Search for stops by name"""
        conn = self._get_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        cur.execute("""
            SELECT * FROM atco_stops
            WHERE LOWER(name) LIKE LOWER(%s)
            ORDER BY name
            LIMIT %s
        """, (f"%{query}%", limit))
        
        results = cur.fetchall()
        cur.close()
        conn.close()
        
        return results
    
    def get_all_stops(self, limit=None):
        """Get all stops"""
        conn = self._get_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        if limit:
            cur.execute("SELECT * FROM atco_stops LIMIT %s", (limit,))
        else:
            cur.execute("SELECT * FROM atco_stops")
        
        results = cur.fetchall()
        cur.close()
        conn.close()
        
        return results
    
    def get_stop_count(self):
        """Get total number of stops"""
        conn = self._get_connection()
        cur = conn.cursor()
        
        cur.execute("SELECT COUNT(*) FROM atco_stops")
        count = cur.fetchone()[0]
        
        cur.close()
        conn.close()
        
        return count