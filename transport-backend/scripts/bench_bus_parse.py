import os, time, zipfile, io, tempfile, ssl, urllib.request
from bus_loader import BusLoader
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
data = urllib.request.urlopen("https://transport.scc.lancs.ac.uk/timetable/dataset/18047/download/", context=ctx).read()

with tempfile.TemporaryDirectory() as tmp_dir:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        zf.extractall(tmp_dir)

    loader = BusLoader("postgresql://pguser:pgpass@127.0.0.1:5011/transport")
    # Make populate a no-op so we only bench parsing + thread overhead
    loader.populate = lambda *args, **kwargs: None
    
    t0 = time.time()
    loader.load_folder(tmp_dir, tag="TEST")
    print(f"Time: {time.time() - t0:.2f}s")
