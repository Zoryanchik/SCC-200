"""Force-download and load all bus datasets, upserting dataset stop coords into atop_coords.

Run from repo root:

    export PYTHONPATH=transport-backend
    python3 transport-backend/scripts/populate_atop_coords.py

This script is idempotent and safe to run multiple times.
"""

from main import BUS_DB_PATH, WALK_DB_PATH
from bus_loader import BusLoader
import sys
from atco_loader import AtcoLoader

def main():
    loader = BusLoader(BUS_DB_PATH, walking_db_path=WALK_DB_PATH)
    loader.ensure_db()
    # Ensure the canonical table exists first
    try:
        AtcoLoader(BUS_DB_PATH).create_schema()
        print("Ensured atop_coords schema exists (or attempted to create it)")
    except Exception as e:
        print("Warning: create_schema() failed:", e)
    loader.create_schema()
    print("Fetching dataset list...")
    datasets = loader._fetch_dataset_info()
    if not datasets:
        print("No datasets found.")
        return 0
    print(f"Found {len(datasets)} datasets; downloading & loading each (may take a while)...")
    failed = []
    for ds in datasets:
        tag = ds.get('source_url', '').rstrip('/').split('/')[-1] or 'dataset'
        url = ds.get('download_url')
        print(f"- Loading {tag} from {url}")
        try:
            loader.download_and_load(url, tag=tag)
        except Exception as e:
            print(f"  Failed to load {tag}: {e}")
            import traceback
            traceback.print_exc()
            failed.append((tag, e))
    if failed:
        print(f"Completed with {len(failed)} failures")
        for t, e in failed:
            print(f" - {t}: {e}")
        return 2
    print("All datasets loaded (or up-to-date). atop_coords should be populated.")
    return 0

if __name__ == '__main__':
    sys.exit(main())
