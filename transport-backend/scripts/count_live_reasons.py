#!/usr/bin/env python3
"""Fetch /bus/live/all and print match_reason counts, logged_journey_id totals,
and aggregate per-vehicle reject_reasons (when BUS_LIVE_PROVENANCE=1).

Usage: run backend with BUS_LIVE_PROVENANCE=1 and then run this script.
"""
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
reject_c = Counter()
no_reject = 0
for e in data:
    c[e.get('match_reason')] += 1
    if e.get('logged_journey_id'):
        logged += 1
    rr = e.get('reject_reasons')
    if rr and isinstance(rr, (list, tuple)):
        for r in rr:
            reject_c[r] += 1
    else:
        no_reject += 1

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

# Print aggregated reject_reasons
print()
print("reject_reasons_counts=", dict(reject_c))
print("records_without_reject_reasons=", no_reject)
print()
print("reject_reasons_percentages:")
for k, v in sorted(reject_c.items(), key=lambda x: -x[1]):
    pct = (v / total * 100) if total else 0.0
    print(f"  {k}: {v} ({pct:.1f}%)")
