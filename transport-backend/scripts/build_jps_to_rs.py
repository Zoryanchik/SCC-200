#!/usr/bin/env python3
"""
Build a strict JPS -> RS mapping by parsing TransXChange XML files from dataset download URLs.

Rule: For each JourneyPattern in a TXC file, find its RouteRef and the corresponding
Route element's ordered RouteSectionRefs. Map JourneyPatternSectionRefs (JPS ids)
to RouteSectionRefs (RS ids) by position (one-to-one) — this follows the TXC structure
and your specified strict mapping rule.

The script writes mappings into a new table `jps_to_rs_map(legacy_id, canonical_id, confidence)`
and then inserts aliases into `route_id_aliases` for any canonical ids that exist in
`bus_route_section_tracks`.

Usage: PYTHONPATH=. python3 scripts/build_jps_to_rs.py

"""
import os
import io
import ssl
import zipfile
import tempfile
import urllib.request
import psycopg
from lxml import etree as ET
from main import BUS_DB_PATH

SQL_CREATE = '''
CREATE TABLE IF NOT EXISTS jps_to_rs_map (
    legacy_id TEXT PRIMARY KEY,
    canonical_id TEXT NOT NULL,
    confidence TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);
'''

def download_and_extract(url):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    resp = urllib.request.urlopen(url, context=ctx, timeout=180)
    data = resp.read()
    tmp = tempfile.TemporaryDirectory()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        zf.extractall(tmp.name)
    return tmp


def parse_file(path):
    try:
        tree = ET.parse(path)
    except Exception:
        return None
    root = tree.getroot()
    ns = '{http://www.transxchange.org.uk/}'
    # service code
    svc = root.find(f'{ns}Services/{ns}Service')
    service_code = ''
    if svc is not None:
        service_code = (svc.findtext(f'{ns}ServiceCode') or '').strip()
        if not service_code:
            so_el = root.find(f'{ns}ServiceOrganisations/{ns}ServiceOrganisation')
            if so_el is not None:
                service_code = (so_el.findtext(f'{ns}OrganisationCode') or '').strip()
    # file prefix
    file_provided_name = root.attrib.get('FileName') or ''
    return root, ns, service_code, file_provided_name


def build_mappings_from_root(root, ns, service_code, file_prefix, fname):
    mappings = []
    # compute file prefix used by loader: use FileName or filename stem
    if file_prefix:
        file_prefix_use = os.path.splitext(os.path.basename(file_prefix))[0]
    else:
        file_prefix_use = os.path.splitext(os.path.basename(fname))[0]
    file_prefix_use = file_prefix_use.replace(' ', '_')

    # collect Routes -> ordered RouteSectionRefs
    routes = {}
    routes_el = root.find(f'{ns}Routes')
    if routes_el is not None:
        for route in routes_el.findall(f'{ns}Route'):
            rid = route.attrib.get('id') or ''
            refs = [r.text for r in route.findall(f'.//{ns}RouteSectionRef') if r.text]
            routes[rid] = refs

    # collect JourneyPatterns
    std = None
    services = root.find(f'{ns}Services')
    if services is not None:
        svc = services.find(f'{ns}Service')
        if svc is not None:
            std = svc.find(f'{ns}StandardService')
    if std is None:
        return mappings

    for jp in std.findall(f'{ns}JourneyPattern'):
        jp_id = jp.attrib.get('id')
        route_ref = jp.findtext(f'{ns}RouteRef')
        jps_refs = [s.text for s in jp.findall(f'{ns}JourneyPatternSectionRefs') if s.text]
        if not jp_id or not route_ref or not jps_refs:
            continue
        route_section_refs = routes.get(route_ref) or []
        # If lengths differ, attempt to map by trimming or skip — strict mapping requires equal length
        if len(jps_refs) != len(route_section_refs) or not route_section_refs:
            # skip non-strict cases
            continue
        for jps, rs in zip(jps_refs, route_section_refs):
            # build legacy and canonical route_ids as the loader would
            if service_code:
                legacy_r = f"{file_prefix_use}::{service_code}:{jps}"
                canonical_r = f"{file_prefix_use}::{service_code}:{rs}"
            else:
                legacy_r = f"{file_prefix_use}::{jps}"
                canonical_r = f"{file_prefix_use}::{rs}"
            mappings.append((legacy_r, canonical_r))
    return mappings


def main():
    print('Connecting to', BUS_DB_PATH)
    conn = psycopg.connect(BUS_DB_PATH)
    cur = conn.cursor()
    print('Ensuring jps_to_rs_map table exists...')
    cur.execute(SQL_CREATE)
    conn.commit()

    # Gather dataset download URLs from bus_dataset_meta
    cur.execute("SELECT download_url FROM bus_dataset_meta")
    urls = [r[0] for r in cur.fetchall()]
    print('Found', len(urls), 'dataset URLs')

    total_mappings = 0
    for url in urls:
        print('Processing', url)
        try:
            tmp = download_and_extract(url)
        except Exception as e:
            print('  Failed to download/extract:', e)
            continue
        try:
            for root_dir, _, files in os.walk(tmp.name):
                for fname in files:
                    if not fname.lower().endswith('.xml'):
                        continue
                    full = os.path.join(root_dir, fname)
                    parsed = parse_file(full)
                    if parsed is None:
                        continue
                    root, ns, service_code, file_provided_name = parsed
                    mappings = build_mappings_from_root(root, ns, service_code, file_provided_name, fname)
                    for legacy, canonical in mappings:
                        try:
                            cur.execute("INSERT INTO jps_to_rs_map (legacy_id, canonical_id, confidence) VALUES (%s, %s, %s) ON CONFLICT (legacy_id) DO NOTHING",
                                        (legacy, canonical, 'txc_strict'))
                        except Exception as e:
                            print('  insert error', e)
                    conn.commit()
                    total_mappings += len(mappings)
        finally:
            tmp.cleanup()

    print('Total strict mappings inserted into jps_to_rs_map:', total_mappings)

    # Populate route_id_aliases from jps_to_rs_map where canonical exists in section tracks
    print('Populating route_id_aliases from jps_to_rs_map (strict mappings)...')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS route_id_aliases (
            legacy_id TEXT PRIMARY KEY,
            canonical_id TEXT NOT NULL,
            confidence TEXT,
            created_at TIMESTAMPTZ DEFAULT now()
        );
    ''')
    cur.execute("CREATE INDEX IF NOT EXISTS idx_route_id_aliases_canon ON route_id_aliases(canonical_id);")
    conn.commit()

    cur.execute("INSERT INTO route_id_aliases (legacy_id, canonical_id, confidence) SELECT j.legacy_id, j.canonical_id, j.confidence FROM jps_to_rs_map j JOIN bus_route_section_tracks s ON s.route_id = j.canonical_id ON CONFLICT (legacy_id) DO NOTHING")
    inserted = cur.rowcount
    conn.commit()
    print('Inserted alias rows (strict TXC):', inserted)
    conn.close()


if __name__ == '__main__':
    main()
