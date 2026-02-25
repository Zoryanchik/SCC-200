"""Station classification algorithm.

Computes a classification for every stop in a MergedData network based
on three metrics:

* **degree**       – number of distinct routes serving the stop
* **frequency**    – total daily journeys (departures) through the stop
* **interchange**  – number of distinct service line-names at the stop

Classifications (highest → lowest):

| Class          | Rule (all conditions must hold)                |
|----------------|------------------------------------------------|
| hub            | degree ≥ D_HUB **and** frequency ≥ F_HUB      |
| interchange    | interchange ≥ I_INTER **or** degree ≥ D_INTER  |
| local          | frequency ≥ F_LOCAL                            |
| request_stop   | everything else                                |

The default thresholds are tuned for North-West England bus/rail
networks; callers may override them via keyword arguments.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# ── default thresholds ───────────────────────────────────────────────
DEFAULT_THRESHOLDS = {
    "degree_hub": 5,        # min routes for hub
    "freq_hub": 100,        # min daily departures for hub
    "interchange_inter": 3, # min distinct lines for interchange
    "degree_inter": 4,      # alternative: min routes for interchange
    "freq_local": 10,       # min daily departures for local
}


def compute_stop_metrics(merged: Any) -> List[Dict[str, Any]]:
    """Return per-stop metrics from a MergedData instance.

    Each entry is a dict with keys:
        stop_index, name, degree, frequency, interchange, lines
    """
    total_stops = len(merged.stop_to_routes)
    route_meta = getattr(merged, "route_metadata", [])

    metrics: list[dict[str, Any]] = []
    for stop in range(total_stops):
        routes = merged.stop_to_routes[stop]
        degree = len(routes)

        # Frequency: count departures across all routes at this stop
        frequency = 0
        for rsd in merged.route_stop_departures:
            deps = rsd.get(stop)
            if deps:
                frequency += len(deps)

        # Interchange: distinct line names from route_metadata
        lines: set[str] = set()
        for r_idx in routes:
            if r_idx < len(route_meta) and route_meta[r_idx]:
                ln = route_meta[r_idx].get("line_name")
                if ln:
                    lines.add(ln)

        name = ""
        if stop < len(merged.stop_metadata):
            name = merged.stop_metadata[stop] or ""

        metrics.append({
            "stop_index": stop,
            "name": name,
            "degree": degree,
            "frequency": frequency,
            "interchange": len(lines),
            "lines": sorted(lines),
        })

    return metrics


def classify_stop(metric: Dict[str, Any], **thresholds) -> str:
    """Return the classification string for a single stop metric dict.

    Accepts optional keyword overrides for any threshold in
    DEFAULT_THRESHOLDS.
    """
    t = {**DEFAULT_THRESHOLDS, **thresholds}

    degree = metric["degree"]
    freq = metric["frequency"]
    inter = metric["interchange"]

    if degree >= t["degree_hub"] and freq >= t["freq_hub"]:
        return "hub"
    if inter >= t["interchange_inter"] or degree >= t["degree_inter"]:
        return "interchange"
    if freq >= t["freq_local"]:
        return "local"
    return "request_stop"


def classify_all(
    merged: Any,
    *,
    thresholds: Optional[Dict[str, int]] = None,
) -> List[Dict[str, Any]]:
    """Compute metrics and classification for every stop.

    Returns a list of dicts, each containing the metric fields plus
    a ``classification`` key.
    """
    t = thresholds or {}
    metrics = compute_stop_metrics(merged)
    for m in metrics:
        m["classification"] = classify_stop(m, **t)
    return metrics


def classify_to_lookup(
    merged: Any,
    *,
    thresholds: Optional[Dict[str, int]] = None,
) -> Dict[int, str]:
    """Return a fast {stop_index: classification} mapping."""
    results = classify_all(merged, thresholds=thresholds)
    return {r["stop_index"]: r["classification"] for r in results}
