import time
from main import initialize_base

t0 = time.time()
initialize_base()
t1 = time.time()
print(f"TOTAL TIME: {t1-t0:.2f}s")
