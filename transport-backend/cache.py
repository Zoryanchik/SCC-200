from cachetools import TTLCache


# Cache for stop coordinates and planner data
STOP_CACHE = TTLCache(maxsize=1, ttl=300)
PLANNER_CACHE = TTLCache(maxsize=1, ttl=300)
