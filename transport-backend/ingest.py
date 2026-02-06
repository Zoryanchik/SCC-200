import argparse
import os

from atco import Atco
from ingest_txc import ingest_txc_dir
from schema import ensure_schema


def main() -> None:
    p = argparse.ArgumentParser(description="Ingest NaPTAN and TXC data into Postgres")
    p.add_argument("--naptan", action="store_true", help="Download and load NaPTAN stops")
    p.add_argument("--txc-dir", help="Directory containing TXC XML files")
    args = p.parse_args()

    ensure_schema()

    if args.naptan:
        atco = Atco()
        atco.load_naptan()

    if args.txc_dir:
        if not os.path.isdir(args.txc_dir):
            raise SystemExit(f"TXC directory not found: {args.txc_dir}")
        count = ingest_txc_dir(args.txc_dir)
        print(f"Ingested {count} journeys from {args.txc_dir}")


if __name__ == "__main__":
    main()
