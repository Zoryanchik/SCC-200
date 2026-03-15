"""Utilities for resolving file-prefixed route_id values in the DB.

Many route IDs in the loader and stored rows are namespaced by the
source filename using the pattern "<file_prefix>::<route_id>". Tools and
scripts that accept an un-prefixed route id should resolve the stored
prefixed candidate before running SQL lookups.

This module exposes resolve_prefixed_route_id(conn, route_id,
prefer_section=True) which returns a DB candidate (possibly the same as
route_id) to use in WHERE route_id = %s queries.
"""
from typing import Optional
import logging

logger = logging.getLogger(__name__)


def resolve_prefixed_route_id(conn, route_id: str, prefer_section: bool = True) -> str:
    """Return a prefixed route_id candidate from the DB if available.

    - If the provided route_id already contains '::' it is returned as-is.
    - Otherwise the DB is searched for a matching stored id that ends
      with the provided route_id (pattern '%%::<route_id>'). If
      prefer_section is True, the search checks
      bus_route_section_tracks first (more specific), then
      bus_route_tracks.

    If no candidate is found, the original route_id is returned.
    """
    logger.debug("resolve_prefixed_route_id: resolving route_id=%s (prefer_section=%s)", route_id, prefer_section)
    if '::' in route_id:
        logger.debug("resolve_prefixed_route_id: route_id already prefixed, returning as-is: %s", route_id)
        return route_id

    cur = conn.cursor()

    pattern = f"%::{route_id}"
    logger.debug("resolve_prefixed_route_id: using LIKE pattern %s", pattern)

    if prefer_section:
        try:
            cur.execute("SELECT route_id FROM bus_route_section_tracks WHERE route_id LIKE %s LIMIT 1", (pattern,))
            r = cur.fetchone()
            if r:
                candidate = r[0]
                logger.debug("resolve_prefixed_route_id: matched section_tracks candidate %s for %s", candidate, route_id)
                return candidate
        except Exception:
            # Ignore DB errors here; fall through to next check
            logger.exception("Error checking bus_route_section_tracks for %s", route_id)

    try:
        cur.execute("SELECT route_id FROM bus_route_tracks WHERE route_id LIKE %s LIMIT 1", (pattern,))
        r = cur.fetchone()
        if r:
            candidate = r[0]
            logger.debug("resolve_prefixed_route_id: matched route_tracks candidate %s for %s", candidate, route_id)
            return candidate
    except Exception:
        logger.exception("Error checking bus_route_tracks for %s", route_id)

    # No candidate found; return original
    logger.debug("resolve_prefixed_route_id: no prefixed candidate for %s, returning original", route_id)
    return route_id
