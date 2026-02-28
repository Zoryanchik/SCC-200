import psycopg
from train_data import TrainData


class TrainLoader:
    """Minimal TrainLoader stub to initialize a separate train DB.

    This is intentionally small: it creates a train metadata table so the
    main initialization can manage train storage in parallel with bus and
    walking setup. A fuller TrainLoader (GTFS/NTC ingestion) can be added
    later.
    """
    def __init__(self, db_path):
        self.db_path = db_path

    def ensure_db(self):
        # Ensure a connection can be opened to the Postgres DSN
        conn = psycopg.connect(self.db_path)
        conn.close()

    def create_schema(self):
        # Create the minimal schema in Postgres
        conn = psycopg.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            '''CREATE TABLE IF NOT EXISTS train_dataset_meta (
                source_url   TEXT PRIMARY KEY,
                download_url TEXT NOT NULL,
                modified     TEXT NOT NULL
            );'''
        )
        conn.commit()
        conn.close()

    def load_traindata_for_date(self, date_str):
        """Return a minimal TrainData for the requested date.

        This is a lightweight placeholder so train loading can run in
        parallel with bus loading. A fuller implementation (GTFS/NTC)
        can replace this later.
        """
        # For now return an empty TrainData container; callers expect
        # a TrainData-like object (or None). Returning an empty
        # TrainData keeps the downstream code uniform and allows
        # concurrent loading with buses.
        return TrainData(num_routes=0, num_journeys=0, num_stops=0)
