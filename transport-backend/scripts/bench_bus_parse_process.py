import os, time, zipfile, io, tempfile, ssl, urllib.request
from bus_loader import BusLoader
from concurrent.futures import ProcessPoolExecutor, as_completed

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
data = urllib.request.urlopen("https://transport.scc.lancs.ac.uk/timetable/dataset/18047/download/", context=ctx).read()

with tempfile.TemporaryDirectory() as tmp_dir:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        zf.extractall(tmp_dir)

    loader = BusLoader("postgresql://pguser:pgpass@127.0.0.1:5011/transport")
    files = sorted(f for f in os.listdir(tmp_dir) if f.lower().endswith('.xml'))
    
    t0 = time.time()
    with ProcessPoolExecutor() as ex:
        futures = { ex.submit(loader._parse_file, os.path.join(tmp_dir, fname)): fname for fname in files }
        for fut in as_completed(futures):
            fut.result()
    print(f"Time (ProcessPoolExecutor): {time.time() - t0:.2f}s")
