from typing import Dict, Optional

from walking_utils import find_nearby_stops, resolve_location


class Walking:
    def __init__(self, stop_mapper: Optional[object] = None) -> None:
        self.stop_mapper = stop_mapper
        self.stop_index = getattr(stop_mapper, "code_to_int", None)

    def _map_codes(self, stops: Dict[str, float]) -> Dict[int, float] | Dict[str, float]:
        if not self.stop_mapper:
            return stops
        mapped: Dict[int, float] = {}
        for code, time_sec in stops.items():
            if self.stop_index is not None:
                if code in self.stop_index:
                    mapped[self.stop_index[code]] = time_sec
            else:
                mapped[self.stop_mapper.get_int(code)] = time_sec
        return mapped

    # return dict of walkable stops and walking time in seconds from start_point
    # uses a simple radius-based lookup over NaPTAN coordinates
    def inter_walk(self, start_point: str) -> Dict[int, float] | Dict[str, float]:
        lat, lon = resolve_location(start_point)
        return self._map_codes(find_nearby_stops(lat, lon, radius_m=300))

    # given a location (ATCO code), return reachable stops by walking and time
    def reachable_stops(self, location: str) -> Dict[int, float] | Dict[str, float]:
        lat, lon = resolve_location(location)
        return self._map_codes(find_nearby_stops(lat, lon, radius_m=1200))
    