from walking_loader import WalkingLoader
from atco_loader import AtcoLoader
import time, requests, bisect, math

al = AtcoLoader("postgresql://pguser:pgpass@127.0.0.1:5011/transport")
coords = al.get_all_stop_coords()
atco_list = list(coords.keys())
lat_list = [coords[a][0] for a in atco_list]
lon_list = [coords[a][1] for a in atco_list]

indexed = sorted(range(len(atco_list)), key=lambda i: lat_list[i])
sorted_lats = [lat_list[i] for i in indexed]

bbox_margin = 0.012
max_walk_seconds = 600
max_straight_m = max_walk_seconds * 2.0

def fast_dist_m(lat1, lon1, lat2, lon2):
    r_lat1 = math.radians(lat1)
    x = math.radians(lon2 - lon1) * math.cos(r_lat1)
    y = math.radians(lat2 - lat1)
    return 6371000.0 * math.sqrt(x*x + y*y)

def query_one_stop(src_idx):
    src_atco = atco_list[src_idx]
    src_lat = lat_list[src_idx]
    src_lon = lon_list[src_idx]
    lo = bisect.bisect_left(sorted_lats, src_lat - bbox_margin)
    hi = bisect.bisect_right(sorted_lats, src_lat + bbox_margin)
    neighbors = []
    for j in range(lo, hi):
        nb_idx = indexed[j]
        if nb_idx <= src_idx:
            continue
        if abs(lon_list[nb_idx] - src_lon) <= bbox_margin:
            dist = fast_dist_m(src_lat, src_lon, lat_list[nb_idx], lon_list[nb_idx])
            if dist <= max_straight_m:
                neighbors.append(nb_idx)
    if not neighbors:
        return []
    all_indices = [src_idx] + neighbors
    coord_str = ";".join(f"{lon_list[i]},{lat_list[i]}" for i in all_indices)
    return coord_str

t0 = time.time()
for src_idx in indexed:
    query_one_stop(src_idx)
print(f"Pure python loop time: {time.time() - t0:.2f}s")
