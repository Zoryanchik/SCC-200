import time, requests, urllib.request
from concurrent.futures import ThreadPoolExecutor

urls = ["http://localhost:5012/table/v1/foot/-2.798,54.046;-2.8,54.05?sources=0&annotations=duration"] * 1000

t0 = time.time()
with ThreadPoolExecutor(50) as ex:
    def fetch(u):
        with urllib.request.urlopen(u) as r:
            return r.read()
    list(ex.map(fetch, urls))
print(f"urllib: {time.time() - t0:.2f}s")

session = requests.Session()
adapter = requests.adapters.HTTPAdapter(pool_connections=50, pool_maxsize=50)
session.mount('http://', adapter)

t1 = time.time()
with ThreadPoolExecutor(50) as ex:
    def fetch2(u):
        return session.get(u).content
    list(ex.map(fetch2, urls))
print(f"requests: {time.time() - t1:.2f}s")
