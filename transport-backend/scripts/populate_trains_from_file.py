#!/usr/bin/env python3
"""Populate train_journey_cache from a local TOC gzip file.

Usage:
  python3 populate_trains_from_file.py [--file PATH] [--date YYYY-MM-DD]

Defaults:
  --file: ~/Downloads/toc-full.gz
  --date: today's date (service date applied to schedule filtering)

This script parses the gzipped line-delimited JSON schedule using
TrainLoader.load_schedule_file() and then persists the resulting
TrainData into the train_journey_cache table for the provided date
using TrainLoader._save_cached_traindata().

Note: this is a developer convenience tool. It expects the local
Postgres train DSN to be set via TRAIN_DB_DSN or to fall back to the
project default (see transport-backend/main.py).
"""
import sys
import os
import argparse
from datetime import date

# Ensure imports resolve to the transport-backend package
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from train_loader import TrainLoader
import main as _main


def main():
    p = argparse.ArgumentParser(description='Populate train cache from a local TOC gzip file')
    p.add_argument('--file', '-f', default=os.path.expanduser('~/Downloads/toc-full.gz'))
    p.add_argument('--date', '-d', default=date.today().isoformat())
    args = p.parse_args()

    file_path = os.path.abspath(os.path.expanduser(args.file))
    service_date = args.date

    if not os.path.exists(file_path):
        print(f"File not found: {file_path}")
        sys.exit(2)

    print(f"Using TRAIN DB DSN: {_main.TRAIN_DB_PATH}")
    print(f"Using ATCO/WALK DB DSN: {_main.WALK_DB_PATH}")
    print(f"Loading schedule bytes from: {file_path}")

    with open(file_path, 'rb') as fh:
        data = fh.read()

    tl = TrainLoader(_main.TRAIN_DB_PATH, atco_db_path=_main.WALK_DB_PATH)
    # Ensure DB schema exists
    try:
        tl.create_schema()
    except Exception as exc:
        print(f"Failed to create train cache schema: {exc}")

    print(f"Parsing schedule and building TrainData for service_date={service_date} (this may take a while)")
    train_data = tl.load_schedule_file(data, target_date=service_date)

    rcount = len(train_data.route_stops) if hasattr(train_data, 'route_stops') else 0
    jcount = sum(1 for row in getattr(train_data, 'journey_times', []) if row)
    print(f"Parsed TrainData: routes={rcount}, journeys(with times)={jcount}")

    print(f"Saving cached train journeys into DB for {service_date}...")
    tl._save_cached_traindata(service_date, train_data)
    print("Done.")


if __name__ == '__main__':
    main()
