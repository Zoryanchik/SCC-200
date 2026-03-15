from atco_loader import AtcoLoader
import bisect

al = AtcoLoader("postgresql://pguser:pgpass@127.0.0.1:5011/transport")
coords = al.get_all_stop_coords()
atco_list = list(coords.keys())
lat_list = [coords[a][0] for a in atco_list]
lon_list = [coords[a][1] for a in atco_list]

indexed = sorted(range(len(atco_list)), key=lambda i: lat_list[i])
sorted_lats = [lat_list[i] for i in indexed]

bbox_margin = 0.012
sizes = []
for src_idx in indexed:
    src_lat = lat_list[src_idx]
    src_lon = lon_list[src_idx]
    lo = bisect.bisect_left(sorted_lats, src_lat - bbox_margin)
    hi = bisect.bisect_right(sorted_lats, src_lat + bbox_margin)
    count = 0
    for j in range(lo, hi):
        nb_idx = indexed[j]
        if nb_idx == src_idx:
            continue
        if abs(lon_list[nb_idx] - src_lon) <= bbox_margin:
            count += 1
    sizes.append(count)

print(f"Total stops: {len(sizes)}")
print(f"Average neighbors: {sum(sizes)/len(sizes):.1f}")
print(f"Max neighbors: {max(sizes)}")
print(f"Percentiles:")
sizes.sort()
print(f" 50%: {sizes[int(len(sizes)*0.5)]}")
print(f" 90%: {sizes[int(len(sizes)*0.9)]}")
print(f" 99%: {sizes[int(len(sizes)*0.99)]}")
