import os
from typing import Any, Dict

import psycopg2
from psycopg2.extras import RealDictCursor


def get_db_config() -> Dict[str, Any]:
    return {
        "host": os.getenv("DB_HOST", "localhost"),
        "port": int(os.getenv("DB_PORT", "5432")),
        "dbname": os.getenv("DB_NAME", "transport"),
        "user": os.getenv("DB_USER", "postgres"),
        "password": os.getenv("DB_PASSWORD", ""),
    }


def get_connection():
    cfg = get_db_config()
    return psycopg2.connect(**cfg)


def get_dict_cursor(conn):
    return conn.cursor(cursor_factory=RealDictCursor)
