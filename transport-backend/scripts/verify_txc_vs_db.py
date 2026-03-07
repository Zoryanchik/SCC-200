#!/usr/bin/env python3
"""Verify TXC per-file journey stop lists against DB-loaded journey stop lists.

Writes CSV `tmp_sccu/verification_results.csv` with mismatches and prints a summary.

Run from the repository root: python3 transport-backend/scripts/verify_txc_vs_db.py
"""
import csv
import sys
from pathlib import Path
try:
    from lxml import etree as ET
except Exception:
    import xml.etree.ElementTree as ET
import psycopg

NS = '{http://www.transxchange.org.uk/}'

DB_DSN = None
try:
    # import the project's BUS_DB_PATH if available
    sys.path.append(str(Path(__file__).resolve().parents[1]))
    from main import BUS_DB_PATH
    DB_DSN = BUS_DB_PATH
except Exception:
    DB_DSN = None

def txc_order_for_file_and_vj(fpath, vj_code):
    tree = ET.parse(fpath)
    root = tree.getroot()
    for v in root.findall('.//{}VehicleJourney'.format(NS)):
        if v.findtext('{}VehicleJourneyCode'.format(NS)) == vj_code:
            jp_ref = v.findtext('{}JourneyPatternRef'.format(NS))
            if not jp_ref:
                return None
            # find JP definition
            jp = None
            for jp_el in root.findall('.//{}JourneyPattern'.format(NS)):
                if jp_el.attrib.get('id') == jp_ref:
                    jp = jp_el; break
            if jp is None:
                return None
            sec_refs = [s.text for s in jp.findall('{}JourneyPatternSectionRefs'.format(NS))]
            # build jps_data
            jps_data = {}
            for jps in root.findall('.//{}JourneyPatternSection'.format(NS)):
                sid = jps.attrib.get('id')
                stops = []
                for jptl in jps.findall('{}JourneyPatternTimingLink'.format(NS)):
                    from_stop = jptl.findtext('{}From/{}StopPointRef'.format(NS,NS))
                    to_stop = jptl.findtext('{}To/{}StopPointRef'.format(NS,NS))
                    if not stops:
                        stops.append(from_stop)
                    stops.append(to_stop)
                jps_data[sid] = stops
            txc_stops = []
            for sid in sec_refs:
                txc_stops.extend(jps_data.get(sid, []))
            # dedupe first-seen
            seen = set(); ordered = []
            for s in txc_stops:
                if s not in seen:
                    seen.add(s); ordered.append(s)
            return ordered
    return None

def gather_txc_vjs(tmp_dir):
    files = sorted(Path(tmp_dir).glob('*.xml'))
    vj_map = {}  # vj_code -> (file_path, ordered_stops)
    for f in files:
        try:
            tree = ET.parse(f)
        except Exception:
            continue
        root = tree.getroot()
        for v in root.findall('.//{}VehicleJourney'.format(NS)):
            vj_code = v.findtext('{}VehicleJourneyCode'.format(NS))
            if not vj_code:
                continue
            if vj_code in vj_map:
                # keep first-seen file for simplicity
                continue
            ordered = txc_order_for_file_and_vj(f, vj_code)
            if ordered:
                vj_map[vj_code] = (f, ordered)
    return vj_map

def db_stops_for_vj(conn, vj_code):
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT journey_id FROM bus_journey_times WHERE journey_id LIKE %s", (f"%{vj_code}%",))
    jids = [r[0] for r in cur.fetchall()]
    results = {}
    for jid in jids:
        cur.execute("SELECT atco_code FROM bus_journey_times WHERE journey_id=%s ORDER BY arrival_time", (jid,))
        results[jid] = [r[0] for r in cur.fetchall()]
    return results

def main():
    tmp = Path('tmp_sccu')
    if not tmp.exists():
        print('tmp_sccu not found; run this from transport-backend and ensure tmp_sccu exists')
        return

    vj_map = gather_txc_vjs(tmp)
    if not vj_map:
        print('No VehicleJourneys found in tmp_sccu')
        return

    dsn = DB_DSN or ''
    conn = psycopg.connect(dsn)

    out_csv = tmp / 'verification_results.csv'
    with open(out_csv, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['vj_code','txc_file','txc_count','db_journey_id','db_count','status','txc_first10','db_first10','extras_in_db','extras_in_txc'])
        mismatches = 0
        checked = 0
        for vj_code, (fpath, txc_stops) in sorted(vj_map.items()):
            checked += 1
            db_map = db_stops_for_vj(conn, vj_code)
            if not db_map:
                w.writerow([vj_code, fpath.name, len(txc_stops), '', 0, 'no_db', ','.join(txc_stops[:10]), '', '', ''])
                mismatches += 1
                continue
            # Compare each DB journey for this vj
            for jid, db_stops in db_map.items():
                status = 'ok' if db_stops == txc_stops else 'mismatch'
                if status == 'mismatch':
                    mismatches += 1
                extras_in_db = [s for s in db_stops if s not in txc_stops]
                extras_in_txc = [s for s in txc_stops if s not in db_stops]
                w.writerow([vj_code, fpath.name, len(txc_stops), jid, len(db_stops), status, ','.join(txc_stops[:10]), ','.join(db_stops[:10]), ','.join(extras_in_db[:10]), ','.join(extras_in_txc[:10])])

    conn.close()
    print(f'Checked {checked} VehicleJourneys; mismatches: {mismatches}. Results saved to {out_csv}')

if __name__ == '__main__':
    main()
