import urllib.request, json, time

coord_str = "-2.798,54.046;-2.8,54.05;-2.801,54.051;-2.802,54.052"
# 1 source, 3 dests
url1 = f"http://localhost:5012/table/v1/foot/{coord_str}?sources=0&annotations=duration"
# 2 sources, 2 dests
url2 = f"http://localhost:5012/table/v1/foot/{coord_str}?sources=0;1&annotations=duration"

t0 = time.time()
for _ in range(100):
    with urllib.request.urlopen(url1) as r:
        r.read()
print(f"1 source: {time.time() - t0:.2f}s")

t0 = time.time()
for _ in range(100):
    with urllib.request.urlopen(url2) as r:
        r.read()
print(f"2 sources: {time.time() - t0:.2f}s")
