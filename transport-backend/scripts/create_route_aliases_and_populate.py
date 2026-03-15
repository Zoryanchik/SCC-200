#!/usr/bin/env python3
"""Create `route_id_aliases` table and populate JPS->RS numeric mappings.

This script is safe to run multiple times (uses ON CONFLICT DO NOTHING).
It implements the "numeric suffix" rule: for any legacy route_id ending
with `:JPS<N>` that has a corresponding `:RS<N>` entry in
`bus_route_section_tracks`, insert an alias mapping. This follows the
strict mapping rule you specified (map JourneyPatternSection -> Route
via RouteSection numeric suffix).

Usage: PYTHONPATH=. python3 scripts/create_route_aliases_and_populate.py
"""
import psycopg
from main import BUS_DB_PATH

SQL_CREATE = '''
CREATE TABLE IF NOT EXISTS route_id_aliases (
    legacy_id TEXT PRIMARY KEY,
    canonical_id TEXT NOT NULL,
    confidence TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_route_id_aliases_canon ON route_id_aliases(canonical_id);
'''

SQL_INSERT_NUMERIC = '''
INSERT INTO route_id_aliases(legacy_id, canonical_id, confidence)
SELECT bs.route_id, regexp_replace(bs.route_id, ':JPS(\\d+)$', ':RS\\1'), 'jps_numeric'
FROM bus_route_stops bs
WHERE bs.route_id ~ ':JPS\\d+$'
  AND EXISTS (
    SELECT 1 FROM bus_route_section_tracks st
    WHERE st.route_id = regexp_replace(bs.route_id, ':JPS(\\d+)$', ':RS\\1')
  )
ON CONFLICT (legacy_id) DO NOTHING;
'''

def main():
    print('Connecting to', BUS_DB_PATH)
    conn = psycopg.connect(BUS_DB_PATH)
    cur = conn.cursor()
    print('Creating alias table if missing...')
    cur.execute(SQL_CREATE)
    conn.commit()
    print('Populating numeric JPS->RS aliases (this may take a moment)...')
    cur.execute(SQL_INSERT_NUMERIC)
    inserted = cur.rowcount
    conn.commit()
    print('Done. Inserted rows (approx):', inserted)
    # Show a few samples
    cur.execute("SELECT legacy_id, canonical_id, confidence FROM route_id_aliases ORDER BY created_at DESC LIMIT 20")
    for r in cur.fetchall():
        print('  ', r[0], '->', r[1], r[2])
    conn.close()

if __name__ == '__main__':
    main()
