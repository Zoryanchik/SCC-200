import os
import psycopg2


def main() -> None:
    conn = psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=int(os.getenv("DB_PORT", "5432")),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )
    cur = conn.cursor()
    cur.execute(
        "SELECT column_name,data_type FROM information_schema.columns WHERE table_name='atco_stops' ORDER BY ordinal_position"
    )
    print("atco_stops columns:", cur.fetchall())

    for table in ["atco_stops", "routes", "journeys", "journey_times", "route_stops"]:
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        count = cur.fetchone()[0]
        print(f"{table}: {count}")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
