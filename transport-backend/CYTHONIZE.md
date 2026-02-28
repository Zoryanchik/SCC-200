# Building the Cythonized RAPTOR router

This project includes a Cython copy of the RAPTOR router implementation: `raptor_router.pyx`.

To build the extension in-place (produces `raptor_router*.so`):

```bash
cd transport-backend
python3 -m pip install -r requirements.txt
python3 setup_raptor.py build_ext --inplace
```

Notes:
- We keep the pure-Python `raptor_router.py` as the source of truth. The `.pyx` file is a drop-in copy used for faster builds. If you make changes to `raptor_router.py`, copy them to `raptor_router.pyx` (or regenerate via a small script) before building.
- The setup script uses O3 compile flags. You can adjust `extra_compile_args` in `setup_raptor.py` if needed.
