#!/usr/bin/env python3
"""Fetch /bus/live/all and print match_reason counts and logged_journey_id totals."""
import json
import urllib.request
import sys
from collections import Counter

URL = "http://localhost:5050/bus/live/all?lat=54.05&lon=-2.8&latTol=2.0&lonTol=2.0"

try:
    with urllib.request.urlopen(URL, timeout=15) as resp:
        data = json.load(resp)
except Exception as e:
    print("ERROR fetching", URL, str(e), file=sys.stderr)
    sys.exit(2)

c = Counter()
logged = 0
for e in data:
    c[e.get('match_reason')] += 1
    if e.get('logged_journey_id'):
        logged += 1

print("total_live_records=", len(data))
print("match_reason_counts=", dict(c))
print("records_with_logged_journey_id=", logged)
print()
print("sample_entries (first 3):")
print(json.dumps(data[:3], indent=2))

# Print percentages for each match_reason
print()
print("match_reason_percentages:")
total = len(data) if data else 0
for k, v in sorted(c.items(), key=lambda x: -x[1]):
    pct = (v / total * 100) if total else 0.0
    print(f"  {k}: {v} ({pct:.1f}%)")
