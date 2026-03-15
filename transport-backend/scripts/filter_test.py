import math
def fast_dist_m(lat1, lon1, lat2, lon2):
    r_lat1 = math.radians(lat1)
    x = math.radians(lon2 - lon1) * math.cos(r_lat1)
    y = math.radians(lat2 - lat1)
    return 6371000.0 * math.sqrt(x*x + y*y)
print(fast_dist_m(54.046, -2.798, 54.046, -2.798+0.012))
print(fast_dist_m(54.046, -2.798, 54.046+0.012, -2.798))
