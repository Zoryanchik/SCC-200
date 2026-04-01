"""Search and geocoding helpers for API endpoints."""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List
from urllib.parse import urlencode

# Nominatim rate-limiting: ensure we do at most 1 request per second.
_NOMINATIM_LOCK = threading.Lock()
_NOMINATIM_LAST_CALL = 0.0
_NOMINATIM_MIN_INTERVAL = 1.0

# Known Lancashire place names, areas, and common POIs used for fuzzy
# correction when the user makes a typo. Nominatim has no built-in
# fuzzy matching, so we correct the query first using difflib.
_LANCASHIRE_PLACES: List[str] = [
    "Lancaster", "Preston", "Blackpool", "Blackburn", "Burnley",
    "Accrington", "Morecambe", "Fleetwood", "Lytham", "Clitheroe",
    "Chorley", "Leyland", "Ormskirk", "Skelmersdale", "Colne",
    "Nelson", "Darwen", "Rawtenstall", "Bacup", "Haslingden",
    "Carnforth", "Garstang", "Poulton-le-Fylde", "Thornton-Cleveleys",
    "Cleveleys", "Kirkham", "Longridge", "Bamber Bridge", "Fulwood",
    "Ingleton", "Heysham", "Silverdale", "Bolton-le-Sands",
    "Galgate", "Cockerham", "Knott End", "Whalley", "Ribchester",
    "Oswaldtwistle", "Great Harwood", "Rishton", "Clayton-le-Moors",
    "Padiham", "Brierfield", "Barnoldswick", "Earby",
    "Penwortham", "Lostock Hall", "Walton-le-Dale", "Longton",
    "Freckleton", "Warton", "Wesham",
    "Lancaster University", "UCLan", "Edge Hill University",
    "Lancaster Bus Station", "Preston Bus Station",
    "Blackpool North", "Blackpool South", "Blackpool Pleasure Beach",
    "Lancaster Railway Station", "Preston Railway Station",
    "Morecambe Railway Station", "Carnforth Railway Station",
    "Blackpool Tower", "Blackpool Zoo", "Williamson Park",
    "Beacon Fell", "Pendle Hill", "Forest of Bowland",
    "Ribble Valley", "Lune Valley", "Trough of Bowland",
    "Ashton Memorial", "Lancaster Castle", "Lancaster Priory",
    "Morecambe Bay", "Happy Mount Park", "Stanley Park",
    "Sainsbury", "Sainsburys", "Sainsbury's",
    "Tesco", "Asda", "Aldi", "Lidl", "Morrisons", "Morrison",
    "Nando's", "Nandos", "McDonald's", "McDonalds",
    "Costa", "Starbucks", "Greggs",
    "Hospital", "Royal Lancaster Infirmary", "Royal Preston Hospital",
    "Blackpool Victoria Hospital",
    "Arndale", "Fishergate", "St George's Shopping Centre",
    "Houndshill", "Market", "Library", "Cinema", "Park", "Beach",
]
_LANCASHIRE_PLACES_LOWER: List[str] = [p.lower() for p in _LANCASHIRE_PLACES]


def _fuzzy_correct_query(query: str, threshold: float = 0.6) -> str:
    """Return the best fuzzy match from the known-places list."""
    from difflib import get_close_matches

    q = query.strip()
    q_lower = q.lower()
    matches = get_close_matches(q_lower, _LANCASHIRE_PLACES_LOWER, n=1, cutoff=threshold)
    if matches:
        idx = _LANCASHIRE_PLACES_LOWER.index(matches[0])
        return _LANCASHIRE_PLACES[idx]

    words = q.split()
    corrected_words = []
    changed = False
    for word in words:
        if len(word) < 4:
            corrected_words.append(word)
            continue
        word_matches = get_close_matches(
            word.lower(), _LANCASHIRE_PLACES_LOWER, n=1, cutoff=threshold
        )
        if word_matches:
            idx = _LANCASHIRE_PLACES_LOWER.index(word_matches[0])
            corrected_words.append(_LANCASHIRE_PLACES[idx])
            changed = True
        else:
            corrected_words.append(word)
    if changed:
        return " ".join(corrected_words)
    return q


def looks_like_street(s: str) -> bool:
    """Heuristic: return True if the suffix looks like a street/address."""
    if not s:
        return False
    s = s.strip().lower()
    import re

    if re.search(r"\d", s):
        return True

    street_tokens = {
        "street", "st", "road", "rd", "lane", "ln", "avenue", "ave", "drive", "dr",
        "way", "court", "ct", "crescent", "close", "terrace", "gardens", "place",
        "square", "hill", "park", "boulevard", "blvd", "grove", "row", "alley", "isle",
        "mount", "mountain", "walk", "end",
    }
    words = re.split(r"[\s,]+", s)
    for word in words:
        if word in street_tokens or word.rstrip(".") in street_tokens:
            return True

    if 0 < len(s) <= 3:
        return True

    return False


def geocode_locations(query: str, limit: int = 5, county: str = "Lancashire") -> List[Dict[str, Any]]:
    """Query Nominatim and return candidates filtered to Lancashire."""
    if not query or limit <= 0:
        return []

    main_q = query
    town_hint = None
    if "," in query:
        first, tail = query.split(",", 1)
        first = first.strip()
        tail = tail.strip()
        if tail and not looks_like_street(tail):
            main_q = first
            town_hint = tail

    corrected = _fuzzy_correct_query(main_q)
    town_corrected = _fuzzy_correct_query(town_hint) if town_hint else None

    effective_query = corrected
    if town_corrected:
        effective_query = f"{corrected} {town_corrected}"
    if county:
        lower_eff = effective_query.lower()
        if county.lower() not in lower_eff and (not town_corrected or county.lower() not in town_corrected.lower()):
            effective_query = f"{effective_query} {county}"

    nominatim_limit = limit * 3 if county else limit
    params = {
        "format": "json",
        "q": effective_query,
        "limit": str(nominatim_limit),
        "addressdetails": "1",
    }
    if county:
        params["countrycodes"] = "gb"

    url = f"https://nominatim.openstreetmap.org/search?{urlencode(params)}"
    import requests

    global _NOMINATIM_LAST_CALL
    with _NOMINATIM_LOCK:
        now = time.monotonic()
        elapsed = now - _NOMINATIM_LAST_CALL
        wait = _NOMINATIM_MIN_INTERVAL - elapsed
        if wait > 0:
            time.sleep(wait)
        _NOMINATIM_LAST_CALL = time.monotonic()
        resp = requests.get(url, headers={"User-Agent": "transport-backend/1.0"}, timeout=5)
    resp.raise_for_status()
    payload = resp.json()

    results = []
    for item in payload:
        try:
            lat = float(item.get("lat"))
            lon = float(item.get("lon"))
        except (TypeError, ValueError):
            continue

        if county:
            addr = item.get("address", {}) or {}
            display = (item.get("display_name") or "").lower()
            addr_combined = " ".join(str(v) for v in addr.values() if v).lower()
            county_lc = county.lower()
            if county_lc not in addr_combined and county_lc not in display:
                continue

        name = item.get("display_name") or item.get("name") or query
        results.append(
            {
                "id": f"loc:{len(results)}",
                "name": name,
                "lat": lat,
                "lon": lon,
                "atco_code": None,
                "type": "location",
            }
        )
        if len(results) >= limit:
            break
    return results
