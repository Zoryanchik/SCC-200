"""Backfill journey->section mappings (journey_id -> JPS route_id).

This best-effort script finds JourneyPatternSection route ids (those
present in `bus_route_stops`) and inserts rows into
`bus_journey_routes` mapping every journey that visits any of the
section's stops. It's permissive: it may add extra mappings but
ensures the in-memory merged network can link stops -> journeys.

Run as: python3 scripts/backfill_journey_sections.py
"""
import psycopg
from main import BUS_DB_PATH

BATCH = 500

conn = psycopg.connect(BUS_DB_PATH)
cur = conn.cursor()

print('Loading section route stop lists (JPS)')
cur.execute("SELECT route_id, array_agg(atco_code ORDER BY stop_order) FROM bus_route_stops GROUP BY route_id")
routes = cur.fetchall()
# Keep only those that look like section ids (heuristic: contain 'JPS' or are short)
jps_routes = [(r[0], r[1]) for r in routes if 'JPS' in (r[0] or '')]
print('Found', len(jps_routes), 'JPS routes')

# Iterate journeys in batches
cur.execute("SELECT DISTINCT journey_id FROM bus_journey_times")
all_jids = [r[0] for r in cur.fetchall()]
print('Found', len(all_jids), 'journeys to scan')

inserted = 0
for start in range(0, len(all_jids), BATCH):
    batch = all_jids[start:start+BATCH]
    # fetch stops for batch journeys
    placeholders = ','.join(['%s']*len(batch))
    cur.execute(f"SELECT journey_id, array_agg(atco_code ORDER BY arrival_time) FROM bus_journey_times WHERE journey_id IN ({placeholders}) GROUP BY journey_id", batch)
    jid_rows = cur.fetchall()
    jid_map = {jid: stops for jid, stops in jid_rows}

    inserts = []
    for jid, j_stops in jid_map.items():
        sset = set(j_stops)
        for rid, r_stops in jps_routes:
            # quick membership test
            if not any(s in sset for s in r_stops):
                continue
            # map this journey to this section route id
            inserts.append((jid, rid, '', ''))
    if not inserts:
        continue
    # Bulk insert using ON CONFLICT DO NOTHING
    temp = "_tmp_backfill"
    cur.execute(f"DROP TABLE IF EXISTS {temp}")
    cur.execute(f"CREATE TEMP TABLE {temp} (LIKE bus_journey_routes) ON COMMIT DROP")
    with cur.copy(f"COPY {temp} (journey_id, route_id, line_name, destination_display) FROM STDIN") as copy:
        for row in inserts:
            copy.write_row(row)
    cur.execute(f"INSERT INTO bus_journey_routes (journey_id, route_id, line_name, destination_display) SELECT journey_id, route_id, line_name, destination_display FROM {temp} ON CONFLICT (journey_id, route_id) DO NOTHING")
    inserted += len(inserts)
    conn.commit()
    print(f'Processed {start+BATCH}/{len(all_jids)} journeys — inserted {inserted} rows so far')

print('Done — total inserted approx', inserted)
conn.close()
